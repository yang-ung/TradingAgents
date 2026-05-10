from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


_BASE_RECORD = {
    "run_id": "seed-run",
    "ticker": "005930.KS",
    "trade_date": "2026-01-01",
    "generated_at": "2026-01-01T00:00:00+00:00",
    "rating": "Hold",
    "trader_action": "Wait",
    "research_recommendation": "Hold",
    "decision_summary": "초기 전략",
    "raw_log_path": "",
    "reports": {"quant_strategy_report": "초기 전략"},
    "strategy_spec": {
        "strategy_id": "seed-too-narrow",
        "ticker": "005930.KS",
        "trade_date": "2026-01-01",
        "entry": {"type": "price_zone", "low": 80.0, "high": 81.0},
        "take_profit": {"type": "fixed_price", "price": 90.0},
        "stop_loss": {"type": "fixed_price", "price": 70.0},
        "currency": "KRW",
    },
}

_CHART = {
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


@pytest.mark.unit
def test_self_feedback_loop_repeats_agent_revision_and_selects_best_iteration():
    from tradingagents.validation.performance import ValidationConfig
    from tradingagents.validation.self_feedback import run_strategy_self_feedback_loop

    calls = []

    def fake_agent_runner(payload):
        calls.append(payload)
        return {
            "quant_strategy_report": "진입 구간을 실제 최근 1년 일봉 접촉 범위로 넓힘",
            "strategy_spec": {
                "strategy_id": f"revised-{payload['iteration']}",
                "ticker": "005930.KS",
                "trade_date": "2026-01-01",
                "entry": {"type": "price_zone", "low": 99.0, "high": 101.0},
                "take_profit": {"type": "fixed_price", "price": 105.0},
                "stop_loss": {"type": "fixed_price", "price": 95.0},
                "currency": "KRW",
                "basis": ["피드백 기반 진입 조건 완화"],
                "avoid_conditions": ["95원 이탈 시 재분석"],
            },
        }

    result = run_strategy_self_feedback_loop(
        _BASE_RECORD,
        _CHART,
        iterations=2,
        agent_runner=fake_agent_runner,
        validation_config=ValidationConfig(min_trades=1, min_win_rate_percent=0, min_profit_factor=0, max_drawdown_percent=100),
    )

    assert result["loop_type"] == "strategy_self_feedback"
    assert result["ticker"] == "005930.KS"
    assert result["requested_iterations"] == 2
    assert len(result["iterations"]) == 2
    assert len(calls) == 1
    assert calls[0]["iteration"] == 2
    assert calls[0]["previous_iteration"]["strategy_spec"]["strategy_id"] == "seed-too-narrow"
    assert "진입 조건이 너무 좁아 시장에서 거의 실행되지 않음" in calls[0]["feedback"]["reasons"]
    assert result["iterations"][0]["pre_live_validation"]["reanalysis_required"] is True
    assert result["iterations"][1]["strategy_spec"]["strategy_id"] == "revised-2"
    assert result["iterations"][1]["pre_live_validation"]["paper_trading_candidate"] is True
    assert result["best_iteration"] == 2
    assert result["final_strategy_spec"]["strategy_id"] == "revised-2"
    assert result["live_capital_allowed"] is False


@pytest.mark.unit
def test_self_feedback_loop_records_agent_error_without_losing_previous_iteration():
    from tradingagents.validation.self_feedback import run_strategy_self_feedback_loop

    def failing_agent_runner(payload):
        raise TimeoutError("llm request timed out")

    result = run_strategy_self_feedback_loop(
        _BASE_RECORD,
        _CHART,
        iterations=3,
        agent_runner=failing_agent_runner,
    )

    assert result["requested_iterations"] == 3
    assert result["completed_iterations"] == 1
    assert len(result["iterations"]) == 2
    assert result["iterations"][0]["status"] == "completed"
    assert result["iterations"][1]["status"] == "agent_error"
    assert result["iterations"][1]["error_type"] == "TimeoutError"
    assert result["live_capital_allowed"] is False


@pytest.mark.unit
def test_repository_persists_self_feedback_loop_result(tmp_path):
    from tradingagents.dashboard.storage import AnalysisRepository

    repository = AnalysisRepository(tmp_path)
    loop = {
        "loop_id": "loop-1",
        "loop_type": "strategy_self_feedback",
        "ticker": "005930.KS",
        "trade_date": "2026-01-01",
        "requested_iterations": 2,
        "completed_iterations": 2,
        "best_iteration": 2,
        "live_capital_allowed": False,
        "iterations": [{"iteration": 1}, {"iteration": 2}],
    }

    saved = repository.save_strategy_self_feedback_loop(loop)
    loaded = repository.get_strategy_self_feedback_loop("loop-1")

    assert saved["loop_id"] == "loop-1"
    assert loaded is not None
    assert loaded["best_iteration"] == 2
    assert loaded["iterations"] == [{"iteration": 1}, {"iteration": 2}]
    assert repository.list_strategy_self_feedback_loops(ticker="005930.KS")[0]["loop_id"] == "loop-1"


@pytest.mark.unit
def test_dashboard_api_lists_and_loads_strategy_self_feedback_loops(tmp_path):
    from tradingagents.dashboard.app import create_dashboard_app
    from tradingagents.dashboard.storage import AnalysisRepository

    repository = AnalysisRepository(tmp_path)
    repository.save_strategy_self_feedback_loop({
        "loop_id": "loop-api-1",
        "loop_type": "strategy_self_feedback",
        "ticker": "005930.KS",
        "trade_date": "2026-01-01",
        "generated_at": "2026-01-02T00:00:00+00:00",
        "requested_iterations": 3,
        "completed_iterations": 3,
        "best_iteration": 2,
        "live_capital_allowed": False,
        "iterations": [{"iteration": 1}, {"iteration": 2}, {"iteration": 3}],
    })
    client = TestClient(create_dashboard_app(tmp_path))

    list_response = client.get("/api/strategy-self-feedback", params={"ticker": "005930.KS"})
    detail_response = client.get("/api/strategy-self-feedback/loop-api-1")

    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["loops"][0]["loop_id"] == "loop-api-1"
    assert detail_response.status_code == 200
    assert detail_response.json()["best_iteration"] == 2
    assert detail_response.json()["live_capital_allowed"] is False
