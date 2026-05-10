from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

MARKET_SCORE_BASE = 50
MARKET_SCORE_FACTOR_DEFINITIONS = (
    {
        "key": "equity_trend",
        "label": "지수 추세",
        "description": "SPY/QQQ 등 주요 지수의 단기·중기 추세 강도",
    },
    {
        "key": "volatility",
        "label": "변동성",
        "description": "VIX/변동성 체계가 위험 선호에 주는 영향",
    },
    {
        "key": "rates",
        "label": "금리",
        "description": "미국 국채금리와 금리 기대 변화",
    },
    {
        "key": "fx",
        "label": "환율",
        "description": "달러/원 및 달러 강세가 위험자산에 주는 압력",
    },
    {
        "key": "news_sentiment",
        "label": "뉴스 심리",
        "description": "최근 뉴스·정책·기업 이벤트의 위험 선호/회피 분위기",
    },
    {
        "key": "macro_risk",
        "label": "거시 리스크",
        "description": "지정학·정책·경기침체 등 시장 공통 위험 요인",
    },
)
_FACTOR_KEYS = tuple(definition["key"] for definition in MARKET_SCORE_FACTOR_DEFINITIONS)


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _risk_level(score: int) -> str:
    if score >= 80:
        return "strong_risk_on"
    if score >= 60:
        return "weak_risk_on"
    if score >= 40:
        return "neutral"
    if score >= 20:
        return "risk_off"
    return "strong_risk_off"


def _format_signed_score(score: int) -> str:
    return f"+{score}" if score > 0 else str(score)


def _build_factor_breakdown(factors: Mapping[str, int]) -> list[dict[str, Any]]:
    breakdown: list[dict[str, Any]] = []
    for definition in MARKET_SCORE_FACTOR_DEFINITIONS:
        key = str(definition["key"])
        score = int(factors.get(key, 0))
        breakdown.append(
            {
                "key": key,
                "label": definition["label"],
                "score": score,
                "contribution_label": f"{_format_signed_score(score)}점",
                "description": definition["description"],
            }
        )
    return breakdown


def _build_score_formula(factors: Mapping[str, int], market_score: int) -> str:
    parts = [f"{definition['label']} {_format_signed_score(int(factors.get(str(definition['key']), 0)))}" for definition in MARKET_SCORE_FACTOR_DEFINITIONS]
    return f"기준점수 {MARKET_SCORE_BASE} + {' + '.join(parts)} = {market_score}"


def calculate_market_score(factor_scores: Mapping[str, object]) -> dict[str, Any]:
    """Combine hourly macro/news factor scores into a bounded market score."""
    factors = {key: int(round(_to_float(factor_scores.get(key)))) for key in _FACTOR_KEYS}
    raw_score = MARKET_SCORE_BASE + sum(factors.values())
    market_score = max(0, min(100, int(round(raw_score))))
    risk_level = _risk_level(market_score)
    return {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "score_base": MARKET_SCORE_BASE,
        "market_score": market_score,
        "risk_level": risk_level,
        "risk_off": market_score < 40,
        "factor_scores": factors,
        "factor_breakdown": _build_factor_breakdown(factors),
        "score_formula": _build_score_formula(factors, market_score),
    }


def _valid_candle(candle: Mapping[str, object]) -> bool:
    return all(key in candle for key in ("high", "low", "close"))


def detect_fair_value_gaps(candles: Sequence[Mapping[str, object]], *, max_age: int = 20) -> list[dict[str, Any]]:
    """Detect simple 3-candle bullish/bearish Fair Value Gaps."""
    valid = [c for c in candles if isinstance(c, Mapping) and _valid_candle(c)]
    gaps: list[dict[str, Any]] = []
    last_index = len(valid) - 1
    for idx in range(2, len(valid)):
        first = valid[idx - 2]
        third = valid[idx]
        first_high = _to_float(first.get("high"))
        first_low = _to_float(first.get("low"))
        third_high = _to_float(third.get("high"))
        third_low = _to_float(third.get("low"))
        age = last_index - idx
        if age > max_age:
            continue
        if first_high < third_low:
            gaps.append(_build_gap("bullish", first_high, third_low, idx, age, valid))
        if first_low > third_high:
            gaps.append(_build_gap("bearish", third_high, first_low, idx, age, valid))
    return gaps


