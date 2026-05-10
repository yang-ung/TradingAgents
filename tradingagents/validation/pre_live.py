from __future__ import annotations

from math import isfinite
from typing import Any

from .performance import ValidationConfig, evaluate_trade_returns

_DEFAULT_INITIAL_CAPITAL = 100_000_000
_DEFAULT_PRE_LIVE_CONFIG = ValidationConfig(commission_bps=5.0, slippage_bps=10.0)
_SEVERE_MARKET_RISK_THRESHOLD = -75
_MIN_ENTRY_TOUCH_PERCENT = 10.0


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
            "lifecycle_status": "prelive_failed",
            "paper_trading_candidate": False,
            "live_capital_allowed": False,
            "failure_reasons": ["백테스트 데이터 없음"],
            "reanalysis_required": False,
            "reanalysis_request": None,
        }

    period = _select_period(backtest, period_label)
    if period is None:
        return {
            "available": False,
            "reason": "validation_period_unavailable",
            "status_label": "검증 불가",
            "lifecycle_status": "prelive_failed",
            "paper_trading_candidate": False,
            "live_capital_allowed": False,
            "failure_reasons": ["검증 기간 데이터 없음"],
            "reanalysis_required": False,
            "reanalysis_request": None,
        }

    returns = _extract_trade_returns(period)
    cfg = config or _DEFAULT_PRE_LIVE_CONFIG
    metrics = evaluate_trade_returns(returns, cfg)
    failure_reasons = list(metrics.get("failure_reasons") or [])
    entry_touch_failure = _entry_touch_failure(period)
    if entry_touch_failure and entry_touch_failure not in failure_reasons:
        failure_reasons.append(entry_touch_failure)

    market_risk_score = _market_risk_score(scorecard)
    if market_risk_score is not None and market_risk_score <= _SEVERE_MARKET_RISK_THRESHOLD:
        failure_reasons.append("시장 공통 리스크 매우 부정")

    reanalysis_request = _build_reanalysis_request(failure_reasons, period)
    reanalysis_required = reanalysis_request is not None
    passed_gate_a = bool(metrics.get("passed")) and not failure_reasons and not reanalysis_required
    total_return = _safe_float(metrics.get("total_return_percent")) or 0.0
    benchmark_return = _safe_float(period.get("benchmark_return_percent")) or 0.0
    capital = max(int(initial_capital or 0), 0)
    pnl = int(round(capital * total_return / 100.0))
    adjusted_final_equity = capital + pnl
    cost_drag = _safe_float(metrics.get("cost_drag_percent_per_trade")) or 0.0

    status_label = "paper trading 후보" if passed_gate_a else ("재분석 필요" if reanalysis_required and _only_reanalysis_failures(failure_reasons) else "검증 실패")
    lifecycle_status = "paper_candidate" if passed_gate_a else ("reanalysis_required" if status_label == "재분석 필요" else "prelive_failed")

    return {
        "available": True,
        "gate": "Gate A",
        "gate_label": "실전 투입 전 과거 적용 검증",
        "status_label": status_label,
        "lifecycle_status": lifecycle_status,
        "passed_gate_a": passed_gate_a,
        "paper_trading_candidate": passed_gate_a,
        "live_capital_allowed": False,
        "failure_reasons": failure_reasons,
        "reanalysis_required": reanalysis_required,
        "reanalysis_request": reanalysis_request,
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
            else (
                "전략 실행 빈도 부족: Quant Strategy Analyst 재분석으로 진입 조건/전략 유형을 재조정해야 합니다."
                if reanalysis_required
                else "실전 투입 보류: 실패 사유를 해소하거나 새 Agent 재분석이 필요합니다."
            )
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
            "lifecycle_status": report.get("lifecycle_status"),
            "paper_trading_candidate": bool(report.get("paper_trading_candidate")),
            "live_capital_allowed": bool(report.get("live_capital_allowed")),
            "failure_reasons": report.get("failure_reasons") or [],
            "reanalysis_required": bool(report.get("reanalysis_required")),
            "reanalysis_request": report.get("reanalysis_request"),
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


def _entry_touch_failure(period: dict[str, Any]) -> str | None:
    touch_percent = _safe_float(period.get("entry_touch_percent"))
    if touch_percent is not None and touch_percent < _MIN_ENTRY_TOUCH_PERCENT:
        return "진입 접촉 빈도 부족"
    return None


def _build_reanalysis_request(failure_reasons: list[str], period: dict[str, Any]) -> dict[str, Any] | None:
    reasons: list[str] = []
    if "표본 부족" in failure_reasons:
        reasons.append("거래 표본 부족")
    if "진입 접촉 빈도 부족" in failure_reasons:
        reasons.append("진입 조건이 너무 좁아 시장에서 거의 실행되지 않음")
    if not reasons:
        return None
    return {
        "agent": "quant_strategy_analyst",
        "action": "revise_strategy",
        "reasons": reasons,
        "guidance": [
            "전략 유형을 장기보유형/스윙형/단타형/방어형으로 먼저 재분류",
            "고정 진입가가 원인이라면 이동평균/ATR 기반 동적 진입 조건으로 재설계",
            "재조정 StrategySpec 생성 후 비용 반영 백테스트를 재실행",
        ],
        "diagnostics": {
            "trade_count": period.get("trade_count"),
            "entry_touch_days": period.get("entry_touch_days"),
            "entry_touch_percent": period.get("entry_touch_percent"),
            "sample_days": period.get("sample_days"),
            "min_entry_touch_percent": _MIN_ENTRY_TOUCH_PERCENT,
        },
    }


def _only_reanalysis_failures(failure_reasons: list[str]) -> bool:
    reanalysis_failures = {"표본 부족", "진입 접촉 빈도 부족"}
    return bool(failure_reasons) and all(reason in reanalysis_failures for reason in failure_reasons)


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
