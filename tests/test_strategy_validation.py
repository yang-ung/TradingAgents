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
