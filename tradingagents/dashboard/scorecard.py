from __future__ import annotations

import re
from typing import Any

_FACTOR_ORDER = [
    "market_common_risk",
    "sector",
    "fundamentals",
    "stock_news",
    "sentiment",
    "chart_trend",
    "entry_timing",
    "volatility_risk",
]

_FACTOR_LABELS = {
    "market_common_risk": "시장 공통 리스크",
    "sector": "산업/섹터",
    "fundamentals": "기업 펀더멘털",
    "stock_news": "종목 뉴스/공시",
    "sentiment": "수급/심리",
    "chart_trend": "차트 추세",
    "entry_timing": "진입 타이밍",
    "volatility_risk": "변동성/리스크",
}

_FACTOR_SCOPES = {
    "market_common_risk": "시장 공통",
    "sector": "섹터",
    "fundamentals": "종목",
    "stock_news": "종목",
    "sentiment": "종목",
    "chart_trend": "종목",
    "entry_timing": "종목",
    "volatility_risk": "종목",
}

_POSITIVE_RULES = {
    "market_common_risk": [],
    "sector": [
        ("반도체", 15, "반도체 업황"),
        ("AI 인프라", 15, "AI 인프라 수혜"),
        ("업황 회복", 15, "업황 회복"),
        ("수요", 8, "수요 개선"),
    ],
    "fundamentals": [
        ("영업이익", 16, "영업이익 개선"),
        ("순이익", 10, "순이익 개선"),
        ("영업이익률", 14, "마진 개선"),
        ("ROE", 8, "ROE 확인"),
        ("PER", 14, "밸류에이션 매력"),
        ("PEG", 14, "성장 대비 저평가"),
        ("저평가", 12, "저평가 언급"),
        ("플러스 FCF", 10, "현금창출 개선"),
    ],
    "stock_news": [
        ("수주", 14, "수주/계약"),
        ("실적 개선", 12, "실적 개선 뉴스"),
        ("투자", 8, "투자/증설"),
        ("직접 악재 없음", 8, "직접 악재 부재"),
    ],
    "sentiment": [
        ("긍정", 10, "긍정 심리"),
        ("수급", 8, "수급 개선"),
        ("자금 흐름", 8, "자금 흐름 우호"),
    ],
    "chart_trend": [
        ("MACD", 15, "MACD 강세"),
        ("히스토그램", 10, "모멘텀 확인"),
        ("상방", 10, "상방 추세"),
        ("이동평균", 8, "이동평균 추세"),
        ("상회", 8, "주요 지표 상회"),
    ],
}

_NEGATIVE_RULES = {
    "market_common_risk": [
        ("전쟁", 24, "전쟁/지정학 리스크"),
        ("유가 상승", 18, "유가 상승"),
        ("인플레이션", 14, "인플레이션 압력"),
        ("금리 인하 기대가 후퇴", 16, "금리 인하 기대 후퇴"),
        ("금리 상승", 16, "금리 상승"),
        ("위험자산 회피", 14, "위험자산 회피"),
        ("관세", 12, "관세 리스크"),
        ("수출 규제", 14, "수출 규제"),
        ("미중", 10, "미중 갈등"),
    ],
    "sector": [
        ("수출 규제", 14, "섹터 규제"),
        ("업황 둔화", 16, "업황 둔화"),
        ("공급 과잉", 14, "공급 과잉"),
    ],
    "fundamentals": [
        ("차입", 10, "차입 증가"),
        ("부채", 10, "부채 부담"),
        ("CAPEX", 10, "CAPEX 부담"),
        ("현금흐름 변동성", 10, "현금흐름 변동성"),
    ],
    "stock_news": [
        ("악재", 14, "악재"),
        ("소송", 12, "소송"),
        ("리콜", 12, "리콜"),
        ("촉매 부족", 10, "단기 촉매 부족"),
    ],
    "sentiment": [
        ("부정", 10, "부정 심리"),
        ("회피", 8, "위험 회피"),
        ("매도", 8, "매도 압력"),
    ],
    "entry_timing": [
        ("RSI", 15, "RSI 과열"),
        ("과열", 18, "과열"),
        ("이격", 18, "이동평균 이격"),
        ("추격 매수", 14, "추격 매수 위험"),
        ("볼린저 상단", 12, "볼린저 상단"),
        ("대기", 8, "대기 권고"),
    ],
    "volatility_risk": [
        ("ATR", 15, "ATR 확대"),
        ("변동성", 15, "변동성 확대"),
        ("손절", 8, "손절 리스크"),
        ("리스크", 8, "리스크 언급"),
    ],
}

_NUMERIC_PATTERNS = [
    ("entry_timing", re.compile(r"RSI\s*(?:[:=]|이)?\s*(?P<value>\d+(?:\.\d+)?)", re.IGNORECASE), 70.0, 80.0, "RSI 과열 수치"),
]


