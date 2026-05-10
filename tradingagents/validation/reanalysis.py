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


def run_reanalysis_if_required(
    record: dict[str, Any],
    chart: dict[str, Any],
    pre_live_report: dict[str, Any],
    *,
    agent_runner: AgentRunner | None = None,
    backtest_builder: BacktestBuilder = build_strategy_backtest,
    validation_config: ValidationConfig | None = None,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Call a strategy-revision agent when programmatic validation requests it.

    The deterministic program remains the judge: the agent can only propose a
    revised StrategySpec, then the program reruns backtest + pre-live validation.
    """

    if not isinstance(pre_live_report, dict) or not pre_live_report.get("reanalysis_required"):
        return {"triggered": False, "status": "not_required", "attempts": []}

    request = pre_live_report.get("reanalysis_request")
    if not isinstance(request, dict):
        return {"triggered": False, "status": "missing_request", "attempts": []}

    if request.get("agent") != "quant_strategy_analyst":
        return {
            "triggered": False,
            "status": "unsupported_agent",
            "requested_agent": request.get("agent"),
            "attempts": [],
        }

    if agent_runner is None:
        agent_runner = run_quant_strategy_reanalysis_agent

    attempts: list[dict[str, Any]] = []
    parent_spec = deepcopy(record.get("strategy_spec")) if isinstance(record, dict) else None
    current_record = deepcopy(record)
    latest_validation = pre_live_report

    for attempt_number in range(1, max(int(max_attempts or 0), 0) + 1):
        payload = _build_agent_payload(
            current_record,
            latest_validation,
            request,
            parent_spec,
            attempt_number,
        )
        agent_output = agent_runner(payload)
        attempt = _materialize_attempt(
            current_record,
            chart,
            agent_output,
            attempt_number,
            request,
            backtest_builder,
            validation_config,
        )
        attempts.append(attempt)
        if attempt.get("status") != "completed":
            break
        latest_validation = attempt.get("pre_live_validation") or {}
        if not latest_validation.get("reanalysis_required"):
            break
        current_record = attempt.get("record") or current_record

    final_attempt = attempts[-1] if attempts else {}
    final_validation = final_attempt.get("pre_live_validation") if isinstance(final_attempt, dict) else None
    if final_attempt.get("status") != "completed":
        status = final_attempt.get("status") or "failed"
    elif isinstance(final_validation, dict) and final_validation.get("reanalysis_required"):
        status = "max_attempts_reached"
    else:
        status = "completed"

    return {
        "triggered": True,
        "status": status,
        "agent": "quant_strategy_analyst",
        "action": request.get("action") or "revise_strategy",
        "requested_at": _utc_now_iso(),
        "attempts": attempts,
        "final_pre_live_validation": final_validation,
        "final_strategy_spec": final_attempt.get("strategy_spec") if isinstance(final_attempt, dict) else None,
    }


def run_quant_strategy_reanalysis_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """Production agent bridge for quant strategy revision.

    This intentionally runs the existing TradingAgents graph with an injected
    reanalysis context. The Quant Strategy Analyst prompt consumes that context
    and should emit a revised StrategySpec. Downstream graph participants then
    produce a complete refreshed final state.
    """

    from tradingagents.dashboard.batch import build_batch_config, DEFAULT_ARTIFACT_DIR
    from tradingagents.dashboard.extract import build_analysis_record, utc_now_iso
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    artifact_dir = payload.get("artifact_dir") or DEFAULT_ARTIFACT_DIR
    use_hermes_codex_auth = bool(payload.get("openai_use_hermes_codex_auth"))
    config = build_batch_config(artifact_dir, use_hermes_codex_auth=use_hermes_codex_auth)
    graph = TradingAgentsGraph(debug=False, config=config)
    final_state, _ = graph.propagate_reanalysis(
        payload.get("ticker") or (payload.get("record") or {}).get("ticker"),
        payload.get("trade_date") or (payload.get("record") or {}).get("trade_date"),
        reanalysis_payload=payload,
    )
    record = build_analysis_record(
        final_state,
        generated_at=utc_now_iso(),
        raw_log_path="",
        metadata={"reanalysis_of": payload.get("parent_run_id"), "reanalysis_attempt": payload.get("attempt")},
    )
    return {
        "record": record,
        "quant_strategy_report": (record.get("reports") or {}).get("quant_strategy_report", ""),
        "strategy_spec": record.get("strategy_spec"),
    }


def _build_agent_payload(
    record: dict[str, Any],
    pre_live_report: dict[str, Any],
    request: dict[str, Any],
    parent_spec: dict[str, Any] | None,
    attempt_number: int,
) -> dict[str, Any]:
    return {
        "agent": request.get("agent"),
        "action": request.get("action") or "revise_strategy",
        "attempt": attempt_number,
        "parent_run_id": record.get("run_id"),
        "artifact_dir": (record.get("metadata") or {}).get("artifact_dir") if isinstance(record.get("metadata"), dict) else record.get("artifact_dir"),
        "openai_use_hermes_codex_auth": bool((record.get("metadata") or {}).get("openai_use_hermes_codex_auth")) if isinstance(record.get("metadata"), dict) else False,
        "ticker": record.get("ticker"),
        "trade_date": record.get("trade_date"),
        "record": deepcopy(record),
        "previous_strategy_spec": deepcopy(record.get("strategy_spec") or parent_spec),
        "reanalysis_request": deepcopy(request),
        "pre_live_validation": deepcopy(pre_live_report),
        "diagnostics": deepcopy(request.get("diagnostics") or {}),
    }


def _materialize_attempt(
    parent_record: dict[str, Any],
    chart: dict[str, Any],
    agent_output: dict[str, Any],
    attempt_number: int,
    request: dict[str, Any],
    backtest_builder: BacktestBuilder,
    validation_config: ValidationConfig | None,
) -> dict[str, Any]:
    if not isinstance(agent_output, dict):
        return {"attempt": attempt_number, "agent_called": True, "status": "invalid_agent_output"}

    revised_record = deepcopy(agent_output.get("record") if isinstance(agent_output.get("record"), dict) else parent_record)
    revised_record["run_id"] = _reanalysis_run_id(parent_record, attempt_number)
    revised_record["generated_at"] = revised_record.get("generated_at") or _utc_now_iso()
    revised_record["ticker"] = revised_record.get("ticker") or parent_record.get("ticker")
    revised_record["trade_date"] = revised_record.get("trade_date") or parent_record.get("trade_date")
    revised_record["rating"] = revised_record.get("rating") or parent_record.get("rating") or "Hold"
    revised_record["trader_action"] = revised_record.get("trader_action") or parent_record.get("trader_action") or "Wait"
    revised_record["research_recommendation"] = revised_record.get("research_recommendation") or parent_record.get("research_recommendation") or "Hold"
    revised_record["decision_summary"] = revised_record.get("decision_summary") or "자동 재분석으로 생성된 전략 후보"
    revised_record["raw_log_path"] = revised_record.get("raw_log_path") or ""

    reports = dict(revised_record.get("reports") or {})
    if agent_output.get("quant_strategy_report"):
        reports["quant_strategy_report"] = str(agent_output.get("quant_strategy_report"))
    revised_record["reports"] = reports

    strategy_spec = agent_output.get("strategy_spec") or revised_record.get("strategy_spec")
    if parse_strategy_spec(strategy_spec) is None:
        return {"attempt": attempt_number, "agent_called": True, "status": "invalid_strategy_spec", "record": revised_record}
    revised_record["strategy_spec"] = deepcopy(strategy_spec)
    revised_record["reanalysis"] = {
        "parent_run_id": parent_record.get("run_id"),
        "attempt": attempt_number,
        "agent": request.get("agent"),
        "action": request.get("action") or "revise_strategy",
        "reasons": list(request.get("reasons") or []),
    }

    backtest = backtest_builder(revised_record, chart)
    pre_live = build_pre_live_validation_report(backtest, config=validation_config)
    return {
        "attempt": attempt_number,
        "agent_called": True,
        "status": "completed",
        "record": revised_record,
        "strategy_spec": deepcopy(strategy_spec),
        "backtest": backtest,
        "pre_live_validation": pre_live,
    }


def _reanalysis_run_id(record: dict[str, Any], attempt_number: int) -> str:
    parent = str(record.get("run_id") or "run")
    return f"{parent}-reanalysis-{attempt_number}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
