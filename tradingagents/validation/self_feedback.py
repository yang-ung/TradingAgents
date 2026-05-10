from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from tradingagents.dashboard.backtest import build_strategy_backtest
from tradingagents.strategies.schema import parse_strategy_spec

from .performance import ValidationConfig
from .pre_live import build_pre_live_validation_report

AgentRunner = Callable[[dict[str, Any]], dict[str, Any]]
BacktestBuilder = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def run_strategy_self_feedback_loop(
    seed_record: dict[str, Any],
    chart: dict[str, Any],
    *,
    iterations: int,
    agent_runner: AgentRunner | None = None,
    backtest_builder: BacktestBuilder = build_strategy_backtest,
    validation_config: ValidationConfig | None = None,
    scorecard: dict[str, Any] | None = None,
    period_label: str = "전체",
) -> dict[str, Any]:
    """Run an Agent→Program→feedback strategy revision loop.

    Each iteration is judged by deterministic backtest + pre-live validation.
    The agent may only propose the next StrategySpec; live capital stays blocked.
    """

    requested_iterations = max(int(iterations or 0), 0)
    if requested_iterations < 1:
        raise ValueError("iterations must be >= 1")
    if agent_runner is None and requested_iterations > 1:
        agent_runner = run_quant_strategy_self_feedback_agent

    current_record = deepcopy(seed_record or {})
    loop_iterations: list[dict[str, Any]] = []
    loop_id = _loop_id(current_record, requested_iterations)

    for iteration_number in range(1, requested_iterations + 1):
        materialized = _materialize_iteration(
            current_record,
            chart,
            iteration_number,
            backtest_builder,
            validation_config,
            scorecard,
            period_label,
        )
        loop_iterations.append(materialized)

        if iteration_number >= requested_iterations:
            break
        if agent_runner is None:
            break
        payload = _build_revision_payload(
            current_record,
            materialized,
            loop_iterations,
            iteration_number + 1,
            requested_iterations,
            loop_id,
        )
        agent_output = agent_runner(payload)
        next_record = _record_from_agent_output(current_record, agent_output, iteration_number + 1)
        if next_record is None:
            loop_iterations.append({
                "iteration": iteration_number + 1,
                "status": "invalid_strategy_spec",
                "agent_called": True,
                "feedback": payload["feedback"],
                "live_capital_allowed": False,
            })
            break
        current_record = next_record

    best = _select_best_iteration(loop_iterations)
    final_spec = best.get("strategy_spec") if best else None
    return {
        "loop_id": loop_id,
        "loop_type": "strategy_self_feedback",
        "ticker": current_record.get("ticker") or seed_record.get("ticker"),
        "trade_date": current_record.get("trade_date") or seed_record.get("trade_date"),
        "timeframe": "1d",
        "lookback": "chart_input",
        "requested_iterations": requested_iterations,
        "completed_iterations": sum(1 for item in loop_iterations if item.get("status") == "completed"),
        "best_iteration": best.get("iteration") if best else None,
        "final_strategy_spec": deepcopy(final_spec),
        "live_capital_allowed": False,
        "generated_at": _utc_now_iso(),
        "iterations": loop_iterations,
        "assumptions": [
            "Agent는 전략 수정만 제안하고, 프로그램이 최근 일봉 차트로 deterministic 검증을 수행합니다.",
            "반복 최적화 결과는 과거 구간에 과최적화될 수 있으므로 paper trading 전 실전 허가가 아닙니다.",
            "live_capital_allowed는 항상 False입니다.",
        ],
    }


