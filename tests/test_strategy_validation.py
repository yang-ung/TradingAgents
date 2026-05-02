from __future__ import annotations

import pytest

from tradingagents.validation.performance import evaluate_trade_returns, ValidationConfig


@pytest.mark.unit
def test_evaluate_trade_returns_applies_round_trip_costs_and_metrics():
    result = evaluate_trade_returns(
        [5.0, -2.0, 3.0],
        ValidationConfig(commission_bps=5, slippage_bps=10, min_trades=3, min_win_rate_percent=50, min_profit_factor=1.2, max_drawdown_percent=5),
    )

    # 5 bps commission + 10 bps slippage on entry and exit = 0.30% round-trip drag.
    assert result["adjusted_returns_percent"] == [4.7, -2.3, 2.7]
    assert result["trade_count"] == 3
    assert result["win_rate_percent"] == pytest.approx(66.67, abs=0.01)
    assert result["total_return_percent"] == pytest.approx(5.04, abs=0.01)
    assert result["profit_factor"] == pytest.approx(3.22, abs=0.01)
    assert result["passed"] is True


@pytest.mark.unit
def test_validation_config_rejects_unsafe_inputs():
    with pytest.raises(ValueError, match="commission_bps"):
        ValidationConfig(commission_bps=-1)
    with pytest.raises(ValueError, match="min_trades"):
        ValidationConfig(min_trades=0)


@pytest.mark.unit
def test_evaluate_trade_returns_fails_when_sample_or_drawdown_is_unsafe():
    result = evaluate_trade_returns(
        [4.0, -12.0],
        ValidationConfig(min_trades=10, max_drawdown_percent=5),
    )

    assert result["passed"] is False
    assert "표본 부족" in result["failure_reasons"]
    assert "최대낙폭 초과" in result["failure_reasons"]


@pytest.mark.unit
def test_pre_live_validation_report_converts_backtest_trades_to_paper_trading_candidate():
    from tradingagents.validation.pre_live import build_pre_live_validation_report

    backtest = {
        "available": True,
        "periods": {
            "전체": {
                "benchmark_return_percent": 3.0,
                "trades": [
                    {"return_percent": 5.0},
                    {"return_percent": -2.0},
                    {"return_percent": 3.0},
                ],
            }
        },
    }

    report = build_pre_live_validation_report(
        backtest,
        config=ValidationConfig(commission_bps=5, slippage_bps=10, min_trades=3, min_win_rate_percent=50, min_profit_factor=1.2, max_drawdown_percent=5),
        initial_capital=100_000_000,
    )

    assert report["available"] is True
    assert report["status_label"] == "paper trading 후보"
    assert report["lifecycle_status"] == "paper_candidate"
    assert report["paper_trading_candidate"] is True
    assert report["live_capital_allowed"] is False
    assert report["metrics"]["adjusted_returns_percent"] == [4.7, -2.3, 2.7]
    assert report["metrics"]["trade_count"] == 3
    assert report["costs"]["round_trip_cost_percent"] == 0.3
    assert report["benchmark"]["excess_return_percent"] == pytest.approx(2.04, abs=0.01)
    assert report["capital"]["pnl"] == 5_050_000
    assert "실전 투입 전 paper trading 검증이 필요" in report["next_step"]


@pytest.mark.unit
def test_pre_live_validation_report_fails_closed_for_low_sample_and_severe_market_risk():
    from tradingagents.validation.pre_live import build_pre_live_validation_report

    backtest = {
        "available": True,
        "periods": {"전체": {"benchmark_return_percent": -1.0, "trades": [{"return_percent": 4.0}]}},
    }

    report = build_pre_live_validation_report(
        backtest,
        scorecard={"available": True, "market_risk_score": -86},
        config=ValidationConfig(min_trades=3, min_win_rate_percent=0, min_profit_factor=0, max_drawdown_percent=100),
    )

    assert report["status_label"] == "검증 실패"
    assert report["lifecycle_status"] == "prelive_failed"
    assert report["paper_trading_candidate"] is False
    assert report["live_capital_allowed"] is False
    assert "표본 부족" in report["failure_reasons"]
    assert "시장 공통 리스크 매우 부정" in report["failure_reasons"]


@pytest.mark.unit
def test_pre_live_validation_report_marks_unavailable_as_prelive_failed():
    from tradingagents.validation.pre_live import build_pre_live_validation_report

    report = build_pre_live_validation_report({"available": False, "reason": "missing_strategy_spec"})

    assert report["status_label"] == "검증 불가"
    assert report["lifecycle_status"] == "prelive_failed"
    assert report["paper_trading_candidate"] is False
    assert report["live_capital_allowed"] is False


@pytest.mark.unit
def test_pre_live_validation_report_uses_nonzero_default_cost_model():
    from tradingagents.validation.pre_live import build_pre_live_validation_report

    backtest = {
        "available": True,
        "periods": {"전체": {"benchmark_return_percent": 0.0, "trades": [{"return_percent": 1.0} for _ in range(30)]}},
    }

    report = build_pre_live_validation_report(backtest)

    assert report["costs"]["commission_bps"] > 0
    assert report["costs"]["slippage_bps"] > 0
    assert report["costs"]["round_trip_cost_percent"] == 0.3
    assert report["metrics"]["adjusted_returns_percent"][0] == 0.7


@pytest.mark.unit
def test_batch_pre_live_validation_report_summarizes_candidates_and_failures():
    from tradingagents.validation.pre_live import build_batch_pre_live_validation_report

    items = [
        {
            "run_id": "pass-run",
            "ticker": "AAA",
            "backtest": {
                "available": True,
                "periods": {"전체": {"benchmark_return_percent": 1.0, "trades": [{"return_percent": 4.0}, {"return_percent": -1.0}, {"return_percent": 3.0}]}},
            },
        },
        {
            "run_id": "fail-run",
            "ticker": "BBB",
            "backtest": {
                "available": True,
                "periods": {"전체": {"benchmark_return_percent": 0.0, "trades": [{"return_percent": -2.0}]}},
            },
        },
    ]

    batch = build_batch_pre_live_validation_report(
        items,
        config=ValidationConfig(min_trades=3, min_win_rate_percent=50, min_profit_factor=1.0, max_drawdown_percent=5),
    )

    assert batch["total"] == 2
    assert batch["paper_trading_candidate_count"] == 1
    assert batch["failed_count"] == 1
    assert batch["live_capital_allowed_count"] == 0
    assert batch["candidates"][0]["ticker"] == "AAA"
    assert batch["failures"][0]["ticker"] == "BBB"
    assert "표본 부족" in batch["failures"][0]["failure_reasons"]
