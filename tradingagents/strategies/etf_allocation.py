from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd


WeightRule = Callable[[pd.Timestamp, pd.DataFrame, dict[str, Any]], tuple[str, dict[str, float]]]


@dataclass(frozen=True)
class ETFAllocationSpec:
    """Executable ETF allocation contract for programmatic system trading tests."""

    strategy_id: str
    assets: tuple[str, ...]
    rule: WeightRule
    rebalance_frequency: str = "monthly"
    execution_mode: str = "etf_allocation_rule_engine"
    live_capital_allowed: bool = False


def build_static_weight_spec(*, strategy_id: str, weights: dict[str, float]) -> ETFAllocationSpec:
    assets = tuple(weights.keys())
    normalized = _normalize_weights(weights, assets)

    def rule(dt: pd.Timestamp, history: pd.DataFrame, state: dict[str, Any]) -> tuple[str, dict[str, float]]:
        return "static", dict(normalized)

    return ETFAllocationSpec(strategy_id=strategy_id, assets=assets, rule=rule)


def build_adaptive_core_v2_spec(
    *,
    strategy_id: str = "adaptive_core_v2",
    risk_on_weights: dict[str, float] | None = None,
    risk_off_weights: dict[str, float] | None = None,
    confirmation_months: int = 2,
    drawdown_trigger: float = -0.10,
) -> ETFAllocationSpec:
    """Build a less whipsaw-prone ETF rule.

    v2 does not attempt full liquidation. It confirms risk-off with consecutive
    monthly closes under the rolling trend line plus drawdown, then keeps partial
    equity exposure and routes only a controlled part to cash-like assets.
    """

    risk_on = risk_on_weights or {"SPY": 0.50, "QQQ": 0.30, "SCHD": 0.10, "IEF": 0.00, "GLD": 0.05, "BIL": 0.05}
    risk_off = risk_off_weights or {"SPY": 0.35, "QQQ": 0.10, "SCHD": 0.10, "IEF": 0.15, "GLD": 0.10, "BIL": 0.20}
    assets = tuple(dict.fromkeys([*risk_on.keys(), *risk_off.keys()]))
    risk_on = _normalize_weights(risk_on, assets)
    risk_off = _normalize_weights(risk_off, assets)
    confirmation_months = max(1, int(confirmation_months))

    def rule(dt: pd.Timestamp, history: pd.DataFrame, state: dict[str, Any]) -> tuple[str, dict[str, float]]:
        if len(history) < 4 or "SPY" not in history:
            return "risk_on_warmup", dict(risk_on)
        monthly = history.resample("ME").last().dropna(how="all")
        spy = monthly["SPY"].dropna()
        if len(spy) < 4:
            return "risk_on_warmup", dict(risk_on)
        trend_window = min(10, len(spy))
        trend = spy.rolling(trend_window, min_periods=1).mean()
        peak = spy.cummax()
        below_and_drawdown = (spy < trend) & ((spy / peak - 1.0) <= float(drawdown_trigger))
        confirmed = bool(below_and_drawdown.tail(confirmation_months).all())
        if confirmed:
            return "risk_off_confirmed", dict(risk_off)
        return "risk_on", dict(risk_on)

    return ETFAllocationSpec(strategy_id=strategy_id, assets=assets, rule=rule)