def _clamp(value: float, low: int = -100, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def _score_label(score: int) -> str:
    if score >= 60:
        return "매우 긍정"
    if score >= 20:
        return "긍정"
    if score <= -60:
        return "매우 부정"
    if score <= -20:
        return "부정"
    return "중립"


def _tone(score: int) -> str:
    if score >= 20:
        return "positive"
    if score <= -20:
        return "negative"
    return "neutral"


def _collect_text(reports: dict[str, str], keys: list[str]) -> str:
    return "\n".join(str(reports.get(key) or "") for key in keys)


_MARKET_SECTION_PATTERNS = (
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?global_market\b\s*[:：-]?", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?market[- ]common\b\s*[:：-]?", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?시장\s*공통(?:\s*뉴스)?\s*[:：-]?"),
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?거시(?:\s*뉴스|\s*이벤트)?\s*[:：-]?"),
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?macro\b\s*[:：-]?", re.IGNORECASE),
)
_STOCK_SECTION_PATTERNS = (
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?stock[-_ ]specific\b\s*[:：-]?", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?종목별(?:\s*뉴스)?\s*[:：-]?"),
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?기업별(?:\s*뉴스)?\s*[:：-]?"),
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*)?company[- ]specific\b\s*[:：-]?", re.IGNORECASE),
)
_NEGATION_MARKERS = ("없", "미미", "제한적", "완화", "부재", "낮", "후퇴하지 않", "아님", "not", "limited", "no ")


def _sentence_for_keyword(text: str, keyword: str) -> str:
    lower_keyword = keyword.lower()
    for sentence in re.split(r"(?<=[.!?。])\s+|[\n;]", text):
        if lower_keyword in sentence.lower():
            return sentence
    return text


def _is_negated_context(text: str, keyword: str) -> bool:
    sentence = _sentence_for_keyword(text, keyword)
    return any(marker.lower() in sentence.lower() for marker in _NEGATION_MARKERS)


def _extract_market_table_text(normalized: str) -> str | None:
    table_lines = [line.strip() for line in normalized.splitlines() if line.strip().startswith("|") and "|" in line.strip()[1:]]
    scoped_rows: list[tuple[str, str]] = []
    for line in table_lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        lower_cells = [cell.lower() for cell in cells]
        row_text = " ".join(cells)
        if any("global_market" in cell or "market-common" in cell or "market common" in cell or "시장 공통" in cell or cell == "macro" for cell in lower_cells):
            scoped_rows.append(("market", row_text))
        elif any("stock_specific" in cell or "stock-specific" in cell or "stock specific" in cell or "종목별" in cell or "기업별" in cell or "company-specific" in cell for cell in lower_cells):
            scoped_rows.append(("stock", row_text))
    if not scoped_rows:
        return None
    return "\n".join(text for scope, text in scoped_rows if scope == "market")


def _extract_market_common_text(news_report: str) -> str:
    if not news_report:
        return ""
    normalized = news_report.replace("\r\n", "\n")
    table_text = _extract_market_table_text(normalized)
    if table_text is not None:
        return table_text
    section_spans: list[tuple[int, int, str]] = []
    for pattern in _MARKET_SECTION_PATTERNS:
        section_spans.extend((match.start(), match.end(), "market") for match in pattern.finditer(normalized))
    for pattern in _STOCK_SECTION_PATTERNS:
        section_spans.extend((match.start(), match.end(), "stock") for match in pattern.finditer(normalized))
    if section_spans:
        section_spans.sort(key=lambda item: item[0])
        market_chunks: list[str] = []
        for index, (_, marker_end, section_type) in enumerate(section_spans):
            next_start = section_spans[index + 1][0] if index + 1 < len(section_spans) else len(normalized)
            if section_type == "market":
                market_chunks.append(normalized[marker_end:next_start].strip())
        return "\n".join(chunk for chunk in market_chunks if chunk)
    return news_report


def _apply_rules(text: str, factor_id: str) -> tuple[int, list[str]]:
    score = 0
    evidence: list[str] = []
    for keyword, weight, label in _POSITIVE_RULES.get(factor_id, []):
        if keyword.lower() in text.lower():
            score += weight
            evidence.append(label)
    for keyword, weight, label in _NEGATIVE_RULES.get(factor_id, []):
        if keyword.lower() in text.lower() and not _is_negated_context(text, keyword):
            score -= weight
            evidence.append(label)
    for numeric_factor, pattern, warn, severe, label in _NUMERIC_PATTERNS:
        if numeric_factor != factor_id:
            continue
        for match in pattern.finditer(text):
            try:
                value = float(match.group("value"))
            except (TypeError, ValueError):
                continue
            if value >= severe:
                score -= 18
                evidence.append(f"{label} {value:g}")
            elif value >= warn:
                score -= 10
                evidence.append(f"{label} {value:g}")
    return _clamp(score), evidence[:5]


def _reason_for_factor(factor_id: str, score: int, evidence: list[str]) -> str:
    if evidence:
        joined = ", ".join(evidence[:3])
        if score > 0:
            return f"{joined} 요인이 긍정적으로 작용합니다."
        if score < 0:
            return f"{joined} 요인이 부담으로 작용합니다."
        return f"{joined} 요인이 혼재되어 있습니다."
    return "뚜렷한 긍정·부정 신호가 제한적입니다."


