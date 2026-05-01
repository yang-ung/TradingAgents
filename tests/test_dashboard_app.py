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

    assert response.status_code == 200
    assert "투자 리서치 운영센터" in response.text
    assert "핵심 정보" in response.text
    assert "오늘의 투자 판단" in response.text
    assert "긴 분석 리포트를 실행 가능한 투자 판단으로 압축합니다." not in response.text
    assert "운영 인사이트" in response.text
    assert "서비스 상태" in response.text
    assert "검증 리포트" in response.text
    assert "market-overview" in response.text
    assert "run-card-list" in response.text
    assert "전체 분석" in response.text
    assert "한국 시장" in response.text
    assert "최근 업데이트" in response.text
    assert "삼성전자 (005930.KS)" in response.text
    assert ">005930.KS</strong>" not in response.text
    assert "Buy" in response.text
    assert "Hold" in response.text
    assert "100%" in response.text
    assert "2026-05-01 00:00" in response.text
    assert "2026-05-01T00:00:00+09:00" not in response.text

    paginated_response = client.get("/?limit=1")
    assert paginated_response.status_code == 200
    assert "Buy" in paginated_response.text
    assert "Hold" in paginated_response.text
    assert "2건" in paginated_response.text


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
    assert "0%" in response.text
    assert '<span class="pill verified">검증 리포트</span>' not in response.text
    assert "원문" in response.text


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
