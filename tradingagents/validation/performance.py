from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Any


@dataclass(frozen=True)
class ValidationConfig:
    """Risk gates for pre-live trading validation.

    Costs are specified in basis points per side. The evaluator applies them
    once on entry and once on exit, so round-trip cost drag is:
    2 * (commission_bps + slippage_bps) / 100 percent points.
    """

    commission_bps: float = 0.0
    slippage_bps: float = 0.0
    min_trades: int = 30
    min_win_rate_percent: float = 50.0
    min_profit_factor: float = 1.2
    max_drawdown_percent: float = 10.0
    min_total_return_percent: float = 0.0

    def __post_init__(self) -> None:
        numeric_non_negative = ("commission_bps", "slippage_bps", "min_win_rate_percent", "min_profit_factor", "max_drawdown_percent")
        for field_name in numeric_non_negative:
            value = float(getattr(self, field_name))
            if not isfinite(value) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative finite number")
        if not isinstance(self.min_trades, int) or self.min_trades < 1:
            raise ValueError("min_trades must be an integer >= 1")
        if not isfinite(float(self.min_total_return_percent)):
            raise ValueError("min_total_return_percent must be finite")


def evaluate_trade_returns(
    gross_returns_percent: Iterable[float],
    config: ValidationConfig | None = None,
) -> dict[str, Any]:
    """Evaluate a list of gross trade returns after costs and risk gates.

    The function is intentionally deterministic and model-free. LLM agents can
    propose strategies, but this evaluator decides whether a sample is safe
    enough to proceed to paper trading / live capital gates.
    """

    cfg = config or ValidationConfig()
    cost_drag = 2.0 * (float(cfg.commission_bps) + float(cfg.slippage_bps)) / 100.0
    adjusted: list[float] = []
    for value in gross_returns_percent:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if isfinite(parsed):
            adjusted.append(round(parsed - cost_drag, 4))

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    for ret in adjusted:
        if ret > 0:
            wins += 1
            gross_profit += ret
        elif ret < 0:
            gross_loss += abs(ret)
        equity *= 1.0 + ret / 100.0
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)

    trade_count = len(adjusted)
    total_return = (equity - 1.0) * 100.0
    win_rate = wins / trade_count * 100.0 if trade_count else 0.0
    avg_return = sum(adjusted) / trade_count if trade_count else 0.0
    profit_factor = float("inf") if gross_loss == 0.0 and gross_profit > 0 else (gross_profit / gross_loss if gross_loss else 0.0)

    failure_reasons: list[str] = []
    if trade_count < cfg.min_trades:
        failure_reasons.append("표본 부족")
    if win_rate < cfg.min_win_rate_percent:
        failure_reasons.append("승률 미달")
    if profit_factor < cfg.min_profit_factor:
        failure_reasons.append("손익비 미달")
    if max_drawdown > cfg.max_drawdown_percent:
        failure_reasons.append("최대낙폭 초과")
    if total_return <= cfg.min_total_return_percent:
        failure_reasons.append("총수익률 미달")

    return {
        "adjusted_returns_percent": adjusted,
        "trade_count": trade_count,
        "win_rate_percent": round(win_rate, 2),
        "average_return_percent": round(avg_return, 2),
        "total_return_percent": round(total_return, 2),
        "max_drawdown_percent": round(max_drawdown, 2),
        "profit_factor": round(profit_factor, 2) if isfinite(profit_factor) else "inf",
        "cost_drag_percent_per_trade": round(cost_drag, 4),
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
    }
