from __future__ import annotations

from datetime import date
from typing import Any

from .schema import StrategySpec


def _price(point: dict[str, Any]) -> float | None:
    for key in ("close", "price"):
        try:
            value = float(point.get(key))
        except (TypeError, ValueError, AttributeError):
            continue
        return value
    return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _point_date(point: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(point.get("date") or "")[:10])
    except ValueError:
        return None


def evaluate_signal(spec: StrategySpec, point: dict[str, Any], *, position_open: bool = False) -> dict[str, Any]:
    price = _price(point)
    if price is None:
        return {"ticker": spec.ticker, "date": point.get("date", ""), "action": "NO_DATA", "reason": "가격 데이터 없음"}

    if position_open:
        if price <= spec.stop_loss.price:
            return {"ticker": spec.ticker, "date": point.get("date", ""), "action": "SELL_STOP_LOSS", "price": price, "reason": "손절 기준 이탈"}
        if price >= spec.take_profit.price:
            return {"ticker": spec.ticker, "date": point.get("date", ""), "action": "SELL_TAKE_PROFIT", "price": price, "reason": "익절 기준 도달"}
        return {"ticker": spec.ticker, "date": point.get("date", ""), "action": "HOLD", "price": price, "reason": "보유 조건 유지"}

    if spec.entry.low <= price <= spec.entry.high:
        return {"ticker": spec.ticker, "date": point.get("date", ""), "action": "BUY", "price": price, "reason": "진입 구간 도달"}
    if price <= spec.stop_loss.price:
        return {"ticker": spec.ticker, "date": point.get("date", ""), "action": "AVOID", "price": price, "reason": "무효화 가격 이하"}
    return {"ticker": spec.ticker, "date": point.get("date", ""), "action": "WAIT", "price": price, "reason": "진입 구간 대기"}


def evaluate_reanalysis_triggers(spec: StrategySpec, points: list[dict[str, Any]], *, as_of_date: str | None = None) -> dict[str, Any]:
    if not points:
        return {"ticker": spec.ticker, "reanalysis_required": False, "triggers": [], "reason": "가격 데이터 없음"}

    latest = points[-1]
    previous = points[-2] if len(points) >= 2 else None
    latest_price = _price(latest)
    latest_date = _point_date(latest)
    if as_of_date:
        try:
            latest_date = date.fromisoformat(as_of_date[:10])
        except ValueError:
            pass

    fired: list[dict[str, str]] = []
    for trigger in spec.reanalysis_triggers:
        reason = trigger.reason or _default_trigger_reason(trigger.type)
        if trigger.type == "price_below" and latest_price is not None and latest_price <= float(trigger.level):
            fired.append({"type": trigger.type, "reason": reason})
        elif trigger.type == "price_above" and latest_price is not None and latest_price >= float(trigger.level):
            fired.append({"type": trigger.type, "reason": reason})
        elif trigger.type == "time_expired":
            trigger_date = _parse_date(trigger.date)
            if latest_date is not None and trigger_date is not None and latest_date >= trigger_date:
                fired.append({"type": trigger.type, "reason": reason})
        elif trigger.type == "volume_spike" and _volume_spike(points, trigger.multiplier, trigger.lookback_days):
            fired.append({"type": trigger.type, "reason": reason})
        elif trigger.type == "moving_average_cross" and previous is not None and _moving_average_cross(previous, latest, trigger.ma, trigger.direction):
            fired.append({"type": trigger.type, "reason": reason})

    valid_until = _parse_date(spec.valid_until)
    if latest_date is not None and valid_until is not None and latest_date >= valid_until and not any(item["type"] == "time_expired" for item in fired):
        fired.append({"type": "time_expired", "reason": "전략 유효기간 만료"})

    return {
        "ticker": spec.ticker,
        "date": str(latest.get("date") or ""),
        "reanalysis_required": bool(fired),
        "triggers": fired,
    }


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _volume_spike(points: list[dict[str, Any]], multiplier: float | None, lookback_days: int | None) -> bool:
    if len(points) < 2 or multiplier is None or lookback_days is None:
        return False
    current = _number(points[-1].get("volume"))
    if current is None or current <= 0:
        return False
    prior_rows = points[max(0, len(points) - 1 - int(lookback_days)) : -1]
    volumes = [_number(row.get("volume")) for row in prior_rows]
    volumes = [value for value in volumes if value is not None and value > 0]
    if not volumes:
        return False
    average = sum(volumes) / len(volumes)
    return current >= average * float(multiplier)


def _moving_average_cross(previous: dict[str, Any], latest: dict[str, Any], ma: str | None, direction: str | None) -> bool:
    if not ma:
        return False
    prev_price = _price(previous)
    latest_price = _price(latest)
    prev_ma = _number(previous.get(ma))
    latest_ma = _number(latest.get(ma))
    if None in {prev_price, latest_price, prev_ma, latest_ma}:
        return False
    if direction == "down":
        return prev_price >= prev_ma and latest_price < latest_ma
    if direction == "up":
        return prev_price <= prev_ma and latest_price > latest_ma
    return False


def _default_trigger_reason(trigger_type: str) -> str:
    return {
        "price_below": "가격 하향 트리거",
        "price_above": "가격 상향 트리거",
        "volume_spike": "거래량 급증",
        "moving_average_cross": "이동평균 교차",
        "time_expired": "전략 유효기간 만료",
    }.get(trigger_type, "재분석 조건 충족")
