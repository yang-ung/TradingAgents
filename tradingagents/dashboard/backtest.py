from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Any

from tradingagents.strategies.schema import parse_strategy_spec

_PRICE_RE = re.compile(r"(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_RANGE_RE = re.compile(
    r"(?P<label>진입|매수|entry)[^\d]{0,24}"
    r"(?P<low>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?:-|~|–|—|부터|에서|to)\s*"
    r"(?P<high>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_SINGLE_LEVEL_PATTERNS = {
    "entry": re.compile(r"(?:진입|매수|entry)[^\d]{0,24}(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)", re.IGNORECASE),
    "take_profit": re.compile(r"(?:익절|목표|target|take\s*profit|take-profit)[^\d]{0,24}(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)", re.IGNORECASE),
    "stop_loss": re.compile(r"(?:손절|stop\s*loss|stop-loss|invalidation|무효화)[^\d]{0,24}(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)", re.IGNORECASE),
}
_STOP_LEVEL_AFTER_PRICE_RE = re.compile(
    r"(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:원|KRW)?\s*(?:이탈|하회|깨지|하향|아래)",
    re.IGNORECASE,
)
_PERIODS = (
    ("1개월", 30),
    ("3개월", 90),
    ("6개월", 180),
    ("전체", None),
)


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_price(text: str) -> float | None:
    match = _PRICE_RE.search(str(text or ""))
    if not match:
        return None
    return _as_float(match.group("value").replace(",", ""))


