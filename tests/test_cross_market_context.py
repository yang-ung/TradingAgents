from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.storage import AnalysisRepository
from tradingagents.automation.cross_market_context import build_cross_market_context


def _stock_snapshot():
    return {
        "generated_at": "2026-05-07T22:00:00+00:00",
        "market_score": 32,
        "risk_level": "risk_off",
        "risk_level_label": "risk-off",
        "change_summary": "시장 점수 55 → 32 (-23): 지수 추세 -12점, 거시 리스크 -10점, 뉴스 심리 -6점",
        "factor_scores": {"equity_trend": -12, "volatility": -6, "rates": -2, "fx": -4, "news_sentiment": -6, "macro_risk": -10},
        "factor_breakdown": [
            {"key": "equity_trend", "label": "지수 추세", "score": -12, "description": "SPY/QQQ 등 주요 지수의 단기·중기 추세 강도"},
            {"key": "macro_risk", "label": "거시 리스크", "score": -10, "description": "지정학·정책·경기침체 등 시장 공통 위험 요인"},
        ],
        "rows": [
            {"ticker": "QQQ", "action": "신규매수 금지", "status_label": "시장 위험", "market_score": 32, "pattern_score": 42, "entry_zone_label": "650.0 ~ 660.0"},
            {"ticker": "SPY", "action": "관찰", "status_label": "관찰", "market_score": 32, "pattern_score": 50},
        ],
    }


def _crypto_snapshot():
    return {
        "generated_at": "2026-05-07T22:00:00+00:00",
        "llm_market_view": {"bias": "bearish", "summary": "전쟁 확산과 위험회피로 BTC 변동성 확대"},
        "paper_portfolio": {"return_label": "-1.20%", "profit_status_label": "손실 중"},
        "rows": [
            {"ticker": "BTC-USD", "signal_score": 38, "action": "회피", "strategy_commentary": "전쟁 뉴스 이후 risk-off"},
            {"ticker": "ETH-USD", "signal_score": 42, "action": "관망"},
        ],
    }


def test_build_cross_market_context_reuses_shared_market_state_for_next_session_decisions():
    context = build_cross_market_context(
        _stock_snapshot(),
        _crypto_snapshot(),
        hourly_report={"report_stage": "KRX 시작 전", "report_purpose": "전쟁 리스크 반영"},
    )

    assert context["available"] is True
    assert context["summary"]["regime"] == "risk_off"
    assert context["summary"]["primary_action"] == "신규 진입 축소"
    assert any(agent["id"] == "us_lead_market_agent" for agent in context["agent_blueprint"])
    assert any(agent["id"] == "korea_opening_agent" for agent in context["agent_blueprint"])
    assert any(link["id"] == "nasdaq_to_kospi" for link in context["cross_market_links"])
    assert any(link["id"] == "geopolitical_cross_asset" for link in context["cross_market_links"])
    nasdaq_link = next(link for link in context["cross_market_links"] if link["id"] == "nasdaq_to_kospi")
    assert "나스닥 선행 신호" in nasdaq_link["title"]
    assert "코스피" in nasdaq_link["implication"]
    crypto_market = next(market for market in context["market_contexts"] if market["id"] == "crypto")
    assert crypto_market["bias"] == "bearish"
    assert crypto_market["score"] == 40


def test_dashboard_exposes_cross_market_context_api_and_summary_panel(tmp_path):
    (tmp_path / "signal_snapshot.json").write_text(json.dumps(_stock_snapshot(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "crypto_signal_snapshot.json").write_text(json.dumps(_crypto_snapshot(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "hourly_report.json").write_text(json.dumps({"report_stage": "KRX 시작 전", "report_purpose": "전쟁 리스크 반영"}, ensure_ascii=False), encoding="utf-8")
    client = TestClient(create_dashboard_app(tmp_path))

    api_response = client.get("/api/market-context")
    summary_response = client.get("/dashboards/summary")
    detail_response = client.get("/market-context")

    assert api_response.status_code == 200
    assert api_response.json()["summary"]["primary_action"] == "신규 진입 축소"
    assert summary_response.status_code == 200
    assert detail_response.status_code == 200
    assert "시장 연결 인텔리전스" in summary_response.text
    assert "나스닥 선행 신호 → 코스피 개장 체크" in summary_response.text
    assert "지정학 리스크 자산군 영향" in summary_response.text
    assert "Cross-Market Context Agent" in detail_response.text
    assert "Global Macro Synthesizer" in detail_response.text
    assert "시장별 연결 맵" in detail_response.text


def test_cross_market_context_is_persisted_in_sqlite_not_managed_as_single_json(tmp_path):
    repo = AnalysisRepository(tmp_path)
    context = build_cross_market_context(_stock_snapshot(), _crypto_snapshot())

    repo.save_cross_market_context(context, source_updated_at="123.0")
    loaded = repo.get_cross_market_context()

    assert loaded is not None
    assert loaded["state_persistence"] == "sqlite"
    assert loaded["source_updated_at"] == "123.0"
    assert loaded["summary"]["title"] == "시장 연결 인텔리전스"

    client = TestClient(create_dashboard_app(tmp_path))
    response = client.get("/api/market-context")

    assert response.status_code == 200
    assert response.json()["state_persistence"] == "sqlite"