def run_etf_allocation_backtest(
    spec: ETFAllocationSpec,
    prices: pd.DataFrame,
    *,
    monthly_contribution: float = 1_000_000.0,
    initial_capital: float = 0.0,
) -> dict[str, Any]:
    """Run deterministic ETF allocation replay without LLM calls."""

    if monthly_contribution < 0 or initial_capital < 0:
        raise ValueError("capital inputs must be non-negative")
    if prices.empty:
        raise ValueError("prices must not be empty")
    frame = prices.sort_index().copy().ffill().dropna(how="all")
    for asset in spec.assets:
        if asset not in frame:
            raise ValueError(f"missing price column: {asset}")
    frame = frame[list(spec.assets)].dropna()
    if frame.empty:
        raise ValueError("prices have no complete rows for strategy assets")

    shares = {asset: 0.0 for asset in spec.assets}
    invested = float(initial_capital)
    last_month: tuple[int, int] | None = None
    events: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    state: dict[str, Any] = {}
    contribution_count = 0

    for dt, row in frame.iterrows():
        timestamp = pd.Timestamp(dt)
        month_key = (timestamp.year, timestamp.month)
        should_rebalance = False
        contribution = 0.0
        if last_month != month_key:
            contribution = float(monthly_contribution)
            invested += contribution
            contribution_count += 1 if contribution else 0
            should_rebalance = True
            last_month = month_key
        if initial_capital and not events:
            should_rebalance = True

        if should_rebalance:
            history = frame.loc[:timestamp]
            regime, weights = spec.rule(timestamp, history, state)
            weights = _normalize_weights(weights, spec.assets)
            total_value = _portfolio_value(shares, row) + contribution
            for asset in spec.assets:
                shares[asset] = 0.0 if row[asset] <= 0 else total_value * weights[asset] / float(row[asset])
            events.append(
                {
                    "date": timestamp.date().isoformat(),
                    "event": "rebalance",
                    "regime": regime,
                    "contribution": contribution,
                    "target_weights": {asset: round(weights[asset], 6) for asset in spec.assets},
                    "portfolio_value": round(_portfolio_value(shares, row), 2),
                }
            )

        curve.append(
            {
                "date": timestamp.date().isoformat(),
                "value": round(_portfolio_value(shares, row), 2),
                "invested": round(invested, 2),
            }
        )

    values = pd.Series([point["value"] for point in curve], index=[point["date"] for point in curve], dtype="float64")
    final_value = float(values.iloc[-1])
    max_drawdown = float((values / values.cummax() - 1.0).min() * 100.0)
    total_return = 0.0 if invested == 0 else (final_value / invested - 1.0) * 100.0
    return {
        "strategy_id": spec.strategy_id,
        "execution_mode": spec.execution_mode,
        "system_trading_engine": spec.execution_mode,
        "live_capital_allowed": bool(spec.live_capital_allowed),
        "invested": round(invested, 2),
        "final_value": round(final_value, 2),
        "profit": round(final_value - invested, 2),
        "total_return_percent": round(total_return, 4),
        "max_drawdown_percent": round(max_drawdown, 4),
        "contribution_count": contribution_count,
        "events": events,
        "equity_curve": curve,
    }