def _build_factor(factor_id: str, reports: dict[str, str]) -> dict[str, Any]:
    source_keys = {
        "market_common_risk": ["news_report"],
        "sector": ["news_report", "fundamentals_report", "investment_plan", "final_trade_decision"],
        "fundamentals": ["fundamentals_report", "final_trade_decision"],
        "stock_news": ["news_report", "sentiment_report"],
        "sentiment": ["sentiment_report", "news_report"],
        "chart_trend": ["market_report", "quant_strategy_report", "final_trade_decision"],
        "entry_timing": ["market_report", "quant_strategy_report", "trader_investment_decision", "final_trade_decision"],
        "volatility_risk": ["market_report", "quant_strategy_report", "trader_investment_decision", "final_trade_decision"],
    }[factor_id]
    text = _extract_market_common_text(str(reports.get("news_report") or "")) if factor_id == "market_common_risk" else _collect_text(reports, source_keys)
    score, evidence = _apply_rules(text, factor_id)
    if factor_id == "chart_trend" and "RSI" in text and score > 45:
        score = min(score, 45)
    return {
        "id": factor_id,
        "label": _FACTOR_LABELS[factor_id],
        "score": score,
        "score_label": _score_label(score),
        "tone": _tone(score),
        "scope": _FACTOR_SCOPES[factor_id],
        "confidence": 0.78 if evidence else 0.45,
        "reason": _reason_for_factor(factor_id, score, evidence),
        "evidence": evidence,
    }


def _build_global_events(market_factor: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    labels = list(market_factor.get("evidence") or [])
    for label in labels:
        if any(token in label for token in ("전쟁", "유가", "인플레이션", "금리", "관세", "규제", "미중", "회피")):
            event_score = -30 if market_factor.get("score", 0) < 0 else 20
            events.append({
                "label": label,
                "score": event_score,
                "score_label": _score_label(event_score),
                "tone": _tone(event_score),
                "scope": "시장 공통",
                "reason": _reason_for_factor("market_common_risk", event_score, [label]),
            })
    return events[:5]


def build_decision_scorecard(record: dict[str, Any]) -> dict[str, Any]:
    reports = record.get("reports") if isinstance(record.get("reports"), dict) else {}
    if not reports:
        return {"available": False, "reason": "reports_unavailable"}

    factors = [_build_factor(factor_id, reports) for factor_id in _FACTOR_ORDER]
    by_id = {factor["id"]: factor for factor in factors}

    direction_score = _clamp(
        by_id["sector"]["score"] * 0.22
        + by_id["fundamentals"]["score"] * 0.34
        + by_id["stock_news"]["score"] * 0.14
        + by_id["sentiment"]["score"] * 0.10
        + by_id["chart_trend"]["score"] * 0.20
        + min(0, by_id["market_common_risk"]["score"]) * 0.25
    )
    entry_timing_score = _clamp(
        by_id["entry_timing"]["score"] * 0.55
        + by_id["volatility_risk"]["score"] * 0.30
        + by_id["chart_trend"]["score"] * 0.15
    )
    market_risk_score = by_id["market_common_risk"]["score"]

    if market_risk_score <= -75:
        final_action_label = "시장 리스크로 신규 매수 보류"
    elif entry_timing_score <= -25 and direction_score > 0:
        final_action_label = "신규 매수 대기"
    elif direction_score >= 30 and entry_timing_score >= 0:
        final_action_label = "분할 매수 검토"
    elif direction_score <= -30:
        final_action_label = "비중 축소 검토"
    else:
        final_action_label = "관망"

    positive = sorted((f for f in factors if f["score"] > 0), key=lambda item: item["score"], reverse=True)[:3]
    negative = sorted((f for f in factors if f["score"] < 0), key=lambda item: item["score"])[:3]
    global_events = _build_global_events(by_id["market_common_risk"])

    summary = (
        f"중기 방향성은 {_score_label(direction_score)}, 현재 진입 타이밍은 {_score_label(entry_timing_score)}입니다. "
        f"시장 공통 리스크는 {_score_label(market_risk_score)}이며, 최종 실행은 '{final_action_label}'입니다."
    )

    return {
        "available": True,
        "direction_score": direction_score,
        "direction_label": _score_label(direction_score),
        "entry_timing_score": entry_timing_score,
        "entry_timing_label": _score_label(entry_timing_score),
        "market_risk_score": market_risk_score,
        "market_risk_label": _score_label(market_risk_score),
        "final_action_label": final_action_label,
        "summary": summary,
        "factors": factors,
        "positive_factors": [
            {"label": f["label"], "score": f["score"], "reason": f["reason"], "score_label": f["score_label"], "tone": f["tone"]}
            for f in positive
        ],
        "negative_factors": [
            {"label": f["label"], "score": f["score"], "reason": f["reason"], "score_label": f["score_label"], "tone": f["tone"]}
            for f in negative
        ],
        "global_events": global_events,
    }