def extract_price_timing_levels(record: dict[str, Any], chart: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Extract conservative long-only price timing levels from a structured strategy first, then prose."""
    spec = parse_strategy_spec(record.get("strategy_spec") if isinstance(record, dict) else None)
    if spec is not None:
        levels = spec.to_price_timing_levels()
        if not levels.get("currency"):
            levels["currency"] = str((chart or {}).get("currency") or "").strip().upper()
        return levels

    reports = record.get("reports") if isinstance(record, dict) else {}
    if not isinstance(reports, dict):
        return None
    text = "\n".join(
        str(reports.get(key) or "")
        for key in ("quant_strategy_report", "trader_investment_decision", "final_trade_decision")
    )
    range_match = _RANGE_RE.search(text)
    entry_low = entry_high = None
    if range_match:
        entry_low = _parse_price(range_match.group("low"))
        entry_high = _parse_price(range_match.group("high"))
        if entry_low is not None and entry_high is not None and entry_low > entry_high:
            entry_low, entry_high = entry_high, entry_low
    else:
        entry = _match_single_level(text, "entry")
        if entry is not None:
            entry_low = entry_high = entry

    take_profit = _match_single_level(text, "take_profit", min_value=entry_high)
    stop_loss = _match_single_level(text, "stop_loss", max_value=entry_low)
    if entry_low is None or entry_high is None or take_profit is None or stop_loss is None:
        return None
    if stop_loss >= entry_low or take_profit <= entry_high:
        return None
    currency = str((chart or {}).get("currency") or "").strip().upper() or _infer_currency(text)
    return {
        "entry_low": round(entry_low, 4),
        "entry_high": round(entry_high, 4),
        "take_profit": round(take_profit, 4),
        "stop_loss": round(stop_loss, 4),
        "currency": currency,
    }


def _match_single_level(
    text: str,
    key: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float | None:
    pattern = _SINGLE_LEVEL_PATTERNS[key]
    for match in pattern.finditer(text):
        value_text = match.group("value")
        if _looks_like_ordinal_or_percent(text, match.end("value"), value_text):
            continue
        value = _parse_price(value_text)
        if value is None:
            continue
        if min_value is not None and value <= min_value:
            continue
        if max_value is not None and value >= max_value:
            continue
        return value
    if key == "stop_loss":
        for stop_match in _STOP_LEVEL_AFTER_PRICE_RE.finditer(text):
            value = _parse_price(stop_match.group("value"))
            if value is None:
                continue
            if min_value is not None and value <= min_value:
                continue
            if max_value is not None and value >= max_value:
                continue
            return value
    return None


def _looks_like_ordinal_or_percent(text: str, value_end: int, value_text: str) -> bool:
    """Avoid treating Korean ordinal labels such as 1차/2순위 as prices."""
    if "," in str(value_text):
        return False
    suffix = str(text or "")[value_end:value_end + 8]
    return bool(re.match(r"\s*(?:[.)]|차|순위|퍼센트|%)", suffix))


def _infer_currency(text: str) -> str:
    upper = text.upper()
    if "KRW" in upper or "원" in text:
        return "KRW"
    if "USD" in upper or "$" in text:
        return "USD"
    return ""


def build_strategy_backtest(record: dict[str, Any], chart: dict[str, Any]) -> dict[str, Any]:
    if not chart.get("available"):
        return {"available": False, "reason": "chart_unavailable", "periods": {}}
    points = _valid_points(chart.get("points") or [])
    levels = extract_price_timing_levels(record, chart)
    if levels is None:
        return {"available": False, "reason": "price_timing_levels_unavailable", "periods": {}}
    if len(points) < 2:
        return {"available": False, "reason": "insufficient_price_history", "periods": {}}

    periods: dict[str, Any] = {}
    for label, days in _PERIODS:
        window = _slice_period(points, days)
        if days is not None and _window_span_days(window) < max(days - 3, 1):
            continue
        if len(window) >= 2:
            periods[label] = _simulate_long_strategy(window, levels)
    if not periods:
        return {"available": False, "reason": "insufficient_price_history", "periods": {}}

    best_return = max(period["strategy_return_percent"] for period in periods.values())
    best_period = "전체" if periods.get("전체", {}).get("strategy_return_percent") == best_return else next(
        key for key, period in periods.items() if period["strategy_return_percent"] == best_return
    )
    all_period = periods.get("전체") or next(iter(periods.values()))
    return {
        "available": True,
        "strategy": "price_timing_long",
        "levels": levels,
        "periods": periods,
        "summary": {
            "best_period": best_period,
            "total_return_percent": all_period["strategy_return_percent"],
            "benchmark_return_percent": all_period["benchmark_return_percent"],
            "trade_count": all_period["trade_count"],
            "win_rate_percent": all_period["win_rate_percent"],
        },
    }


def _valid_points(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in rows:
        close = _as_float(row.get("close"))
        if close is None or close <= 0:
            continue
        open_price = _as_float(row.get("open")) or close
        high = max(_as_float(row.get("high")) or close, open_price, close)
        low = min(_as_float(row.get("low")) or close, open_price, close)
        points.append({"date": str(row.get("date") or ""), "open": open_price, "high": high, "low": low, "close": close})
    return points


def _slice_period(points: list[dict[str, Any]], days: int | None) -> list[dict[str, Any]]:
    if days is None:
        return points
    try:
        end = date.fromisoformat(points[-1]["date"][:10])
    except ValueError:
        return points[-min(len(points), days):]
    start = end - timedelta(days=days)
    sliced = [point for point in points if _point_date(point) is None or _point_date(point) >= start]
    return sliced if len(sliced) >= 2 else points[-min(len(points), days):]


def _window_span_days(points: list[dict[str, Any]]) -> int:
    if len(points) < 2:
        return 0
    start = _point_date(points[0])
    end = _point_date(points[-1])
    if start is None or end is None:
        return len(points) - 1
    return max((end - start).days, 0)


def _point_date(point: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(point.get("date") or "")[:10])
    except ValueError:
        return None


def _simulate_long_strategy(points: list[dict[str, Any]], levels: dict[str, Any]) -> dict[str, Any]:
    entry_low = float(levels["entry_low"])
    entry_high = float(levels["entry_high"])
    take_profit = float(levels["take_profit"])
    stop_loss = float(levels["stop_loss"])
    cash = 1.0
    position = 0.0
    entry_price = None
    entry_date = None
    trades: list[dict[str, Any]] = []
    entry_touch_days = 0

    for point in points:
        entry_touched = point["low"] <= entry_high and point["high"] >= entry_low
        if entry_touched:
            entry_touch_days += 1
        if position == 0.0:
            if entry_touched:
                fill = min(max(entry_high, point["low"]), point["high"])
                position = cash / fill
                cash = 0.0
                entry_price = fill
                entry_date = point["date"]
            else:
                continue

        exit_price = None
        exit_reason = None
        # Conservative assumption: if both stop and target are touched in the same candle, stop first.
        if point["low"] <= stop_loss:
            exit_price = stop_loss
            exit_reason = "stop_loss"
        elif point["high"] >= take_profit:
            exit_price = take_profit
            exit_reason = "take_profit"
        if exit_price is not None and entry_price is not None:
            cash = position * exit_price
            position = 0.0
            trades.append({
                "entry_date": entry_date,
                "entry_price": round(entry_price, 4),
                "exit_date": point["date"],
                "exit_price": round(exit_price, 4),
                "exit_reason": exit_reason,
                "return_percent": round((exit_price - entry_price) / entry_price * 100, 2),
            })
            entry_price = None
            entry_date = None

    if position and entry_price is not None:
        final_close = points[-1]["close"]
        cash = position * final_close
        trades.append({
            "entry_date": entry_date,
            "entry_price": round(entry_price, 4),
            "exit_date": points[-1]["date"],
            "exit_price": round(final_close, 4),
            "exit_reason": "period_end",
            "return_percent": round((final_close - entry_price) / entry_price * 100, 2),
        })

    strategy_return = (cash - 1.0) * 100
    benchmark_return = (points[-1]["close"] - points[0]["close"]) / points[0]["close"] * 100 if points[0]["close"] else 0.0
    wins = sum(1 for trade in trades if trade["return_percent"] > 0)
    return {
        "start_date": points[0]["date"],
        "end_date": points[-1]["date"],
        "strategy_return_percent": round(strategy_return, 2),
        "benchmark_return_percent": round(benchmark_return, 2),
        "excess_return_percent": round(strategy_return - benchmark_return, 2),
        "trade_count": len(trades),
        "entry_touch_days": entry_touch_days,
        "entry_touch_percent": round(entry_touch_days / len(points) * 100, 2) if points else 0.0,
        "sample_days": len(points),
        "win_rate_percent": round(wins / len(trades) * 100, 2) if trades else 0.0,
        "trades": trades,
    }


def build_strategy_execution_replay(record: dict[str, Any], chart: dict[str, Any], *, initial_capital: int = 100_000_000) -> dict[str, Any]:
    """Replay one saved strategy and stop automatic re-entry after invalidation."""
    if not chart.get("available"):
        return {"available": False, "reason": "chart_unavailable", "events": []}
    spec = parse_strategy_spec(record.get("strategy_spec") if isinstance(record, dict) else None)
    if spec is None:
        return {"available": False, "reason": "strategy_spec_unavailable", "events": []}
    points = _valid_points(chart.get("points") or [])
    if not points:
        return {"available": False, "reason": "insufficient_price_history", "events": []}

    levels = spec.to_price_timing_levels()
    entry_low = float(levels["entry_low"])
    entry_high = float(levels["entry_high"])
    take_profit = float(levels["take_profit"])
    stop_loss = float(levels["stop_loss"])
    currency = levels.get("currency") or str(chart.get("currency") or "").upper()
    cash = float(initial_capital)
    shares = 0.0
    entry_price: float | None = None
    entry_capital: float | None = None
    events: list[dict[str, Any]] = []
    reanalysis_required = False
    stopped_after_reanalysis = False

    for point in points:
        if reanalysis_required:
            break
        if shares == 0.0:
            if point["low"] <= entry_high and point["high"] >= entry_low:
                fill = min(max(entry_high, point["low"]), point["high"])
                entry_capital = cash
                shares = cash / fill
                cash = 0.0
                entry_price = fill
                events.append({
                    "type": "buy",
                    "label": "매수",
                    "date": point["date"],
                    "price": round(fill, 4),
                    "capital": int(round(entry_capital)),
                    "reason": "entry_zone",
                })
            else:
                continue

        exit_price = None
        exit_reason = None
        exit_label = None
        if point["low"] <= stop_loss:
            exit_price = stop_loss
            exit_reason = "stop_loss"
            exit_label = "손절"
        elif point["high"] >= take_profit:
            exit_price = take_profit
            exit_reason = "take_profit"
            exit_label = "익절"

        if exit_price is not None and entry_price is not None and entry_capital is not None:
            display_return_percent = round((exit_price - entry_price) / entry_price * 100, 2)
            cash = entry_capital * (1 + display_return_percent / 100)
            shares = 0.0
            trade_pnl = cash - entry_capital
            events.append({
                "type": "sell",
                "label": exit_label,
                "date": point["date"],
                "price": round(exit_price, 4),
                "reason": exit_reason,
                "return_percent": display_return_percent,
                "pnl": int(round(trade_pnl)),
                "equity": int(round(cash)),
            })
            if exit_reason == "stop_loss":
                reanalysis_required = True
                stopped_after_reanalysis = True
                events.append({
                    "type": "reanalysis_required",
                    "label": "재분석 필요",
                    "date": point["date"],
                    "price": round(exit_price, 4),
                    "reason": "손절가 이탈",
                    "auto_reentry_blocked": True,
                })
            entry_price = None
            entry_capital = None

    final_equity = cash if shares == 0.0 else shares * points[-1]["close"]
    pnl = final_equity - float(initial_capital)
    return {
        "available": True,
        "strategy": spec.strategy_type,
        "strategy_id": spec.strategy_id,
        "currency": currency,
        "initial_capital": int(round(initial_capital)),
        "final_equity": int(round(final_equity)),
        "pnl": int(round(pnl)),
        "return_percent": round(pnl / float(initial_capital) * 100, 2) if initial_capital else 0.0,
        "reanalysis_required": reanalysis_required,
        "stopped_after_reanalysis": stopped_after_reanalysis,
        "events": events,
    }
