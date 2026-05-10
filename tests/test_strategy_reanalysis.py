from __future__ import annotations

import pytest


@pytest.mark.unit
def test_reanalysis_orchestrator_calls_quant_agent_and_revalidates_strategy():
    from tradingagents.validation.performance import ValidationConfig
    from tradingagents.validation.pre_live import build_pre_live_validation_report
    from tradingagents.validation.reanalysis import run_reanalysis_if_required

    chart = {
        "available": True,
        "currency": "KRW",
        "points": [
            {"date": "2026-01-01", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"date": "2026-01-02", "open": 100.0, "high": 103.0, "low": 99.0, "close": 101.0},
            {"date": "2026-01-03", "open": 101.0, "high": 106.0, "low": 100.0, "close": 105.0},
            {"date": "2026-01-04", "open": 105.0, "high": 107.0, "low": 101.0, "close": 102.0},
            {"date": "2026-01-05", "open": 102.0, "high": 108.0, "low": 100.0, "close": 107.0},
        ],
    }
    record = {
        "run_id": "parent-run",
        "ticker": "005930.KS",
        "trade_date": "2026-01-01",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "rating": "Hold",
        "trader_action": "Wait",
        "research_recommendation": "Hold",
        "decision_summary": "기존 전략",
        "raw_log_path": "",
        "reports": {"quant_strategy_report": "진입 80-81 KRW, 익절 90 KRW, 손절 70 KRW"},
        "strategy_spec": {
            "strategy_id": "old-spec",
            "ticker": "005930.KS",
            "trade_date": "2026-01-01",
            "entry": {"type": "price_zone", "low": 80.0, "high": 81.0},
            "take_profit": {"type": "fixed_price", "price": 90.0},
            "stop_loss": {"type": "fixed_price", "price": 70.0},
            "currency": "KRW",
        },
    }
    failing_backtest = {
        "available": True,
        "periods": {
            "전체": {
                "benchmark_return_percent": 7.0,
                "entry_touch_percent": 0.0,
                "entry_touch_days": 0,
                "sample_days": 5,
                "trades": [],
            }
        },
    }
    pre_live = build_pre_live_validation_report(
        failing_backtest,
        config=ValidationConfig(min_trades=1, min_win_rate_percent=0, min_profit_factor=0, max_drawdown_percent=100),
    )
    calls = []

    def fake_agent_runner(payload):
        calls.append(payload)
        return {
            "quant_strategy_report": "재조정: 진입 99-101 KRW, 익절 105 KRW, 손절 95 KRW",
            "strategy_spec": {
                "strategy_id": "new-spec",
                "ticker": "005930.KS",
                "trade_date": "2026-01-01",
                "entry": {"type": "price_zone", "low": 99.0, "high": 101.0},
                "take_profit": {"type": "fixed_price", "price": 105.0},
                "stop_loss": {"type": "fixed_price", "price": 95.0},
                "currency": "KRW",
            },
        }

    result = run_reanalysis_if_required(
        record,
        chart,
        pre_live,
        agent_runner=fake_agent_runner,
        validation_config=ValidationConfig(min_trades=1, min_win_rate_percent=0, min_profit_factor=0, max_drawdown_percent=100),
    )

    assert result["triggered"] is True
    assert len(calls) == 1
    assert calls[0]["agent"] == "quant_strategy_analyst"
    assert calls[0]["previous_strategy_spec"]["strategy_id"] == "old-spec"
    assert "진입 조건이 너무 좁아 시장에서 거의 실행되지 않음" in calls[0]["reanalysis_request"]["reasons"]
    assert result["status"] == "completed"
    assert result["attempts"][0]["attempt"] == 1
    assert result["attempts"][0]["agent_called"] is True
    assert result["attempts"][0]["strategy_spec"]["strategy_id"] == "new-spec"
    assert result["attempts"][0]["backtest"]["available"] is True
    assert result["attempts"][0]["pre_live_validation"]["paper_trading_candidate"] is True


@pytest.mark.unit
def test_reanalysis_orchestrator_does_not_call_agent_without_reanalysis_request():
    from tradingagents.validation.reanalysis import run_reanalysis_if_required

    called = False

    def fake_agent_runner(payload):
        nonlocal called
        called = True
        return {}

    result = run_reanalysis_if_required(
        {"run_id": "run", "ticker": "005930.KS", "trade_date": "2026-01-01"},
        {"available": True, "points": []},
        {"reanalysis_required": False},
        agent_runner=fake_agent_runner,
    )

    assert result["triggered"] is False
    assert result["status"] == "not_required"
    assert called is False
