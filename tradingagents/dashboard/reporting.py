from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Callable, Dict, Iterable, List

from .extract import extract_markdown_label, make_snippet
from .models import AnalysisRecord

STRUCTURED_REPORT_VERSION = "structured-report-v1"

_SECTION_TITLES = {
    "market_report": "시장 분석",
    "sentiment_report": "심리 분석",
    "news_report": "뉴스/공시",
    "fundamentals_report": "펀더멘털",
    "investment_plan": "리서치 매니저",
    "trader_investment_decision": "트레이더 제안",
    "final_trade_decision": "최종 포트폴리오 결정",
    "bull_history": "강세 논리",
    "bear_history": "약세 논리",
    "research_manager_decision": "리서치 매니저 결정",
    "aggressive_history": "공격적 리스크 의견",
    "conservative_history": "보수적 리스크 의견",
    "neutral_history": "중립 리스크 의견",
    "portfolio_manager_decision": "포트폴리오 매니저 결정",
}
_POSITIVE_WORDS = (
    "buy",
    "overweight",
    "growth",
    "improved",
    "improve",
    "recovered",
    "strong",
    "exceptional",
    "intact",
    "add",
    "개선",
    "강세",
    "성장",
    "매수",
    "긍정",
)
_NEGATIVE_WORDS = (
    "sell",
    "underweight",
    "risk",
    "caution",
    "scrutiny",
    "valuation",
    "disappointment",
    "do not chase",
    "avoid",
    "리스크",
    "주의",
    "과열",
    "조정",
    "부정",
    "악화",
)
_DECISION_WORDS = {
    "strong buy",
    "buy",
    "overweight",
    "hold",
    "neutral",
    "sell",
    "underweight",
    "strong sell",
}
_UNSUPPORTED_CLAIM_RE = re.compile(
    r"(목표가\s*\d+|\d+(?:\.\d+)?\s*(?:달러|원|%|배)|확실|보장|guarantee|certain|strong buy)",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")

StructuredBuilder = Callable[[AnalysisRecord], Dict[str, Any]]


def _tone_for_text(text: str, fallback: str = "neutral") -> str:
    normalized = (text or "").lower()
    has_positive = any(word in normalized for word in _POSITIVE_WORDS)
    has_negative = any(word in normalized for word in _NEGATIVE_WORDS)
    if has_positive and not has_negative:
        return "positive"
    if has_negative and not has_positive:
        return "warning"
    if has_positive and has_negative:
        return "mixed"
    return fallback


def _tone_for_decision(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"buy", "strong buy", "overweight", "outperform"}:
        return "positive"
    if normalized in {"sell", "strong sell", "underweight", "underperform"}:
        return "negative"
    return "neutral"


def _sentences(text: str, *, limit: int = 3) -> list[str]:
    cleaned = re.sub(r"[#*_`>-]", " ", text or "")
    parts = [re.sub(r"\s+", " ", part).strip(" -:") for part in _SENTENCE_RE.split(cleaned)]
    return [part for part in parts if len(part) >= 12][:limit]


def _factor(text: str, source: str, *, fallback_tone: str = "neutral") -> dict[str, Any]:
    return {
        "text": make_snippet(text, limit=180),
        "tone": _tone_for_text(text, fallback=fallback_tone),
        "source_refs": [source],
    }


def _collect_factors(reports: dict[str, str], *, wanted: str, limit: int = 4) -> list[dict[str, Any]]:
    factors: list[dict[str, Any]] = []
    for source, report in reports.items():
        for sentence in _sentences(report, limit=4):
            tone = _tone_for_text(sentence)
            if wanted == "positive" and tone in {"positive", "mixed"}:
                factor = _factor(sentence, source, fallback_tone="positive")
                if factor["tone"] == "mixed":
                    factor["tone"] = "positive"
                factors.append(factor)
            elif wanted == "risk" and tone in {"warning", "negative", "mixed"}:
                factor = _factor(sentence, source, fallback_tone="warning")
                if factor["tone"] == "mixed":
                    factor["tone"] = "warning"
                factors.append(factor)
            if len(factors) >= limit:
                return factors
    return factors


def _section_summary(source: str, text: str) -> dict[str, Any]:
    bullets = [_factor(sentence, source) for sentence in _sentences(text, limit=3)]
    if not bullets and text:
        bullets = [_factor(make_snippet(text, limit=180), source)]
    return {
        "id": source,
        "title": _SECTION_TITLES.get(source, source),
        "summary_bullets": bullets,
        "detail": text or "",
        "tone": _tone_for_text(text),
        "source_refs": [source],
    }


def build_structured_report(record: AnalysisRecord) -> dict[str, Any]:
    """Build a deterministic, source-preserving structured report for dashboard rendering.

    This is intentionally extractive and conservative: it restructures TradingAgents output
    into cards, factors, and style tokens without inventing new forecasts or changing the
    original investment decision.
    """
    reports = record.get("reports", {}) or {}
    rating = record.get("rating", "") or "-"
    trader_action = record.get("trader_action", "") or "-"
    recommendation = record.get("research_recommendation", "") or "-"
    decision_summary = record.get("decision_summary", "") or make_snippet(reports.get("final_trade_decision", ""))
    final_decision = reports.get("final_trade_decision", "") or ""
    thesis = record.get("investment_thesis", "") or extract_markdown_label(final_decision, "Investment Thesis")
    strategic_actions = extract_markdown_label(reports.get("investment_plan", ""), "Strategic Actions")
    if strategic_actions:
        action_strategy = strategic_actions
        action_source_refs = ["investment_plan"]
    elif reports.get("trader_investment_decision"):
        action_strategy = make_snippet(reports.get("trader_investment_decision", ""), limit=260)
        action_source_refs = ["trader_investment_decision"]
    else:
        action_strategy = make_snippet(final_decision, limit=260)
        action_source_refs = ["final_trade_decision"]

    rating_tone = _tone_for_decision(rating)
    action_tone = _tone_for_decision(trader_action)
    positive_factors = _collect_factors(reports, wanted="positive")
    risk_factors = _collect_factors(reports, wanted="risk")
    if not positive_factors and thesis:
        positive_factors = [_factor(thesis, "final_trade_decision", fallback_tone="neutral")]
    if not risk_factors and final_decision:
        # Do not invent risk when the source did not contain explicit risk language.
        # The UI can render a neutral/limited-risk card without adding unsupported content.
        risk_factors = []


    detailed_sections = [
        _section_summary(key, value)
        for key, value in reports.items()
        if value and key in _SECTION_TITLES
    ]

    return {
        "schema_version": STRUCTURED_REPORT_VERSION,
        "executive_summary": {
            "headline": decision_summary,
            "rating": rating,
            "trader_action": trader_action,
            "research_recommendation": recommendation,
            "tone": rating_tone,
            "risk_level": "medium" if risk_factors else "low",
            "confidence": "medium",
            "source_refs": ["final_trade_decision"],
        },
        "decision_cards": [
            {
                "title": "최종 판단",
                "value": rating,
                "tone": rating_tone,
                "description": decision_summary,
            },
            {
                "title": "트레이더 액션",
                "value": trader_action,
                "tone": action_tone,
                "description": make_snippet(reports.get("trader_investment_decision", ""), limit=180),
            },
            {
                "title": "리서치 의견",
                "value": recommendation,
                "tone": _tone_for_decision(recommendation),
                "description": make_snippet(reports.get("investment_plan", ""), limit=180),
            },
            {
                "title": "핵심 리스크",
                "value": "주의 필요" if risk_factors else "제한적",
                "tone": "warning" if risk_factors else "neutral",
                "description": risk_factors[0]["text"] if risk_factors else "원문에서 별도 리스크가 강하게 확인되지 않았습니다.",
            },
        ],
        "positive_factors": positive_factors[:4],
        "risk_factors": risk_factors[:4],
        "action_strategy": {
            "title": "실행 전략",
            "text": action_strategy,
            "tone": _tone_for_text(action_strategy),
            "source_refs": action_source_refs,
        },
        "detailed_sections": detailed_sections,
        "source_coverage": {
            "included_sections": [section["id"] for section in detailed_sections],
            "missing_sections": [key for key in _SECTION_TITLES if not reports.get(key)],
        },
        "style_tokens": {
            "positive": "강점/개선/매수 근거",
            "negative": "부정/악화 요인",
            "warning": "주의/리스크",
            "neutral": "중립/정보",
            "mixed": "긍정과 리스크 혼재",
        },
    }


def _raw_text(record: AnalysisRecord) -> str:
    return "\n".join((record.get("reports", {}) or {}).values()).lower()


def _issue(severity: str, issue_type: str, field: str, message: str, recommendation: str = "") -> dict[str, str]:
    data = {
        "severity": severity,
        "type": issue_type,
        "field": field,
        "message": message,
    }
    if recommendation:
        data["recommendation"] = recommendation
    return data


def _iter_structured_strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _iter_structured_strings(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_structured_strings(child, f"{path}[{index}]")


def _claim_supported(claim: str, raw_text: str) -> bool:
    claim_lower = claim.lower()
    return claim_lower in raw_text or claim_lower.replace(" ", "") in raw_text.replace(" ", "")


def _normalize_supported_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[#*_`>-]", " ", text or "")).strip().lower().rstrip("…")


def _source_supports_text(record: AnalysisRecord, text: str, refs: Iterable[str] | None = None) -> bool:
    normalized_text = _normalize_supported_text(text)
    if not normalized_text:
        return True
    reports = record.get("reports", {}) or {}
    source_text = "\n".join(reports.get(ref, "") for ref in refs or reports.keys())
    normalized_source = _normalize_supported_text(source_text)
    return normalized_text in normalized_source


def _as_list(value: Any, field: str, issues: list[dict[str, str]]) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    issues.append(_issue("high", "schema_mismatch", field, f"{field} 필드는 list 형태여야 합니다."))
    return []


def _as_source_refs(value: Any, field: str, issues: list[dict[str, str]]) -> list[str]:
    if isinstance(value, list) and all(isinstance(ref, str) for ref in value):
        return value
    issues.append(_issue("medium", "missing_source_ref", field, "근거 참조는 문자열 list여야 합니다."))
    return []


def _validate_supported_text(
    record: AnalysisRecord,
    issues: list[dict[str, str]],
    *,
    field: str,
    text: Any,
    refs: Iterable[str] | None = None,
) -> None:
    if not isinstance(text, str):
        issues.append(_issue("high", "schema_mismatch", field, f"{field} 필드는 문자열이어야 합니다."))
        return
    if not _source_supports_text(record, text, refs):
        issues.append(
            _issue(
                "high",
                "unsupported_text",
                field,
                "구조화 텍스트가 지정된 원문 근거에서 확인되지 않습니다.",
                "요약/카드/근거 문구는 원문에서 추출되거나 원문과 동일한 의미의 짧은 발췌여야 합니다.",
            )
        )


def verify_structured_report(record: AnalysisRecord, structured: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    raw_text = _raw_text(record)
    if not isinstance(structured, dict):
        return {
            "status": "fail",
            "score": 0.0,
            "issues": [
                _issue(
                    "high",
                    "schema_mismatch",
                    "structured_report",
                    "구조화 리포트는 JSON object/dict 형태여야 합니다.",
                )
            ],
            "rewrite_required": True,
        }
    executive = structured.get("executive_summary", {})
    if not isinstance(executive, dict):
        executive = {}
    if structured.get("schema_version") != STRUCTURED_REPORT_VERSION:
        issues.append(_issue("high", "schema_mismatch", "schema_version", "지원하지 않는 구조화 리포트 스키마입니다."))

    for field, record_key in (("rating", "rating"), ("trader_action", "trader_action"), ("research_recommendation", "research_recommendation")):
        expected = str(record.get(record_key, "") or "").strip().lower()
        actual_value = executive.get(field)
        actual = str(actual_value or "").strip().lower()
        if expected and not actual:
            issues.append(
                _issue(
                    "high",
                    "missing_decision_field",
                    f"executive_summary.{field}",
                    f"원문 {record_key}={record.get(record_key)}를 보존하는 필드가 누락되었습니다.",
                    "TradingAgents 원문의 판단 필드를 그대로 포함하세요.",
                )
            )
        elif expected and actual != expected:
            issues.append(
                _issue(
                    "high",
                    "decision_changed",
                    f"executive_summary.{field}",
                    f"원문 {record_key}={record.get(record_key)}와 구조화 결과 {actual}가 다릅니다.",
                    "TradingAgents 원문의 판단 필드를 그대로 사용하세요.",
                )
            )

    for path, text in _iter_structured_strings(structured):
        for match in _UNSUPPORTED_CLAIM_RE.finditer(text):
            if not _claim_supported(match.group(0), raw_text):
                issues.append(
                    _issue(
                        "high",
                        "unsupported_claim",
                        path,
                        f"원문에서 확인되지 않는 강한 표현 또는 수치가 추가되었습니다: {match.group(0)}",
                        "원문에 있는 표현만 사용하거나 표현 강도를 낮추세요.",
                    )
                )
                break

    valid_sources = set((record.get("reports", {}) or {}).keys())
    decision_cards = _as_list(structured.get("decision_cards"), "decision_cards", issues)
    if len(decision_cards) < 3:
        issues.append(
            _issue(
                "high",
                "missing_decision_field",
                "decision_cards",
                "최종 판단, 트레이더 액션, 리서치 의견을 표시하는 상단 의사결정 카드 3개가 필요합니다.",
                "decision_cards[0..2]에는 TradingAgents 원문 rating, trader_action, research_recommendation 값을 그대로 포함하세요.",
            )
        )
    decision_card_expectations = {
        0: "rating",
        1: "trader_action",
        2: "research_recommendation",
    }
    for index, card in enumerate(decision_cards):
        if not isinstance(card, dict):
            issues.append(_issue("high", "schema_mismatch", f"decision_cards[{index}]", "의사결정 카드는 object여야 합니다."))
            continue
        if index in decision_card_expectations:
            record_key = decision_card_expectations[index]
            expected = str(record.get(record_key, "") or "").strip().lower()
            actual_value = card.get("value")
            actual = str(actual_value or "").strip().lower()
            if expected and not actual:
                issues.append(
                    _issue(
                        "high",
                        "missing_decision_field",
                        f"decision_cards[{index}].value",
                        f"원문 {record_key}={record.get(record_key)}를 보존하는 의사결정 카드 값이 누락되었습니다.",
                        "상단 의사결정 카드는 TradingAgents 원문의 판단 값을 그대로 표시해야 합니다.",
                    )
                )
            elif expected and actual != expected:
                issues.append(
                    _issue(
                        "high",
                        "decision_changed",
                        f"decision_cards[{index}].value",
                        f"원문 {record_key}={record.get(record_key)}와 의사결정 카드 값 {actual_value}가 다릅니다.",
                        "상단 의사결정 카드는 TradingAgents 원문의 판단 값을 그대로 표시해야 합니다.",
                    )
                )
        description = card.get("description", "")
        if description and not str(description).startswith("원문에서 별도"):
            _validate_supported_text(record, issues, field=f"decision_cards[{index}].description", text=description)

    for group_name in ("positive_factors", "risk_factors"):
        for index, factor in enumerate(_as_list(structured.get(group_name), group_name, issues)):
            if not isinstance(factor, dict):
                issues.append(_issue("high", "schema_mismatch", f"{group_name}[{index}]", "근거 항목은 object여야 합니다."))
                continue
            refs = _as_source_refs(factor.get("source_refs"), f"{group_name}[{index}].source_refs", issues)
            if any(ref not in valid_sources for ref in refs):
                issues.append(_issue("medium", "invalid_source_ref", f"{group_name}[{index}].source_refs", "존재하지 않는 원문 섹션을 근거로 참조했습니다."))
            _validate_supported_text(record, issues, field=f"{group_name}[{index}].text", text=factor.get("text", ""), refs=refs)

    action_strategy = structured.get("action_strategy", {})
    if isinstance(action_strategy, dict):
        refs = _as_source_refs(action_strategy.get("source_refs"), "action_strategy.source_refs", issues)
        if any(ref not in valid_sources for ref in refs):
            issues.append(_issue("medium", "invalid_source_ref", "action_strategy.source_refs", "존재하지 않는 원문 섹션을 근거로 참조했습니다."))
        _validate_supported_text(record, issues, field="action_strategy.text", text=action_strategy.get("text", ""), refs=refs)
    else:
        issues.append(_issue("high", "schema_mismatch", "action_strategy", "action_strategy 필드는 object여야 합니다."))

    for section_index, section in enumerate(_as_list(structured.get("detailed_sections"), "detailed_sections", issues)):
        if not isinstance(section, dict):
            issues.append(_issue("high", "schema_mismatch", f"detailed_sections[{section_index}]", "상세 섹션은 object여야 합니다."))
            continue
        refs = _as_source_refs(section.get("source_refs"), f"detailed_sections[{section_index}].source_refs", issues)
        if not refs:
            continue
        if any(ref not in valid_sources for ref in refs):
            issues.append(_issue("medium", "invalid_source_ref", "detailed_sections", "존재하지 않는 원문 섹션을 근거로 참조했습니다."))
            continue
        if len(refs) == 1 and section.get("detail") != (record.get("reports", {}) or {}).get(refs[0], ""):
            issues.append(
                _issue(
                    "high",
                    "section_detail_changed",
                    f"detailed_sections.{section.get('id', '')}.detail",
                    "상세 섹션 원문이 원본 report 내용과 다릅니다.",
                    "상세 원문은 TradingAgents 원문을 그대로 유지하세요.",
                )
            )
        for bullet_index, bullet in enumerate(_as_list(section.get("summary_bullets"), f"detailed_sections[{section_index}].summary_bullets", issues)):
            if not isinstance(bullet, dict):
                issues.append(_issue("high", "schema_mismatch", f"detailed_sections[{section_index}].summary_bullets[{bullet_index}]", "요약 bullet은 object여야 합니다."))
                continue
            bullet_refs = _as_source_refs(bullet.get("source_refs"), f"detailed_sections[{section_index}].summary_bullets[{bullet_index}].source_refs", issues)
            if any(ref not in valid_sources for ref in bullet_refs):
                issues.append(_issue("medium", "invalid_source_ref", f"detailed_sections[{section_index}].summary_bullets[{bullet_index}].source_refs", "존재하지 않는 원문 섹션을 근거로 참조했습니다."))
            _validate_supported_text(record, issues, field=f"detailed_sections[{section_index}].summary_bullets[{bullet_index}].text", text=bullet.get("text", ""), refs=bullet_refs)

    if not structured.get("risk_factors") and any(word in raw_text for word in _NEGATIVE_WORDS):
        issues.append(
            _issue(
                "medium",
                "risk_understated",
                "risk_factors",
                "원문에는 리스크/주의 표현이 있으나 구조화 결과의 리스크 요인이 비어 있습니다.",
            )
        )

    severity_penalty = {"high": 0.25, "medium": 0.12, "low": 0.05}
    score = max(0.0, 1.0 - sum(severity_penalty.get(issue["severity"], 0.1) for issue in issues))
    status = "pass" if not issues and score >= 0.8 else "fail"
    return {
        "status": status,
        "score": round(score, 2),
        "issues": issues,
        "rewrite_required": status != "pass",
    }


def repair_structured_report(record: AnalysisRecord, structured: dict[str, Any], issues: Iterable[dict[str, Any]]) -> dict[str, Any]:
    repaired = deepcopy(structured) if isinstance(structured, dict) else {}
    # Fail-safe repair: reset every rendered structured field from the immutable
    # TradingAgents record so a distorted first draft cannot survive verification.
    canonical = build_structured_report(record)
    repaired["schema_version"] = STRUCTURED_REPORT_VERSION
    repaired["executive_summary"] = canonical["executive_summary"]
    repaired["decision_cards"] = canonical["decision_cards"]
    repaired["positive_factors"] = canonical["positive_factors"]
    repaired["risk_factors"] = canonical["risk_factors"]
    repaired["action_strategy"] = canonical["action_strategy"]
    repaired["detailed_sections"] = canonical["detailed_sections"]
    repaired["source_coverage"] = canonical["source_coverage"]
    repaired["style_tokens"] = canonical["style_tokens"]
    return repaired


def structure_and_verify_report(
    record: AnalysisRecord,
    *,
    builder: StructuredBuilder = build_structured_report,
    max_attempts: int = 3,
) -> dict[str, Any]:
    attempts = 0
    structured: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    for attempts in range(1, max(1, max_attempts) + 1):
        structured = builder(record) if attempts == 1 else repair_structured_report(record, structured or {}, verification.get("issues", []) if verification else [])
        verification = verify_structured_report(record, structured)
        if verification["status"] == "pass":
            break
    assert structured is not None and verification is not None
    return {
        "structured_report": structured,
        "structured_report_verification": verification,
        "structured_report_verified": verification["status"] == "pass",
        "structured_report_attempts": attempts,
    }
