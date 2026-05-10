from __future__ import annotations

from tradingagents.automation.signal_engine import (
    build_price_table,
    calculate_market_score,
    detect_fair_value_gaps,
)


def test_market_score_combines_macro_news_and_risk_factors():
    score = calculate_market_score(
        {
            "equity_trend": 18,
            "volatility": -6,
            "rates": -4,
            "fx": -2,
            "news_sentiment": 12,
            "macro_risk": -3,
        }
    )

    assert score["market_score"] == 65
    assert score["risk_level"] == "weak_risk_on"
    assert score["risk_off"] is False
    assert score["factor_scores"]["news_sentiment"] == 12
    assert score["score_base"] == 50
    assert score["score_formula"] == "기준점수 50 + 지수 추세 +18 + 변동성 -6 + 금리 -4 + 환율 -2 + 뉴스 심리 +12 + 거시 리스크 -3 = 65"
    assert score["factor_breakdown"][0] == {
        "key": "equity_trend",
        "label": "지수 추세",
        "score": 18,
        "contribution_label": "+18점",
        "description": "SPY/QQQ 등 주요 지수의 단기·중기 추세 강도",
    }
    assert score["factor_breakdown"][4]["label"] == "뉴스 심리"


def test_detect_fair_value_gaps_returns_bullish_zone_with_fill_state():
    candles = [
        {"date": "2026-05-01", "open": 100, "high": 101, "low": 98, "close": 100},
        {"date": "2026-05-02", "open": 102, "high": 105, "low": 101, "close": 104},
        {"date": "2026-05-03", "open": 106, "high": 108, "low": 103, "close": 107},
        {"date": "2026-05-04", "open": 106, "high": 107, "low": 102, "close": 103},
    ]

    gaps = detect_fair_value_gaps(candles, max_age=10)

    bullish = [gap for gap in gaps if gap["type"] == "bullish"]
    assert bullish
    latest = bullish[-1]
    assert latest["zone_low"] == 101.0
    assert latest["zone_high"] == 103.0
    assert latest["midpoint"] == 102.0
    assert latest["filled_percent"] == 50.0


def test_price_table_marks_buy_candidate_when_market_score_and_fvg_touch_align():
    market_state = {"market_score": 67, "risk_level": "weak_risk_on", "risk_off": False}
    candles = [
        {"date": "2026-05-01", "open": 100, "high": 101, "low": 98, "close": 100},
        {"date": "2026-05-02", "open": 102, "high": 105, "low": 101, "close": 104},
        {"date": "2026-05-03", "open": 106, "high": 108, "low": 103, "close": 107},
        {"date": "2026-05-04", "open": 106, "high": 107, "low": 102, "close": 103},
    ]

    table = build_price_table(market_state, {"QLD": candles})

    row = table["rows"][0]
    assert table["market_score"] == 67
    assert row["ticker"] == "QLD"
    assert row["setup_type"] == "bullish_fvg_retest"
    assert row["status_label"] == "진입구간 터치"
    assert row["action"] == "매수 후보"
    assert row["live_capital_allowed"] is False
    assert row["entry_zone_label"] == "101.00 ~ 103.00"
    assert row["stop_loss"] < row["entry_low"]
    assert row["take_profit_1"] > row["entry_high"]
