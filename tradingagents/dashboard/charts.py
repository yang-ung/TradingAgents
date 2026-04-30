from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Iterable

CHART_WIDTH = 720
CHART_HEIGHT = 220
CHART_PADDING = 14


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _format_date(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10]


def build_price_chart(ticker: str, rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for row in rows:
        close = _as_float(row.get("close"))
        if close is None:
            continue
        point = {
            "date": str(row.get("date") or ""),
            "close": round(close, 4),
        }
        volume = _as_float(row.get("volume"))
        if volume is not None:
            point["volume"] = int(volume)
        points.append(point)

    if not points:
        return {"available": False, "ticker": ticker, "reason": "no_price_data", "points": []}

    closes = [float(point["close"]) for point in points]
    first_close = closes[0]
    latest_close = closes[-1]
    min_close = min(closes)
    max_close = max(closes)
    span = max(max_close - min_close, 1e-9)
    x_step = CHART_WIDTH / max(len(points) - 1, 1)

    coords: list[tuple[float, float]] = []
    for index, close in enumerate(closes):
        x = index * x_step
        y = CHART_PADDING + (max_close - close) / span * (CHART_HEIGHT - CHART_PADDING * 2)
        coords.append((round(x, 2), round(y, 2)))

    if len(coords) == 1:
        x, y = coords[0]
        path = f"M {x} {y} L {CHART_WIDTH} {y}"
        area_path = f"M {x} {y} L {CHART_WIDTH} {y} L {CHART_WIDTH} {CHART_HEIGHT} L {x} {CHART_HEIGHT} Z"
    else:
        path = " ".join(
            f"{'M' if index == 0 else 'L'} {x} {y}" for index, (x, y) in enumerate(coords)
        )
        first_x, first_y = coords[0]
        last_x, last_y = coords[-1]
        area_path = f"M {first_x} {first_y} " + " ".join(
            f"L {x} {y}" for x, y in coords[1:]
        ) + f" L {last_x} {CHART_HEIGHT} L {first_x} {CHART_HEIGHT} Z"

    change = latest_close - first_close
    change_percent = (change / first_close * 100) if first_close else 0.0
    return {
        "available": True,
        "ticker": ticker,
        "points": points,
        "path": path,
        "area_path": area_path,
        "latest_close": round(latest_close, 4),
        "first_close": round(first_close, 4),
        "change": round(change, 4),
        "change_percent": round(change_percent, 2),
        "min_close": round(min_close, 4),
        "max_close": round(max_close, 4),
        "start_date": points[0]["date"],
        "end_date": points[-1]["date"],
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
        "positive": change >= 0,
    }


def get_price_chart(ticker: str, trade_date: str, lookback_days: int = 180) -> dict[str, Any]:
    try:
        import yfinance as yf
    except Exception:  # pragma: no cover - environment dependent
        return {"available": False, "ticker": ticker, "reason": "yfinance_unavailable", "points": []}

    try:
        end = date.fromisoformat(trade_date) + timedelta(days=1)
    except ValueError:
        end = date.today() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)

    try:
        history = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat(), auto_adjust=True)
    except Exception:  # pragma: no cover - network dependent
        return {"available": False, "ticker": ticker, "reason": "history_failed", "points": []}

    rows: list[dict[str, Any]] = []
    try:
        for index, row in history.iterrows():
            close = _as_float(row.get("Close"))
            if close is None:
                continue
            rows.append({
                "date": _format_date(index),
                "close": close,
                "volume": _as_float(row.get("Volume")),
            })
    except Exception:  # pragma: no cover - dataframe dependent
        return {"available": False, "ticker": ticker, "reason": "history_parse_failed", "points": []}

    chart = build_price_chart(ticker, rows)
    chart["currency"] = "KRW" if ticker.upper().endswith((".KS", ".KQ")) else "USD"
    return chart
