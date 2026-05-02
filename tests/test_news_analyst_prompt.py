from __future__ import annotations

import pytest


def test_news_analyst_prompt_requires_market_common_social_political_economic_risks():
    from tradingagents.agents.analysts.news_analyst import NEWS_ANALYST_SYSTEM_MESSAGE

    assert "social, political, and economic" in NEWS_ANALYST_SYSTEM_MESSAGE
    assert "war" in NEWS_ANALYST_SYSTEM_MESSAGE.lower()
    assert "geopolitical" in NEWS_ANALYST_SYSTEM_MESSAGE.lower()
    assert "global_market" in NEWS_ANALYST_SYSTEM_MESSAGE
    assert "stock_specific" in NEWS_ANALYST_SYSTEM_MESSAGE
