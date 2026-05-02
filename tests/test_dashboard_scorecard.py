from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradingagents.dashboard.extract import build_analysis_record
from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.storage import AnalysisRepository


@pytest.mark.unit
def test_build_analysis_record_scores_macro_fundamentals_and_entry_timing(sample_final_state):
    final_state = dict(sample_final_state)
    final_state["company_of_interest"] = "005930.KS"
    final_state["trade_date"] = "2026-03-01"
    final_state["news_report"] = (
        "시장 공통 뉴스: 중동 전쟁 확산으로 유가 상승과 인플레이션 압력이 커지고, "
        "금리 인하 기대가 후퇴하며 위험자산 회피가 나타날 수 있다. "
        "동시에 AI 인프라 투자는 반도체 섹터에 우호적이다."
    )
    final_state["fundamentals_report"] = (
        "최근 분기 매출 93.84조, 영업이익 20.07조, 순이익 19.29조. "
        "영업이익률 21.32%, ROE 10.78%, forward PER 5.41, PEG 0.17로 저평가 매력이 있다."
    )
    final_state["market_report"] = (
        "현재가 216052.81, 10 EMA 196119.04, 50 SMA 149205.87. "
        "RSI 83.66, MACD 17444.82, MACD 히스토그램 3412.47, ATR 8008.87, 볼린저 상단 상회."
    )
    final_state["trader_investment_decision"] = (
        "**Action**: Hold\n\n신규 자금은 대기하고 눌림목 확인 전까지는 비중을 급히 늘리지 않는다."
    )
    final_state["risk_debate_state"] = {
        **final_state["risk_debate_state"],
        "judge_decision": "**Rating**: Overweight\n\n**Executive Summary**: 중기 방향성은 좋지만 단기 진입 타이밍은 나쁘다.",
    }

    record = build_analysis_record(final_state, generated_at="2026-03-01T00:00:00+00:00")

    scorecard = record["decision_scorecard"]
    assert scorecard["available"] is True
    assert scorecard["direction_score"] > 0
    assert scorecard["entry_timing_score"] < 0
    assert scorecard["market_risk_score"] < 0
    assert scorecard["final_action_label"] == "시장 리스크로 신규 매수 보류"

    factors = {factor["id"]: factor for factor in scorecard["factors"]}
    assert factors["market_common_risk"]["score"] < 0
    assert factors["market_common_risk"]["scope"] == "시장 공통"
    assert any("전쟁" in evidence for evidence in factors["market_common_risk"]["evidence"])
    assert factors["fundamentals"]["score"] > 0
    assert factors["entry_timing"]["score"] < 0

    assert scorecard["positive_factors"][0]["score"] > 0
    assert scorecard["negative_factors"][0]["score"] < 0


@pytest.mark.unit
def test_market_common_score_ignores_stock_specific_and_negated_risk_phrases():
    from tradingagents.dashboard.scorecard import build_decision_scorecard

    news_report = (
        "시장 공통 뉴스: 중동 전쟁 확산과 유가 상승. "
        "종목별 뉴스: 해당 기업은 관세 영향이 미미하고 수출 규제 리스크 없음. 전쟁 관련 직접 악재 없음."
    )
    record = {"reports": {"news_report": news_report}}

    scorecard = build_decision_scorecard(record)
    factors = {factor["id"]: factor for factor in scorecard["factors"]}

    assert factors["market_common_risk"]["score"] < 0
    assert any(event["label"] == "전쟁/지정학 리스크" for event in scorecard["global_events"])
    assert not any(event["label"] == "관세 리스크" for event in scorecard["global_events"])
    assert factors["stock_news"]["score"] >= 0
    assert "악재 요인이 부담" not in factors["stock_news"]["reason"]


