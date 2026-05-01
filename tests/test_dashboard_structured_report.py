from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.reporting import (
    STRUCTURED_REPORT_VERSION,
    build_structured_report,
    structure_and_verify_report,
    verify_structured_report,
)
from tradingagents.dashboard.storage import AnalysisRepository


@pytest.mark.unit
def test_verify_structured_report_fails_closed_for_malformed_structured_input(sample_record):
    verification = verify_structured_report(sample_record, None)  # type: ignore[arg-type]

    assert verification["status"] == "fail"
    assert verification["rewrite_required"] is True
    assert any(issue["type"] == "schema_mismatch" for issue in verification["issues"])


@pytest.mark.unit
def test_dashboard_detail_escapes_structured_report_text(tmp_path, sample_record):
    malicious = build_structured_report(sample_record)
    malicious["decision_cards"][0]["description"] = '<script>alert("xss")</script>'
    sample_record["structured_report"] = malicious
    sample_record["structured_report_verification"] = {"status": "pass", "score": 1.0, "issues": [], "rewrite_required": False}
    sample_record["structured_report_verified"] = True
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert '<script>alert("xss")</script>' not in response.text
    assert '&lt;script&gt;alert(&#34;xss&#34;)&lt;/script&gt;' in response.text


@pytest.mark.unit
def test_build_structured_report_preserves_original_decision_and_adds_style_tokens(sample_record):
    structured = build_structured_report(sample_record)

    assert structured["schema_version"] == STRUCTURED_REPORT_VERSION
    assert structured["executive_summary"]["headline"] == sample_record["decision_summary"]
    assert structured["executive_summary"]["rating"] == sample_record["rating"]
    assert structured["executive_summary"]["trader_action"] == sample_record["trader_action"]
    assert structured["executive_summary"]["tone"] == "neutral"
    assert structured["decision_cards"][0] == {
        "title": "최종 판단",
        "value": sample_record["rating"],
        "tone": "neutral",
        "description": sample_record["decision_summary"],
    }
    assert {factor["tone"] for factor in structured["positive_factors"]} <= {"positive", "neutral"}
    assert any(factor["tone"] in {"warning", "negative"} for factor in structured["risk_factors"])
    assert structured["detailed_sections"][0]["id"] == "market_report"
    assert structured["detailed_sections"][0]["source_refs"] == ["market_report"]
    assert structured["detailed_sections"][0]["summary_bullets"]


@pytest.mark.unit
def test_verify_structured_report_fails_when_summary_distorts_rating(sample_record):
    structured = build_structured_report(sample_record)
    structured["executive_summary"]["headline"] = "Strong Buy가 확실하며 목표가 999달러까지 상승 여력이 큽니다."
    structured["executive_summary"]["rating"] = "Strong Buy"

    verification = verify_structured_report(sample_record, structured)

    assert verification["status"] == "fail"
    assert verification["rewrite_required"] is True
    assert verification["score"] < 0.8
    assert {issue["type"] for issue in verification["issues"]} >= {"decision_changed", "unsupported_claim"}


@pytest.mark.unit
def test_verify_structured_report_fails_closed_for_malformed_nested_fields(sample_record):
    structured = build_structured_report(sample_record)
    structured["detailed_sections"] = 123

    verification = verify_structured_report(sample_record, structured)

    assert verification["status"] == "fail"
    assert any(issue["type"] == "schema_mismatch" for issue in verification["issues"])


@pytest.mark.unit
def test_structure_and_verify_report_repairs_non_dict_first_draft(sample_record):
    def malformed_builder(record):
        return ["not", "a", "dict"]  # type: ignore[return-value]

    result = structure_and_verify_report(sample_record, builder=malformed_builder, max_attempts=2)

    assert result["structured_report_verification"]["status"] == "pass"
    assert result["structured_report"]["executive_summary"]["rating"] == sample_record["rating"]
    assert result["structured_report_attempts"] == 2


@pytest.mark.unit
def test_verify_structured_report_rejects_qualitative_text_not_supported_by_source(sample_record):
    structured = build_structured_report(sample_record)
    structured["decision_cards"][1]["description"] = "대규모 자사주 매입과 신규 정부 보조금이 확정되어 매수 근거가 강화되었습니다."

    verification = verify_structured_report(sample_record, structured)

    assert verification["status"] == "fail"
    assert any(issue["type"] == "unsupported_text" for issue in verification["issues"])


@pytest.mark.unit
@pytest.mark.parametrize("decision_cards", [None, []])
def test_verify_structured_report_requires_canonical_decision_cards(sample_record, decision_cards):
    structured = build_structured_report(sample_record)
    if decision_cards is None:
        structured.pop("decision_cards")
    else:
        structured["decision_cards"] = decision_cards

    verification = verify_structured_report(sample_record, structured)

    assert verification["status"] == "fail"
    assert any(issue["type"] == "missing_decision_field" and issue["field"] == "decision_cards" for issue in verification["issues"])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("card_index", "record_key", "distorted_value"),
    [
        (0, "rating", "Buy"),
        (1, "trader_action", "Sell"),
        (2, "research_recommendation", "Strong Buy"),
    ],
)
def test_verify_structured_report_rejects_distorted_decision_card_values(sample_record, card_index, record_key, distorted_value):
    structured = build_structured_report(sample_record)
    assert structured["decision_cards"][card_index]["value"] == sample_record[record_key]
    structured["decision_cards"][card_index]["value"] = distorted_value

    verification = verify_structured_report(sample_record, structured)

    assert verification["status"] == "fail"
    assert any(issue["type"] == "decision_changed" and issue["field"] == f"decision_cards[{card_index}].value" for issue in verification["issues"])


