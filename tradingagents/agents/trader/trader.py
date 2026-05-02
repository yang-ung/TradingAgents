"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools

from langchain_core.messages import AIMessage

from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        instrument_context = build_instrument_context(company_name)
        investment_plan = state["investment_plan"]
        quant_strategy_report = state.get("quant_strategy_report", "")
        market_report = state.get("market_report", "")
        sentiment_report = state.get("sentiment_report", "")
        news_report = state.get("news_report", "")
        fundamentals_report = state.get("fundamentals_report", "")

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    "When possible, translate the plan into actionable price timing: entry zone, add-on zone, take-profit/trim levels, stop-loss or invalidation, and when not to trade. "
                    "Separate the directional view from entry timing: a good company can still be a Hold if the current entry price is unattractive. "
                    "Apply a Market-common risk gate: if global_market news shows severe war, geopolitical, rates, FX, oil, tariff, sanctions, or risk-off pressure, do not recommend Buy unless the edge is explicit and the position size/stop are reduced. "
                    "Anchor your reasoning in the analysts' reports, the quant strategy report, and the research plan."
                    f"{get_language_instruction()}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}\n\n"
                    f"Market Analyst Report: {market_report}\n\n"
                    f"News Analyst Report: {news_report}\n\n"
                    f"Fundamentals Analyst Report: {fundamentals_report}\n\n"
                    f"Sentiment Analyst Report: {sentiment_report}\n\n"
                    f"Quant Strategy Report: {quant_strategy_report}\n\n"
                    f"Before selecting Buy/Hold/Sell, explicitly check: (1) directional view, "
                    f"(2) entry timing, (3) market-common risk gate, and (4) stop-loss / invalidation. "
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
