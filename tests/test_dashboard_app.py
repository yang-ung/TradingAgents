from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.extract import build_analysis_record
from tradingagents.dashboard.storage import AnalysisRepository


@pytest.mark.unit
def test_dashboard_home_lists_saved_runs(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)
    app = create_dashboard_app(tmp_path)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "NVDA" in response.text
    assert stored["decision_summary"] in response.text


@pytest.mark.unit
def test_dashboard_home_has_commercial_operating_surface(tmp_path, sample_record, sample_final_state):
    repository = AnalysisRepository(tmp_path)
    repository.save(sample_record)
    second_state = dict(sample_final_state)
    second_state["company_of_interest"] = "005930.KS"
    second_state["trade_date"] = "2026-04-30"
    second_state["final_trade_decision"] = "**Rating**: Buy\n\n**Executive Summary**: 메모리 회복과 HBM 수요가 긍정적입니다."
    second_state["risk_debate_state"] = dict(sample_final_state["risk_debate_state"])
    second_state["risk_debate_state"]["judge_decision"] = second_state["final_trade_decision"]
    repository.save(
        build_analysis_record(
            second_state,
            generated_at="2026-05-01T00:00:00+09:00",
            raw_log_path="artifacts/dashboard/raw_results/005930/full_states_log_2026-04-30.json",
        )
    )
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get("/")
    list_response = client.get("/dashboards/kospi")

    assert response.status_code == 200
    assert list_response.status_code == 200
    assert "TradingAgents 투자 리서치" in response.text
    assert "오늘의 투자 판단" in response.text
    assert "긴 분석 리포트를 실행 가능한 투자 판단으로 압축합니다." not in response.text
    assert "운영 인사이트" not in response.text
    assert "서비스 상태" not in response.text
    assert "검증 리포트" not in response.text
    assert "저장된 리포트" not in response.text
    assert "한국 시장" not in response.text
    assert "최근 업데이트" not in response.text
    assert "market-overview" in response.text
    assert "run-card-list" in response.text
    assert "collapsible-filter" in list_response.text
    assert "mobile-run-carousel" in list_response.text
    assert "desktop-run-table" in list_response.text
    assert "삼성전자" in list_response.text
    assert "삼성전자 (005930.KS)" not in list_response.text
    assert ">005930.KS</strong>" not in list_response.text
    assert "모델</th>" not in list_response.text
    assert "quick_think_llm" not in list_response.text
    assert "Buy" in list_response.text
    assert "2026-05-01 00:00" in list_response.text
    assert "2026-05-01T00:00:00+09:00" not in list_response.text

    paginated_response = client.get("/dashboards/kospi?limit=1")
    assert paginated_response.status_code == 200
    assert "Buy" in paginated_response.text


@pytest.mark.unit
def test_dashboard_home_requires_passed_structured_verification(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)
    payload = dict(stored)
    payload["structured_report_verified"] = True
    payload["structured_report_verification"] = {"status": "fail", "issues": ["distorted"]}
    with repository._connect() as connection:
        connection.execute(
            "UPDATE analyses SET payload_json = ? WHERE run_id = ?",
            (json.dumps(payload), stored["run_id"]),
        )
        connection.commit()
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get("/")

    assert response.status_code == 200
    assert '<span class="pill verified">검증 리포트</span>' not in response.text
    assert "검증 리포트" not in response.text
    assert "원문" not in response.text


@pytest.mark.unit
def test_dashboard_detail_page_renders_reports(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)
    app = create_dashboard_app(tmp_path)
    client = TestClient(app)

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "최종 포트폴리오 결정" in response.text
    assert "Maintain the current NVDA position" in response.text


@pytest.mark.unit
def test_dashboard_analysis_list_is_profit_focused_without_model_metadata(tmp_path, sample_record, monkeypatch):
    import tradingagents.dashboard.app as dashboard_app

    repository = AnalysisRepository(tmp_path)
    record = dict(sample_record)
    record["ticker"] = "000660.KS"
    record["trade_date"] = "2026-04-01"
    record["decision_summary"] = "000660.KS는 HBM 수요가 우호적이지만 단기 변동성이 있습니다."
    record["strategy_spec"] = {
        "strategy_id": "sk-2026-04-30",
        "ticker": "000660.KS",
        "trade_date": "2026-04-01",
        "side": "long",
        "entry": {"type": "price_zone", "low": 100, "high": 105},
        "take_profit": {"type": "fixed_price", "price": 120},
        "stop_loss": {"type": "fixed_price", "price": 95},
    }
    record["metadata"] = {"quick_think_llm": "gpt-4o-mini", "deep_think_llm": "o3-mini"}
    repository.save(record)

    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, **kwargs: {
            "available": True,
            "currency": "KRW",
            "points": [
                {"date": "2026-04-01", "open": 100, "high": 106, "low": 99, "close": 104},
                {"date": "2026-04-02", "open": 104, "high": 121, "low": 103, "close": 120},
            ],
            "metadata": {"first_close": 104, "latest_close": 120, "change_percent": 15.38, "currency": "KRW"},
        },
    )
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get("/dashboards/kospi")

    assert response.status_code == 200
    assert "분석 실행 목록" in response.text
    assert "SK하이닉스" in response.text
    assert "000660.KS" not in response.text
    assert "000660" not in response.text
    assert "모델" not in response.text
    assert "gpt-4o-mini" not in response.text
    assert "o3-mini" not in response.text
    assert "전략 수익률" in response.text
    assert "+15.38%" in response.text
    assert "1억원 손익" in response.text
    assert "HBM 수요가 우호적" in response.text