def _build_gap(kind: str, zone_low: float, zone_high: float, created_index: int, age: int, candles: Sequence[Mapping[str, object]]) -> dict[str, Any]:
    current = candles[-1]
    current_price = _to_float(current.get("close"))
    gap_size = max(zone_high - zone_low, 0.0)
    if kind == "bullish" and gap_size > 0:
        deepest = min(_to_float(c.get("low"), zone_high) for c in candles[created_index + 1 :]) if created_index + 1 < len(candles) else zone_high
        filled = max(0.0, min(100.0, (zone_high - deepest) / gap_size * 100.0))
    elif kind == "bearish" and gap_size > 0:
        highest = max(_to_float(c.get("high"), zone_low) for c in candles[created_index + 1 :]) if created_index + 1 < len(candles) else zone_low
        filled = max(0.0, min(100.0, (highest - zone_low) / gap_size * 100.0))
    else:
        filled = 0.0
    return {
        "type": kind,
        "created_at": candles[created_index].get("date") or candles[created_index].get("time") or str(created_index),
        "zone_low": round(zone_low, 4),
        "zone_high": round(zone_high, 4),
        "midpoint": round((zone_low + zone_high) / 2.0, 4),
        "gap_percent": round((gap_size / zone_low * 100.0) if zone_low else 0.0, 2),
        "filled_percent": round(filled, 2),
        "age_bars": age,
        "current_price": current_price,
    }


def build_price_table(market_state: Mapping[str, object], symbol_candles: Mapping[str, Sequence[Mapping[str, object]]]) -> dict[str, Any]:
    market_score = int(round(_to_float(market_state.get("market_score"))))
    rows = [_build_symbol_row(ticker, candles, market_score, bool(market_state.get("risk_off"))) for ticker, candles in symbol_candles.items()]
    rows.sort(key=lambda row: (row["action_rank"], -row["pattern_score"], row["ticker"]))
    for row in rows:
        row.pop("action_rank", None)
    return {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "market_score": market_score,
        "risk_level": market_state.get("risk_level") or _risk_level(market_score),
        "risk_off": bool(market_state.get("risk_off")),
        "rows": rows,
    }


def _build_symbol_row(ticker: str, candles: Sequence[Mapping[str, object]], market_score: int, risk_off: bool) -> dict[str, Any]:
    valid = [c for c in candles if isinstance(c, Mapping) and _valid_candle(c)]
    current_price = _to_float(valid[-1].get("close")) if valid else 0.0
    gaps = detect_fair_value_gaps(valid, max_age=20)
    bullish_gaps = [gap for gap in gaps if gap["type"] == "bullish"]
    selected = bullish_gaps[-1] if bullish_gaps else None
    if selected:
        entry_low = float(selected["zone_low"])
        entry_high = float(selected["zone_high"])
        midpoint = float(selected["midpoint"])
        touched = entry_low <= current_price <= entry_high
        stop_loss = round(entry_low - max(entry_high - entry_low, entry_low * 0.02), 4)
        risk = max(midpoint - stop_loss, 0.0001)
        take_profit_1 = round(midpoint + risk * 2.0, 4)
        pattern_score = _pattern_score(selected, touched)
        if risk_off or market_score < 40:
            action, status, rank = "신규매수 금지", "시장 위험", 4
        elif market_score >= 60 and pattern_score >= 70 and touched:
            action, status, rank = "매수 후보", "진입구간 터치", 1
        elif touched:
            action, status, rank = "관찰", "진입구간 터치", 2
        else:
            action, status, rank = "진입 대기", "관찰", 3
        setup_type = "bullish_fvg_retest"
    else:
        entry_low = entry_high = stop_loss = take_profit_1 = None
        pattern_score = 0
        action, status, rank = ("신호 없음", "신호 없음", 5)
        setup_type = "none"
    return {
        "ticker": ticker,
        "current_price": round(current_price, 4),
        "market_score": market_score,
        "pattern_score": pattern_score,
        "setup_type": setup_type,
        "status_label": status,
        "action": action,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "entry_zone_label": f"{entry_low:.2f} ~ {entry_high:.2f}" if entry_low is not None and entry_high is not None else "-",
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "fvg": selected,
        "live_capital_allowed": False,
        "paper_order_only": True,
        "action_rank": rank,
    }


def _pattern_score(gap: Mapping[str, object], touched: bool) -> int:
    score = 45
    gap_percent = _to_float(gap.get("gap_percent"))
    filled_percent = _to_float(gap.get("filled_percent"))
    age_bars = _to_float(gap.get("age_bars"))
    if 0.2 <= gap_percent <= 4.0:
        score += 15
    if touched:
        score += 25
    if 20 <= filled_percent <= 80:
        score += 10
    if age_bars <= 10:
        score += 5
    return max(0, min(100, int(round(score))))