@pytest.mark.unit
def test_structure_and_verify_report_repairs_distorted_decision_card_values(sample_record):
    def distorted_builder(record):
        structured = build_structured_report(record)
        structured["decision_cards"][0]["value"] = "Buy"
        structured["decision_cards"][1]["value"] = "Sell"
        structured["decision_cards"][2]["value"] = "Strong Buy"
        return structured

    result = structure_and_verify_report(sample_record, builder=distorted_builder, max_attempts=2)

    assert result["structured_report_verification"]["status"] == "pass"
    assert result["structured_report"]["decision_cards"][0]["value"] == sample_record["rating"]
    assert result["structured_report"]["decision_cards"][1]["value"] == sample_record["trader_action"]
    assert result["structured_report"]["decision_cards"][2]["value"] == sample_record["research_recommendation"]
    assert result["structured_report_attempts"] == 2


@pytest.mark.unit
def test_verify_structured_report_requires_decision_fields(sample_record):
    structured = build_structured_report(sample_record)
    structured["executive_summary"].pop("rating")

    verification = verify_structured_report(sample_record, structured)

    assert verification["status"] == "fail"
    assert any(issue["type"] == "missing_decision_field" for issue in verification["issues"])


@pytest.mark.unit
def test_verify_structured_report_rejects_unsupported_claims_outside_headline(sample_record):
    structured = build_structured_report(sample_record)
    structured["decision_cards"][1]["description"] = "목표가 999달러와 확실한 Strong Buy가 원문 없이 추가되었습니다."
    structured["action_strategy"]["text"] = "보장된 50% 수익을 기대합니다."
    structured["detailed_sections"][0]["summary_bullets"][0]["text"] = "원문에 없는 목표가 999달러"

    verification = verify_structured_report(sample_record, structured)

    assert verification["status"] == "fail"
    assert any(issue["type"] == "unsupported_claim" for issue in verification["issues"])
    assert any(issue["field"].startswith("decision_cards") for issue in verification["issues"])


@pytest.mark.unit
def test_build_structured_report_does_not_invent_risk_for_neutral_report(sample_record):
    sample_record = dict(sample_record)
    sample_record["reports"] = {
        "market_report": "가격은 전일과 유사하게 움직였습니다.",
        "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: 관망합니다.",
    }
    sample_record["decision_summary"] = "관망합니다."
    sample_record["investment_thesis"] = ""
    structured = build_structured_report(sample_record)

    assert structured["risk_factors"] == []
    assert structured["decision_cards"][3]["value"] == "제한적"
    assert structured["decision_cards"][3]["tone"] == "neutral"


@pytest.mark.unit
def test_structure_and_verify_report_replaces_distorted_detailed_sections_on_repair(sample_record):
    def distorted_builder(record):
        structured = build_structured_report(record)
        structured["detailed_sections"][0]["detail"] = "목표가 999달러와 확실한 수익을 보장합니다."
        return structured

    result = structure_and_verify_report(sample_record, builder=distorted_builder, max_attempts=2)

    assert result["structured_report_verification"]["status"] == "pass"
    assert result["structured_report"]["detailed_sections"][0]["detail"] == sample_record["reports"]["market_report"]
    assert result["structured_report_attempts"] == 2


@pytest.mark.unit
def test_structure_and_verify_report_rewrites_failed_first_draft(sample_record):
    def distorted_builder(record):
        structured = build_structured_report(record)
        structured["executive_summary"]["headline"] = "Strong Buy가 확실하며 목표가 999달러까지 상승 여력이 큽니다."
        structured["executive_summary"]["rating"] = "Strong Buy"
        return structured

    result = structure_and_verify_report(sample_record, builder=distorted_builder, max_attempts=2)

    assert result["structured_report_verification"]["status"] == "pass"
    assert result["structured_report"]["executive_summary"]["rating"] == sample_record["rating"]
    assert result["structured_report"]["executive_summary"]["headline"] == sample_record["decision_summary"]
    assert result["structured_report_attempts"] == 2


@pytest.mark.unit
def test_build_analysis_record_stores_verified_structured_report(sample_final_state):
    from tradingagents.dashboard.extract import build_analysis_record

    record = build_analysis_record(sample_final_state, generated_at="2026-04-29T23:00:00+00:00")

    assert record["structured_report"]["schema_version"] == STRUCTURED_REPORT_VERSION
    assert record["structured_report_verification"]["status"] == "pass"
    assert record["structured_report_verified"] is True


@pytest.mark.unit
def test_dashboard_detail_prefers_verified_structured_report_ui(tmp_path, sample_record):
    sample_record.update(structure_and_verify_report(sample_record))
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "검증된 구조화 리포트" in response.text
    assert "긍정 근거" in response.text
    assert "주의 요인" in response.text
    assert "실행 전략" in response.text
    assert "data-tone=\"warning\"" in response.text or "data-tone=\"negative\"" in response.text
    assert "근거: 시장 분석" in response.text
    assert "근거: market_report" not in response.text


@pytest.mark.unit
def test_dashboard_detail_falls_back_to_raw_report_when_structured_report_unverified(tmp_path, sample_record):
    sample_record["structured_report"] = build_structured_report(sample_record)
    sample_record["structured_report_verification"] = {
        "status": "fail",
        "score": 0.4,
        "issues": [{"severity": "high", "type": "unsupported_claim", "field": "executive_summary.headline", "message": "왜곡"}],
        "rewrite_required": True,
    }
    sample_record["structured_report_verified"] = False
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "원문 리포트 기반 보기" in response.text
    assert "구조화 검증 실패" in response.text
    assert "Maintain the current NVDA position" in response.text