@pytest.mark.unit
def test_market_common_score_parses_markdown_tables_and_headings():
    from tradingagents.dashboard.scorecard import build_decision_scorecard

    table_record = {
        "reports": {
            "news_report": (
                "| scope | news | impact |\n"
                "| stock_specific | 해당 기업은 관세 부담과 수출 규제 우려, 전쟁 관련 악재가 있음 | negative |\n"
                "| global_market | 특별한 리스크 없음 | neutral |"
            )
        }
    }
    table_scorecard = build_decision_scorecard(table_record)
    table_factors = {factor["id"]: factor for factor in table_scorecard["factors"]}
    assert table_factors["market_common_risk"]["score"] == 0
    assert table_scorecard["global_events"] == []

    heading_record = {
        "reports": {
            "news_report": (
                "## global_market\n"
                "중동 전쟁 확산과 유가 상승.\n"
                "## stock_specific:\n"
                "관세 영향 미미, 수출 규제 리스크 없음."
            )
        }
    }
    heading_scorecard = build_decision_scorecard(heading_record)
    heading_factors = {factor["id"]: factor for factor in heading_scorecard["factors"]}
    assert heading_factors["market_common_risk"]["score"] < 0
    assert any(event["label"] == "전쟁/지정학 리스크" for event in heading_scorecard["global_events"])


@pytest.mark.unit
def test_dashboard_detail_renders_decision_scorecard_and_api(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    sample_record = dict(sample_record)
    sample_record["decision_scorecard"] = {
        "available": True,
        "direction_score": 38,
        "direction_label": "긍정",
        "entry_timing_score": -42,
        "entry_timing_label": "부정",
        "market_risk_score": -35,
        "market_risk_label": "부정",
        "final_action_label": "신규 매수 대기",
        "summary": "중기 방향성은 긍정이지만 전쟁·유가 리스크와 진입 과열로 매수 대기가 우세합니다.",
        "factors": [
            {"id": "market_common_risk", "label": "시장 공통 리스크", "score": -35, "score_label": "부정", "tone": "negative", "scope": "시장 공통", "confidence": 0.76, "reason": "전쟁과 유가 상승으로 위험자산 선호가 약화될 수 있음", "evidence": ["중동 전쟁 확산", "유가 상승"]},
            {"id": "fundamentals", "label": "기업 펀더멘털", "score": 55, "score_label": "긍정", "tone": "positive", "scope": "종목", "confidence": 0.72, "reason": "실적과 밸류에이션이 우호적", "evidence": ["영업이익률 21.32%", "PER 5.41"]},
            {"id": "entry_timing", "label": "진입 타이밍", "score": -45, "score_label": "부정", "tone": "negative", "scope": "종목", "confidence": 0.84, "reason": "RSI 과열과 이동평균 이격이 큼", "evidence": ["RSI 83.66", "10 EMA 이격"]},
        ],
        "positive_factors": [
            {"label": "기업 펀더멘털", "score": 55, "reason": "실적과 밸류에이션이 우호적"}
        ],
        "negative_factors": [
            {"label": "진입 타이밍", "score": -45, "reason": "RSI 과열과 이동평균 이격이 큼"},
            {"label": "시장 공통 리스크", "score": -35, "reason": "전쟁과 유가 상승으로 위험자산 선호가 약화될 수 있음"},
        ],
        "global_events": [
            {"label": "중동 전쟁 확산", "score": -30, "score_label": "부정", "tone": "negative", "reason": "유가 상승과 위험회피 가능성", "scope": "시장 공통"}
        ],
    }

    def fake_get_price_chart(ticker, trade_date, lookback_days=180):
        return {"available": False, "reason": "test"}

    monkeypatch.setattr(dashboard_app, "get_price_chart", fake_get_price_chart)
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "판단 점수판" in response.text
    assert "중기 방향성" in response.text
    assert "+38" in response.text
    assert "현재 진입 타이밍" in response.text
    assert "-42" in response.text
    assert "시장 공통 리스크" in response.text
    assert "중동 전쟁 확산" in response.text
    assert "긍정 근거 TOP" in response.text
    assert "부정 근거 TOP" in response.text
    assert "신규 매수 대기" in response.text
    assert "market_common_risk" not in response.text

    api_response = client.get(f"/api/runs/{stored['run_id']}/scorecard")
    assert api_response.status_code == 200
    payload = api_response.json()
    assert payload["direction_score"] == 38
    assert payload["global_events"][0]["label"] == "중동 전쟁 확산"


@pytest.mark.unit
def test_dashboard_scorecard_api_computes_legacy_records_on_read(tmp_path, sample_record):
    legacy_record = dict(sample_record)
    legacy_record.pop("decision_scorecard", None)
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(legacy_record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/api/runs/{stored['run_id']}/scorecard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert "direction_score" in payload
