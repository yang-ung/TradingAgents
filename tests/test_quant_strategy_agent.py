from __future__ import annotations

import pytest


def test_quant_strategy_prompt_targets_price_timing_and_strategy_selection():
    from tradingagents.agents.analysts.quant_strategy_analyst import QUANT_STRATEGY_SYSTEM_MESSAGE

    prompt = QUANT_STRATEGY_SYSTEM_MESSAGE

    assert "entry price" in prompt.lower()
    assert "take-profit" in prompt.lower()
    assert "stop-loss" in prompt.lower()
    assert "trend-following" in prompt.lower()
    assert "mean-reversion" in prompt.lower()
    assert "volatility breakout" in prompt.lower()
    assert "position sizing" in prompt.lower()
    assert "do not invent exact prices" in prompt.lower()
    assert "strategyspec" in prompt.lower()
    assert "programmatic_rule_engine" in prompt
    assert "reanalysis_triggers" in prompt
    assert "valid_until" in prompt


@pytest.mark.unit
def test_price_timing_policy_registry_supports_pluggable_strategies(monkeypatch):
    from tradingagents.agents.strategies.price_timing import (
        PriceTimingPolicy,
        build_price_timing_policy,
        register_price_timing_policy,
    )

    class HalfAtrScalpPolicy(PriceTimingPolicy):
        key = "half_atr_scalp"
        label = "Half ATR Scalp"

        def prompt_instructions(self) -> str:
            return "Use 0.5x ATR for stop distance and 1.0x ATR for first take-profit."

    register_price_timing_policy(HalfAtrScalpPolicy)
    monkeypatch.setenv("TRADINGAGENTS_PRICE_TIMING_STRATEGY", "half_atr_scalp")

    policy = build_price_timing_policy()

    assert policy.key == "half_atr_scalp"
    assert "0.5x ATR" in policy.prompt_instructions()
    assert "1.0x ATR" in policy.prompt_instructions()


@pytest.mark.unit
def test_quant_strategy_prompt_includes_selected_price_timing_policy(monkeypatch):
    from tradingagents.agents.strategies.price_timing import (
        PriceTimingPolicy,
        build_price_timing_policy,
        register_price_timing_policy,
    )

    class BreakoutPolicy(PriceTimingPolicy):
        key = "breakout_policy_for_test"
        label = "Breakout Test Policy"

        def prompt_instructions(self) -> str:
            return "Buy only on confirmed resistance breakout; stop below breakout pivot."

    register_price_timing_policy(BreakoutPolicy)
    monkeypatch.setenv("TRADINGAGENTS_PRICE_TIMING_STRATEGY", "breakout_policy_for_test")

    policy_prompt = build_price_timing_policy().render_prompt_block()

    assert "Breakout Test Policy" in policy_prompt
    assert "confirmed resistance breakout" in policy_prompt


@pytest.mark.unit
def test_trader_schema_renders_take_profit_level():
    from tradingagents.agents.schemas import TraderAction, TraderProposal, render_trader_proposal

    proposal = TraderProposal(
        action=TraderAction.BUY,
        reasoning="Entry is only attractive near support with defined upside.",
        entry_price=333000,
        take_profit=360000,
        stop_loss=318000,
        position_sizing="half position first",
    )

    rendered = render_trader_proposal(proposal)

    assert "**Entry Price**: 333000.0" in rendered
    assert "**Take Profit**: 360000.0" in rendered
    assert "**Stop Loss**: 318000.0" in rendered


@pytest.mark.unit
def test_quant_strategy_report_is_persisted_in_dashboard_record(sample_final_state):
    from tradingagents.dashboard.extract import build_analysis_record

    final_state = dict(
        sample_final_state,
        quant_strategy_report=(
            "## Quant Strategy Analyst\n"
            "Primary strategy: trend-following.\n"
            "Entry price zone: 333,000 KRW.\n"
            "Take-profit zone: 360,000 KRW.\n"
            "Stop-loss: 318,000 KRW."
        ),
    )

    record = build_analysis_record(final_state, generated_at="2026-04-30T00:00:00+00:00")

    assert record["reports"]["quant_strategy_report"].startswith("## Quant Strategy Analyst")
    assert record["report_lengths"]["quant_strategy_report"] > 0


@pytest.mark.unit
def test_dashboard_detail_renders_quant_strategy_agent_section(tmp_path, sample_record, monkeypatch):
    from fastapi.testclient import TestClient

    from tradingagents.dashboard import app as dashboard_app
    from tradingagents.dashboard.app import create_dashboard_app
    from tradingagents.dashboard.storage import AnalysisRepository

    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, lookback_days=180: {"available": False, "reason": "no_price_data", "currency": "KRW"},
    )
    record = dict(sample_record)
    record["reports"] = dict(
        sample_record["reports"],
        quant_strategy_report=(
            "Primary strategy: trend-following after 333,000 KRW reclaim. "
            "Entry price zone 333,000-338,000, take-profit 360,000, stop-loss 318,000."
        ),
    )
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "퀀트 전략" in response.text
    assert "quant_strategy_report" not in response.text
    assert "trend-following" in response.text
    assert "333,000" in response.text
    assert "상세보기" in response.text


@pytest.mark.unit
def test_graph_setup_accepts_quant_analyst():
    from langchain_core.messages import AIMessage
    from langgraph.prebuilt import ToolNode

    from tradingagents.agents.utils.agent_utils import get_indicators, get_stock_data
    from tradingagents.graph.conditional_logic import ConditionalLogic
    from tradingagents.graph.setup import GraphSetup

    class FakeLLM:
        def bind_tools(self, tools):
            class Chain:
                def invoke(self, messages):
                    return AIMessage(content="quant strategy report")

            return Chain()

    tool_nodes = {
        "market": ToolNode([get_stock_data, get_indicators]),
        "quant": ToolNode([get_stock_data, get_indicators]),
    }
    setup = GraphSetup(FakeLLM(), FakeLLM(), tool_nodes, ConditionalLogic())

    workflow = setup.setup_graph(["market", "quant"])

    assert "Quant Analyst" in workflow.nodes
    assert "tools_quant" in workflow.nodes