def run_quant_strategy_self_feedback_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """Production bridge placeholder using the existing reanalysis graph path.

    This keeps the public loop API connected to the current TradingAgents graph
    without letting deterministic validation be replaced by LLM scoring.
    """

    from tradingagents.validation.reanalysis import run_quant_strategy_reanalysis_agent

    bridge_payload = dict(payload)
    bridge_payload["agent"] = "quant_strategy_analyst"
    bridge_payload["action"] = "revise_strategy"
    bridge_payload["pre_live_validation"] = payload.get("previous_iteration", {}).get("pre_live_validation") or {}
    bridge_payload["reanalysis_request"] = {
        "agent": "quant_strategy_analyst",
        "action": "revise_strategy",
        "reasons": (payload.get("feedback") or {}).get("reasons") or [],
        "diagnostics": (payload.get("feedback") or {}).get("diagnostics") or {},
    }
    bridge_payload["attempt"] = payload.get("iteration")
    record = payload.get("record") if isinstance(payload.get("record"), dict) else {}
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    bridge_payload["parent_run_id"] = record.get("run_id")
    bridge_payload["artifact_dir"] = payload.get("artifact_dir") or metadata.get("artifact_dir") or record.get("artifact_dir")
    bridge_payload["openai_use_hermes_codex_auth"] = bool(
        payload.get("openai_use_hermes_codex_auth") or metadata.get("openai_use_hermes_codex_auth")
    )
    return run_quant_strategy_reanalysis_agent(bridge_payload)


def _materialize_iteration(
    record: dict[str, Any],
    chart: dict[str, Any],
    iteration_number: int,
    backtest_builder: BacktestBuilder,
    validation_config: ValidationConfig | None,
    scorecard: dict[str, Any] | None,
    period_label: str,
) -> dict[str, Any]:
    backtest = backtest_builder(record, chart)
    pre_live = build_pre_live_validation_report(
        backtest,
        scorecard=scorecard,
        config=validation_config,
        period_label=period_label,
    )
    spec = parse_strategy_spec(record.get("strategy_spec"))
    return {
        "iteration": iteration_number,
        "status": "completed",
        "run_id": record.get("run_id"),
        "ticker": record.get("ticker"),
        "trade_date": record.get("trade_date"),
        "strategy_spec": spec.model_dump() if spec is not None else None,
        "backtest": backtest,
        "pre_live_validation": pre_live,
        "feedback": _feedback_from_validation(pre_live),
        "score": _iteration_score(pre_live),
        "live_capital_allowed": False,
    }


def _build_revision_payload(
    current_record: dict[str, Any],
    previous_iteration: dict[str, Any],
    loop_iterations: list[dict[str, Any]],
    next_iteration: int,
    requested_iterations: int,
    loop_id: str,
) -> dict[str, Any]:
    feedback = previous_iteration.get("feedback") or _feedback_from_validation(previous_iteration.get("pre_live_validation") or {})
    return {
        "loop_id": loop_id,
        "loop_type": "strategy_self_feedback",
        "agent": "quant_strategy_analyst",
        "action": "revise_strategy",
        "iteration": next_iteration,
        "requested_iterations": requested_iterations,
        "record": deepcopy(current_record),
        "previous_strategy_spec": deepcopy(current_record.get("strategy_spec")),
        "previous_iteration": deepcopy(previous_iteration),
        "iteration_summaries": [_compact_iteration(item) for item in loop_iterations],
        "feedback": feedback,
        "instructions": [
            "이전 StrategySpec을 최근 1년 일봉 백테스트 결과에 맞춰 수정한다.",
            "수익률만 높이지 말고 거래 표본, 진입 접촉 빈도, 손익비, MDD를 함께 개선한다.",
            "새 StrategySpec JSON을 반환하되 실전 허가는 하지 않는다.",
        ],
    }


def _record_from_agent_output(parent_record: dict[str, Any], agent_output: Any, iteration_number: int) -> dict[str, Any] | None:
    if not isinstance(agent_output, dict):
        return None
    next_record = deepcopy(agent_output.get("record") if isinstance(agent_output.get("record"), dict) else parent_record)
    strategy_spec = agent_output.get("strategy_spec") or next_record.get("strategy_spec")
    spec = parse_strategy_spec(strategy_spec)
    if spec is None:
        return None
    next_record["strategy_spec"] = spec.model_dump()
    next_record["run_id"] = f"{parent_record.get('run_id') or 'strategy'}-self-feedback-{iteration_number}"
    next_record["generated_at"] = next_record.get("generated_at") or _utc_now_iso()
    next_record["ticker"] = next_record.get("ticker") or parent_record.get("ticker")
    next_record["trade_date"] = next_record.get("trade_date") or parent_record.get("trade_date")
    reports = dict(next_record.get("reports") or {})
    if agent_output.get("quant_strategy_report"):
        reports["quant_strategy_report"] = str(agent_output.get("quant_strategy_report"))
    next_record["reports"] = reports
    next_record["decision_summary"] = next_record.get("decision_summary") or "셀프 피드백 루프로 수정된 전략"
    next_record["rating"] = next_record.get("rating") or parent_record.get("rating") or "Hold"
    next_record["trader_action"] = next_record.get("trader_action") or parent_record.get("trader_action") or "Wait"
    next_record["research_recommendation"] = next_record.get("research_recommendation") or parent_record.get("research_recommendation") or "Hold"
    next_record["raw_log_path"] = next_record.get("raw_log_path") or ""
    next_record["self_feedback"] = {
        "parent_run_id": parent_record.get("run_id"),
        "iteration": iteration_number,
        "agent": "quant_strategy_analyst",
        "action": "revise_strategy",
    }
    return next_record


