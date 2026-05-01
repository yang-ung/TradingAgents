"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.dashboard.extract import build_analysis_record


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "ZHIPU_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    for env_var in _API_KEY_ENV_VARS:
        monkeypatch.setenv(env_var, os.environ.get(env_var, "placeholder"))


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client


@pytest.fixture()
def sample_final_state():
    return {
        "company_of_interest": "NVDA",
        "trade_date": "2024-05-10",
        "market_report": "FINAL TRANSACTION PROPOSAL: **BUY**\nMomentum recovered and trend structure improved.",
        "quant_strategy_report": (
            "Quant Strategy Report\n"
            "Preferred strategy: buy pullbacks near 330,000, avoid chasing above resistance.\n"
            "Suggested entry 330,000-333,000, first take-profit 360,000, stop-loss 318,000."
        ),
        "sentiment_report": "FINAL TRANSACTION PROPOSAL: **HOLD**\nNo strong fresh catalyst from social/news sources.",
        "news_report": "# NVDA Weekly Macro & Trading News Report\nAI enthusiasm remains intact but valuation scrutiny is rising.",
        "fundamentals_report": "# NVDA Fundamental Report\nMargins and free cash flow remain exceptional.",
        "investment_debate_state": {
            "bull_history": "Bull case: growth and margins remain elite.",
            "bear_history": "Bear case: valuation leaves little room for disappointment.",
            "judge_decision": "**Recommendation**: Hold\n\n**Rationale**: Balanced setup.\n\n**Strategic Actions**: Maintain exposure.",
        },
        "trader_investment_decision": "**Action**: Hold\n\n**Reasoning**: Preserve exposure while avoiding aggressive adds.\n\nFINAL TRANSACTION PROPOSAL: **HOLD**",
        "risk_debate_state": {
            "aggressive_history": "Aggressive analyst: add on recovery.",
            "conservative_history": "Conservative analyst: do not chase valuation.",
            "neutral_history": "Neutral analyst: maintain current weight.",
            "judge_decision": "**Rating**: Hold\n\n**Executive Summary**: Maintain the current NVDA position near benchmark weight and do not chase strength.\n\n**Investment Thesis**: Fundamentals are strong, but valuation and AI-capex dependence argue for caution.\n\n**Time Horizon**: 3-6 months",
        },
        "investment_plan": "**Recommendation**: Hold\n\n**Rationale**: The setup is balanced.\n\n**Strategic Actions**: Maintain exposure and reassess on pullbacks.",
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: Maintain the current NVDA position near benchmark weight and do not chase strength.\n\n**Investment Thesis**: Fundamentals are strong, but valuation and AI-capex dependence argue for caution.\n\n**Time Horizon**: 3-6 months",
    }


@pytest.fixture()
def sample_record(sample_final_state):
    return build_analysis_record(
        sample_final_state,
        generated_at="2026-04-29T23:00:00+00:00",
        raw_log_path="artifacts/dashboard/raw_results/NVDA/TradingAgentsStrategy_logs/full_states_log_2024-05-10.json",
    )
