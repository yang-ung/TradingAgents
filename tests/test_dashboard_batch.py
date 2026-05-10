from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.dashboard.batch import build_batch_config, run_batch_analysis


@pytest.mark.unit
def test_build_batch_config_defaults_to_korean_output(tmp_path):
    config = build_batch_config(tmp_path)
    assert config["output_language"] == "Korean"


@pytest.mark.unit
def test_run_batch_analysis_saves_each_result(tmp_path, sample_final_state):
    mock_graph = MagicMock()
    mock_graph.propagate.side_effect = [
        (dict(sample_final_state, company_of_interest="NVDA"), "Hold"),
        (dict(sample_final_state, company_of_interest="AAPL"), "Hold"),
    ]

    with patch("tradingagents.dashboard.batch.TradingAgentsGraph", return_value=mock_graph):
        summary = run_batch_analysis(
            ["NVDA", "AAPL"],
            "2024-05-10",
            artifact_dir=tmp_path,
        )

    assert summary["completed"] == 2
    assert summary["failed"] == []
    assert len(summary["run_ids"]) == 2
    assert (tmp_path / "dashboard.db").exists()
    assert len(list((tmp_path / "runs").glob("*.json"))) == 2


@pytest.mark.unit
def test_run_batch_analysis_collects_failures(tmp_path, sample_final_state):
    mock_graph = MagicMock()
    mock_graph.propagate.side_effect = [
        (dict(sample_final_state, company_of_interest="NVDA"), "Hold"),
        RuntimeError("boom"),
    ]

    with patch("tradingagents.dashboard.batch.TradingAgentsGraph", return_value=mock_graph):
        summary = run_batch_analysis(
            ["NVDA", "AAPL"],
            "2024-05-10",
            artifact_dir=tmp_path,
        )

    assert summary["completed"] == 1
    assert len(summary["failed"]) == 1
    assert summary["failed"][0]["ticker"] == "AAPL"


@pytest.mark.unit
def test_run_batch_analysis_normalizes_korean_tickers(tmp_path, sample_final_state):
    mock_graph = MagicMock()
    mock_graph.propagate.return_value = (dict(sample_final_state, company_of_interest="005930.KS"), "Hold")

    with patch("tradingagents.dashboard.batch.TradingAgentsGraph", return_value=mock_graph):
        summary = run_batch_analysis(
            ["005930"],
            "2024-05-10",
            artifact_dir=tmp_path,
        )

    assert summary["tickers"] == ["005930.KS"]
    mock_graph.propagate.assert_called_once_with("005930.KS", "2024-05-10")


@pytest.mark.unit
def test_run_batch_analysis_invokes_quant_reanalysis_when_validation_requests_it(tmp_path, sample_final_state):
    from tradingagents.dashboard.storage import AnalysisRepository

    mock_graph = MagicMock()
    state = dict(sample_final_state, company_of_interest="005930.KS")
    state["quant_strategy_report"] = "진입 80-81 KRW, 익절 90 KRW, 손절 70 KRW"
    mock_graph.propagate.return_value = (state, "Hold")
    calls = []

    def fake_chart_provider(ticker, trade_date, lookback_days=180):
        return {
            "available": True,
            "currency": "KRW",
            "points": [
                {"date": "2026-01-01", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
                {"date": "2026-01-02", "open": 100.0, "high": 103.0, "low": 99.0, "close": 101.0},
                {"date": "2026-01-03", "open": 101.0, "high": 106.0, "low": 100.0, "close": 105.0},
            ],
        }

    def fake_agent_runner(payload):
        calls.append(payload)
        return {
            "quant_strategy_report": "재조정: 진입 99-101 KRW, 익절 105 KRW, 손절 95 KRW",
            "strategy_spec": {
                "strategy_id": "revised-spec",
                "ticker": "005930.KS",
                "trade_date": "2024-05-10",
                "entry": {"type": "price_zone", "low": 99.0, "high": 101.0},
                "take_profit": {"type": "fixed_price", "price": 105.0},
                "stop_loss": {"type": "fixed_price", "price": 95.0},
                "currency": "KRW",
            },
        }

    with patch("tradingagents.dashboard.batch.TradingAgentsGraph", return_value=mock_graph):
        summary = run_batch_analysis(
            ["005930"],
            "2024-05-10",
            artifact_dir=tmp_path,
            reanalysis_agent_runner=fake_agent_runner,
            chart_provider=fake_chart_provider,
        )

    assert summary["completed"] == 1
    assert summary["reanalysis_completed"] >= 1
    assert len(calls) >= 1
    assert calls[0]["agent"] == "quant_strategy_analyst"
    stored_runs = AnalysisRepository(tmp_path).list_runs(include_archived=True)
    assert any(run["run_id"].endswith("-reanalysis-1") for run in stored_runs)