def _feedback_from_validation(pre_live: dict[str, Any]) -> dict[str, Any]:
    reasons = list(pre_live.get("failure_reasons") or []) if isinstance(pre_live, dict) else []
    request = pre_live.get("reanalysis_request") if isinstance(pre_live, dict) else None
    if isinstance(request, dict):
        reasons.extend(str(reason) for reason in request.get("reasons") or [] if reason not in reasons)
    metrics = pre_live.get("metrics") if isinstance(pre_live, dict) else {}
    return {
        "status_label": pre_live.get("status_label") if isinstance(pre_live, dict) else "검증 불가",
        "reasons": reasons,
        "diagnostics": request.get("diagnostics") if isinstance(request, dict) else {},
        "metrics": metrics if isinstance(metrics, dict) else {},
        "recommended_action": pre_live.get("next_step") if isinstance(pre_live, dict) else "전략 재검토 필요",
    }


def _compact_iteration(item: dict[str, Any]) -> dict[str, Any]:
    validation = item.get("pre_live_validation") if isinstance(item.get("pre_live_validation"), dict) else {}
    metrics = validation.get("metrics") if isinstance(validation.get("metrics"), dict) else {}
    return {
        "iteration": item.get("iteration"),
        "status_label": validation.get("status_label"),
        "trade_count": metrics.get("trade_count"),
        "total_return_percent": metrics.get("total_return_percent"),
        "max_drawdown_percent": metrics.get("max_drawdown_percent"),
        "profit_factor": metrics.get("profit_factor"),
        "score": item.get("score"),
    }


def _select_best_iteration(items: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in items if item.get("status") == "completed" and item.get("strategy_spec")]
    if not completed:
        return {}
    return max(completed, key=lambda item: float(item.get("score") or -1_000_000.0))


def _iteration_score(pre_live: dict[str, Any]) -> float:
    if not isinstance(pre_live, dict) or not pre_live.get("available"):
        return -1_000_000.0
    metrics = pre_live.get("metrics") if isinstance(pre_live.get("metrics"), dict) else {}
    benchmark = pre_live.get("benchmark") if isinstance(pre_live.get("benchmark"), dict) else {}
    total_return = _safe_float(metrics.get("total_return_percent"), 0.0)
    max_drawdown = _safe_float(metrics.get("max_drawdown_percent"), 0.0)
    profit_factor = _safe_float(metrics.get("profit_factor"), 0.0)
    excess = _safe_float(benchmark.get("excess_return_percent"), 0.0)
    trade_count = _safe_float(metrics.get("trade_count"), 0.0)
    candidate_bonus = 100.0 if pre_live.get("paper_trading_candidate") else 0.0
    return round(candidate_bonus + total_return + excess + min(profit_factor, 10.0) + min(trade_count, 30.0) * 0.1 - max_drawdown, 4)


def _safe_float(value: Any, default: float) -> float:
    try:
        if value == "inf":
            return 10.0
        return float(value)
    except (TypeError, ValueError):
        return default


def _loop_id(record: dict[str, Any], iterations: int) -> str:
    ticker = str(record.get("ticker") or "UNKNOWN").replace("/", "-")
    trade_date = str(record.get("trade_date") or "date")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ticker}-{trade_date}-self-feedback-{iterations}x-{stamp}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