@pytest.mark.unit
def test_dashboard_replaces_known_ticker_codes_in_user_facing_summaries(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    record = dict(sample_record)
    record["ticker"] = "000660.KS"
    record["decision_summary"] = "000660.KS는 중기 방향성이 우호적이지만 단기 진입 타이밍은 과열되었습니다."
    record["decision_scorecard"] = {
        "available": True,
        "direction_score": 42,
        "direction_label": "긍정",
        "entry_timing_score": -32,
        "entry_timing_label": "부정",
        "market_risk_score": 0,
        "market_risk_label": "중립",
        "final_action_label": "신규 매수 대기",
        "summary": "000660.KS는 중기 방향성이 우호적이지만 단기 진입 타이밍은 과열되었습니다.",
        "factors": [],
        "positive_factors": [{"label": "기업 펀더멘털", "score": 42, "reason": "000660.KS 실적 개선", "score_label": "긍정", "tone": "positive"}],
        "negative_factors": [],
        "global_events": [],
    }
    stored = repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    home_response = client.get("/")
    detail_response = client.get(f"/runs/{stored['run_id']}")

    assert home_response.status_code == 200
    assert detail_response.status_code == 200
    assert "SK하이닉스는 중기 방향성이 우호적" in home_response.text
    assert "SK하이닉스는 중기 방향성이 우호적" in detail_response.text
    assert "SK하이닉스 실적 개선" in detail_response.text
    assert "000660.KS는 중기 방향성이" not in home_response.text
    assert "000660.KS는 중기 방향성이" not in detail_response.text


@pytest.mark.unit
def test_dashboard_ticker_replacement_uses_readable_korean_particles(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    record = dict(sample_record)
    record["ticker"] = "068270.KS"
    record["decision_summary"] = "068270.KS는 수급이 안정적이고 068270.KS를 관심 종목으로 봅니다."
    repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get("/")

    assert response.status_code == 200
    assert "셀트리온은 수급이 안정적" in response.text
    assert "셀트리온을 관심 종목" in response.text
    assert "셀트리온는" not in response.text


@pytest.mark.unit
def test_dashboard_localizes_technical_indicator_acronyms_in_visible_ui(tmp_path, sample_record, monkeypatch):
    import tradingagents.dashboard.app as dashboard_app

    repository = AnalysisRepository(tmp_path)
    record = dict(sample_record)
    record["decision_summary"] = "RSI 과열, ATR 확대, 50 SMA 이탈 전까지 관망합니다."
    record["investment_thesis"] = "MACD 개선과 20 EMA 지지 여부를 확인합니다."
    record["reports"] = {
        "market_report": "RSI 83.66, MACD 히스토그램 양호, ATR 8008.87, 50 SMA 근처입니다.",
        "final_trade_decision": "10 EMA 위에서는 보유, SMA 50 이탈 시 감축합니다.",
    }
    stored = repository.save(record)
    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, **kwargs: {
            "available": True,
            "currency": "USD",
            "latest_close": 100,
            "change": 1,
            "change_percent": 1,
            "positive": True,
            "start_date": "2026-01-01",
            "end_date": "2026-05-01",
            "max_close": 110,
            "min_close": 90,
            "width": 640,
            "height": 240,
            "area_path": "",
            "path": "",
            "data_source": "테스트",
            "candles": [{"time": "2026-05-01", "open": 99, "high": 101, "low": 98, "close": 100}],
            "volume": [],
            "moving_averages": {},
        },
    )
    client = TestClient(create_dashboard_app(tmp_path))

    list_response = client.get("/dashboards/nasdaq")
    detail_response = client.get(f"/runs/{stored['run_id']}")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert "상대강도지수 과열" in list_response.text
    assert "평균변동폭 확대" in list_response.text
    assert "50일 단순이동평균 이탈" in list_response.text
    assert "이동평균 수렴·확산 지표 히스토그램" in detail_response.text
    assert "10일 지수이동평균 위에서는 보유" in detail_response.text
    assert "5일 이동평균" in detail_response.text
    assert "20일 이동평균" in detail_response.text
    assert "60일 이동평균" in detail_response.text
    visible_fragments = list_response.text + detail_response.text
    assert "RSI 과열" not in visible_fragments
    assert "ATR 확대" not in visible_fragments
    assert "50 SMA" not in visible_fragments
    assert "10 EMA" not in visible_fragments
    assert "MA5" not in visible_fragments