def assess_etf_live_readiness(
    *,
    selected_result: dict[str, Any],
    benchmark_result: dict[str, Any],
    evaluation_windows: list[dict[str, Any]] | None = None,
    leveraged_assets: tuple[str, ...] = ("QLD", "TQQQ", "SSO", "UPRO", "SOXL", "TECL"),
    max_allowed_leveraged_weight: float = 0.0,
    min_evaluation_windows: int = 4,
    max_window_drawdown_percent: float = -25.0,
    require_positive_excess_all_windows: bool = True,
    cost_model: dict[str, Any] | None = None,
    paper_trading: dict[str, Any] | None = None,
    min_paper_observed_days: int = 90,
    min_paper_closed_signals: int = 6,
    max_paper_drawdown_percent: float = -8.0,
) -> dict[str, Any]:
    """Fail-closed ETF live-readiness gate for allocation candidates.

    This is not a profitability certificate. It records the deterministic evidence
    required before an ETF portfolio can even become a limited-live review
    candidate. The returned ``live_capital_allowed`` intentionally remains False;
    passing this gate still requires final human approval and broker/execution
    controls outside the backtest engine.
    """

    windows = evaluation_windows or []
    weights = selected_result.get("last_weights") if isinstance(selected_result.get("last_weights"), dict) else {}
    leveraged_weight = sum(float(weights.get(asset, 0.0) or 0.0) for asset in leveraged_assets)
    selected_return = float(selected_result.get("total_return_percent") or 0.0)
    benchmark_return = float(benchmark_result.get("total_return_percent") or 0.0)
    failure_reasons: list[str] = []
    warnings: list[str] = []

    window_check_passed = len(windows) >= min_evaluation_windows
    if not window_check_passed:
        failure_reasons.append("장기 검증 부족")
    if leveraged_weight > max_allowed_leveraged_weight:
        failure_reasons.append("레버리지 ETF 포함")

    worst_window_drawdown = None
    weak_windows: list[str] = []
    for window in windows:
        result = window.get("result") if isinstance(window, dict) else None
        benchmark = window.get("benchmark") if isinstance(window, dict) else None
        if not isinstance(result, dict):
            continue
        drawdown = float(result.get("max_drawdown_percent") or 0.0)
        worst_window_drawdown = drawdown if worst_window_drawdown is None else min(worst_window_drawdown, drawdown)
        if require_positive_excess_all_windows and isinstance(benchmark, dict):
            result_return = float(result.get("total_return_percent") or 0.0)
            benchmark_window_return = float(benchmark.get("total_return_percent") or 0.0)
            if result_return < benchmark_window_return:
                weak_windows.append(str(window.get("label") or "미지정 구간"))
    if worst_window_drawdown is not None and worst_window_drawdown < max_window_drawdown_percent:
        failure_reasons.append("위기구간 MDD 초과")
    if weak_windows:
        failure_reasons.append("장기/위기구간 단순 DCA 대비 미달")

    cost_model = cost_model if isinstance(cost_model, dict) else {}
    cost_model_passed = all(
        bool(cost_model.get(key))
        for key in ("modeled", "fx_modeled", "tax_modeled", "trading_cost_modeled", "tracking_error_modeled")
    ) and str(cost_model.get("market") or "").upper() == "KR"
    if not cost_model_passed:
        failure_reasons.append("한국 계좌 비용/세금/환율 미반영")

    paper_trading = paper_trading if isinstance(paper_trading, dict) else {}
    paper_days = int(paper_trading.get("observed_days") or 0)
    paper_closed = int(paper_trading.get("closed_signals") or 0)
    paper_return = float(paper_trading.get("total_return_percent") or 0.0)
    paper_excess = float(paper_trading.get("benchmark_excess_return_percent") or 0.0)
    paper_drawdown = float(paper_trading.get("max_drawdown_percent") or 0.0)
    paper_passed = (
        paper_days >= min_paper_observed_days
        and paper_closed >= min_paper_closed_signals
        and paper_return > 0
        and paper_excess > 0
        and paper_drawdown >= max_paper_drawdown_percent
    )
    if not paper_passed:
        failure_reasons.append("paper trading 검증 부족")

    if cost_model_passed and not failure_reasons:
        status = "limited_live_candidate"
        status_label = "제한적 실거래 검토 후보"
        recommended_action = "최종 사람 승인, 주문 한도, 손실 중단 규칙, 브로커 연동 안전장치 검토 후 제한적 실거래만 검토"
    else:
        status = "not_ready"
        status_label = "실거래 보류"
        recommended_action = "미충족 게이트를 먼저 해소하고 paper trading 누적 후 재평가"
    if leveraged_weight > 0:
        warnings.append("레버리지 ETF는 위성 소수 비중이어도 급락·횡보장 변동성 훼손 위험이 큼")

    return {
        "status": status,
        "status_label": status_label,
        "live_capital_allowed": False,
        "failure_reasons": failure_reasons,
        "warnings": warnings,
        "recommended_action": recommended_action,
        "leveraged_assets": list(leveraged_assets),
        "leveraged_weight_percent": round(leveraged_weight * 100.0, 2),
        "max_allowed_leveraged_weight_percent": round(max_allowed_leveraged_weight * 100.0, 2),
        "benchmark_excess_return_percent": round(selected_return - benchmark_return, 2),
        "evaluation_window_count": len(windows),
        "min_evaluation_windows": min_evaluation_windows,
        "worst_window_drawdown_percent": None if worst_window_drawdown is None else round(worst_window_drawdown, 2),
        "readiness_checks": {
            "evaluation_windows": {"passed": window_check_passed, "count": len(windows), "required": min_evaluation_windows},
            "crisis_drawdown": {
                "passed": worst_window_drawdown is not None and worst_window_drawdown >= max_window_drawdown_percent,
                "worst_window_drawdown_percent": None if worst_window_drawdown is None else round(worst_window_drawdown, 2),
                "max_allowed_drawdown_percent": max_window_drawdown_percent,
            },
            "benchmark_excess": {"passed": not weak_windows, "weak_windows": weak_windows},
            "leverage_limit": {
                "passed": leveraged_weight <= max_allowed_leveraged_weight,
                "leveraged_weight_percent": round(leveraged_weight * 100.0, 2),
                "max_allowed_leveraged_weight_percent": round(max_allowed_leveraged_weight * 100.0, 2),
            },
            "cost_model": {"passed": cost_model_passed, "required": "KR 세금/환율/거래비용/괴리율 또는 추적오차 반영"},
            "paper_trading": {
                "passed": paper_passed,
                "observed_days": paper_days,
                "min_observed_days": min_paper_observed_days,
                "closed_signals": paper_closed,
                "min_closed_signals": min_paper_closed_signals,
                "total_return_percent": round(paper_return, 2),
                "benchmark_excess_return_percent": round(paper_excess, 2),
                "max_drawdown_percent": round(paper_drawdown, 2),
                "max_allowed_drawdown_percent": max_paper_drawdown_percent,
            },
        },
        "assumption": "실거래 가능 판단은 장기/위기구간, 한국 계좌 비용/세금/환율, paper trading 검증을 모두 통과한 뒤에도 최종 승인 전까지 live_capital_allowed=false",
    }


def _portfolio_value(shares: dict[str, float], row: pd.Series) -> float:
    return float(sum(shares[asset] * float(row[asset]) for asset in shares))


def _normalize_weights(weights: dict[str, float], assets: tuple[str, ...]) -> dict[str, float]:
    normalized = {asset: float(weights.get(asset, 0.0)) for asset in assets}
    if any(value < 0 for value in normalized.values()):
        raise ValueError("weights must be non-negative")
    total = sum(normalized.values())
    if total <= 0:
        raise ValueError("weights must sum to a positive value")
    return {asset: value / total for asset, value in normalized.items()}
