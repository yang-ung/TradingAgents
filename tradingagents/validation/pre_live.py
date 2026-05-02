from __future__ import annotations

from math import isfinite
from typing import Any

from .performance import ValidationConfig, evaluate_trade_returns

_DEFAULT_INITIAL_CAPITAL = 100_000_000
_DEFAULT_PRE_LIVE_CONFIG = ValidationConfig(commission_bps=5.0, slippage_bps=10.0)
_SEVERE_MARKET_RISK_THRESHOLD = -75


def build_pre_live_validation_report(
    backtest: dict[str, Any],
    *,
    scorecard: dict[str, Any] | None = None,
    config: ValidationConfig | None = None,
    initial_capital: int = _DEFAULT_INITIAL_CAPITAL,
    period_label: str = "전체",
) -> dict[str, Any]:
    """Build a conservative pre-live validation gate report from deterministic replay results.

    This is intentionally model-free: LLM agents propose a strategy, while this
    adapter consumes already-computed trade returns and applies cost/risk gates.
    Passing this report only promotes a strategy to paper trading; it never
    allows live capital by itself.
    """

    if not isinstance(backtest, dict) or not backtest.get("available"):
        return {
            "available": False,
            "reason": str((backtest or {}).get("reason") or "backtest_unavailable"),
            "status_label": "검증 불가",
            "paper_trading_candidate": False,
            "live_capital_allowed": False,
            "failure_reasons": ["백테스트 데이터 없음"],
        }

    period = _select_period(backtest, period_label)
    if period is None:
        return {
            "available": False,
            "reason": "validation_period_unavailable",
            "status_label": "검증 불가",
            "paper_trading_candidate": False,
            "live_capital_allowed": False,
            "failure_reasons": ["검증 기간 데이터 없음"],
        }

    returns = _extract_trade_returns(period)
    cfg = config or _DEFAULT_PRE_LIVE_CONFIG
    metrics = evaluate_trade_returns(returns, cfg)
    failure_reasons = list(metrics.get("failure_reasons") or [])

    market_risk_score = _market_risk_score(scorecard)
    if market_risk_score is not None and market_risk_score <= _SEVERE_MARKET_RISK_THRESHOLD:
        failure_reasons.append("시장 공통 리스크 매우 부정")

    passed_gate_a = bool(metrics.get("passed")) and not failure_reasons
    total_return = _safe_float(metrics.get("total_return_percent")) or 0.0
    benchmark_return = _safe_float(period.get("benchmark_return_percent")) or 0.0
    capital = max(int(initial_capital or 0), 0)
    pnl = int(round(capital * total_return / 100.0))
    adjusted_final_equity = capital + pnl
    cost_drag = _safe_float(metrics.get("cost_drag_percent_per_trade")) or 0.0

    return {
        "available": True,
        "gate": "Gate A",
        "gate_label": "실전 투입 전 과거 적용 검증",
        "status_label": "paper trading 후보" if passed_gate_a else "검증 실패",
        "passed_gate_a": passed_gate_a,
        "paper_trading_candidate": passed_gate_a,
        "live_capital_allowed": False,
        "failure_reasons": failure_reasons,
        "period_label": period_label if period_label in (backtest.get("periods") or {}) else "전체",
        "period": {
            "start_date": period.get("start_date"),
            "end_date": period.get("end_date"),
        },
        "metrics": metrics,
        "benchmark": {
            "benchmark_return_percent": round(benchmark_return, 2),
            "excess_return_percent": round(total_return - benchmark_return, 2),
        },
        "costs": {
            "commission_bps": float(cfg.commission_bps),
            "slippage_bps": float(cfg.slippage_bps),
            "round_trip_cost_percent": round(cost_drag, 4),
        },
        "capital": {
            "initial_capital": capital,
            "final_equity": adjusted_final_equity,
            "pnl": pnl,
        },
        "risk": {
            "market_risk_score": market_risk_score,
            "severe_market_risk_threshold": _SEVERE_MARKET_RISK_THRESHOLD,
        },
        "next_step": (
            "실전 투입 전 paper trading 검증이 필요합니다."
            if passed_gate_a
            else "실전 투입 보류: 실패 사유를 해소하거나 새 Agent 재분석이 필요합니다."
        ),
        "assumptions": [
            "저장된 진입·익절·손절 규칙을 과거 가격에 적용한 deterministic 검증입니다.",
            "비용/슬리피지는 거래별 왕복 비용으로 차감했습니다.",
            "현재 Gate A 통과는 실전 허가가 아니라 paper trading 후보 선별입니다.",
            "미래 수익을 보장하지 않습니다.",
        ],
    }


def build_batch_pre_live_validation_report(
    items: list[dict[str, Any]],
    *,
    config: ValidationConfig | None = None,
    initial_capital: int = _DEFAULT_INITIAL_CAPITAL,
    period_label: str = "전체",
) -> dict[str, Any]:
    """Summarize pre-live gates across multiple saved analysis runs."""

    reports: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        report = build_pre_live_validation_report(
            item.get("backtest") if isinstance(item.get("backtest"), dict) else {},
            scorecard=item.get("scorecard") if isinstance(item.get("scorecard"), dict) else None,
            config=config,
            initial_capital=initial_capital,
            period_label=period_label,
        )
        summary = {
            "run_id": item.get("run_id"),
            "ticker": item.get("ticker"),
            "status_label": report.get("status_label"),
            "paper_trading_candidate": bool(report.get("paper_trading_candidate")),
            "live_capital_allowed": bool(report.get("live_capital_allowed")),
            "failure_reasons": report.get("failure_reasons") or [],
            "total_return_percent": (report.get("metrics") or {}).get("total_return_percent") if isinstance(report.get("metrics"), dict) else None,
            "pnl": (report.get("capital") or {}).get("pnl") if isinstance(report.get("capital"), dict) else None,
        }
        reports.append(summary)
        if summary["paper_trading_candidate"]:
            candidates.append(summary)
        else:
            failures.append(summary)

    return {
        "total": len(reports),
        "paper_trading_candidate_count": len(candidates),
        "failed_count": len(failures),
        "live_capital_allowed_count": sum(1 for report in reports if report["live_capital_allowed"]),
        "candidates": candidates,
        "failures": failures,
        "reports": reports,
    }


def _select_period(backtest: dict[str, Any], period_label: str) -> dict[str, Any] | None:
    periods = backtest.get("periods")
    if not isinstance(periods, dict):
        return None
    period = periods.get(period_label) or periods.get("전체")
    return period if isinstance(period, dict) else None


def _extract_trade_returns(period: dict[str, Any]) -> list[float]:
    returns: list[float] = []
    trades = period.get("trades")
    if not isinstance(trades, list):
        return returns
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        value = _safe_float(trade.get("return_percent"))
        if value is not None:
            returns.append(value)
    return returns


def _market_risk_score(scorecard: dict[str, Any] | None) -> int | None:
    if not isinstance(scorecard, dict) or not scorecard.get("available", True):
        return None
    value = _safe_float(scorecard.get("market_risk_score"))
    return int(round(value)) if value is not None else None


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None
