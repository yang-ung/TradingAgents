from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents.automation.signal_engine import (
    MARKET_SCORE_FACTOR_DEFINITIONS,
    build_price_table,
    calculate_market_score,
)

DEFAULT_SYMBOLS = ["SPY", "QQQ", "QLD", "SOXL", "SCHD", "IEF", "GLD", "BIL"]
FACTOR_LABELS = {str(definition["key"]): str(definition["label"]) for definition in MARKET_SCORE_FACTOR_DEFINITIONS}


def _load_factor_scores(path: str | None) -> dict[str, Any]:
    if not path:
        return {
            "equity_trend": 0,
            "volatility": 0,
            "rates": 0,
            "fx": 0,
            "news_sentiment": 0,
            "macro_risk": 0,
        }
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("factor score JSON must be an object")
    return payload


def _download_candles(symbol: str, period: str = "6mo") -> list[dict[str, Any]]:
    import yfinance as yf

    history = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    candles: list[dict[str, Any]] = []
    if history is None or history.empty:
        return candles
    for idx, row in history.reset_index().iterrows():
        date_value = row.get("Date") or row.get("Datetime") or idx
        candles.append(
            {
                "date": str(date_value)[:10],
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row.get("Volume", 0) or 0),
            }
        )
    return candles


def _load_previous_snapshot(out_dir: Path) -> dict[str, Any] | None:
    path = out_dir / "signal_snapshot.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _score_delta(value: object, previous: object) -> int:
    try:
        return int(round(float(value))) - int(round(float(previous)))
    except (TypeError, ValueError):
        return int(round(float(value or 0)))


def _format_delta(delta: int) -> str:
    return f"+{delta}" if delta > 0 else str(delta)


def _build_snapshot_factor_breakdown(market_state: dict[str, Any], previous_snapshot: dict[str, Any] | None, factor_deltas: dict[str, int]) -> list[dict[str, Any]]:
    factors = market_state.get("factor_scores") if isinstance(market_state.get("factor_scores"), dict) else {}
    previous_factors = previous_snapshot.get("factor_scores") if isinstance(previous_snapshot, dict) and isinstance(previous_snapshot.get("factor_scores"), dict) else {}
    current_breakdown = market_state.get("factor_breakdown") if isinstance(market_state.get("factor_breakdown"), list) else []
    by_key = {item.get("key"): item for item in current_breakdown if isinstance(item, dict)}
    breakdown: list[dict[str, Any]] = []
    for definition in MARKET_SCORE_FACTOR_DEFINITIONS:
        key = str(definition["key"])
        score = int(round(float(factors.get(key, 0) or 0)))
        previous_score = int(round(float(previous_factors.get(key, 0) or 0)))
        delta = int(factor_deltas.get(key, score - previous_score))
        base = dict(by_key.get(key) or {})
        base.update(
            {
                "key": key,
                "label": str(definition["label"]),
                "score": score,
                "previous_score": previous_score,
                "delta": delta,
                "contribution_label": f"{_format_delta(score)}점",
                "delta_label": f"{_format_delta(delta)}점",
                "description": str(definition["description"]),
            }
        )
        breakdown.append(base)
    return breakdown


def _build_change_fields(market_state: dict[str, Any], previous_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    factors = market_state.get("factor_scores") if isinstance(market_state.get("factor_scores"), dict) else {}
    previous_factors = previous_snapshot.get("factor_scores") if isinstance(previous_snapshot, dict) and isinstance(previous_snapshot.get("factor_scores"), dict) else {}
    factor_deltas = {key: _score_delta(factors.get(key, 0), previous_factors.get(key, 0)) for key in FACTOR_LABELS}
    explanations = [f"{FACTOR_LABELS[key]} {_format_delta(delta)}점" for key, delta in factor_deltas.items() if delta != 0]
    if not explanations:
        explanations = ["factor 변화 없음"]
    market_score = int(market_state.get("market_score") or 0)
    previous_score = previous_snapshot.get("market_score") if isinstance(previous_snapshot, dict) else None
    if previous_score is None:
        previous_score = market_score
    previous_score = int(round(float(previous_score)))
    score_delta = market_score - previous_score
    return {
        "score_base": market_state.get("score_base", 50),
        "score_formula": market_state.get("score_formula"),
        "previous_market_score": previous_score,
        "score_delta": score_delta,
        "factor_deltas": factor_deltas,
        "factor_breakdown": _build_snapshot_factor_breakdown(market_state, previous_snapshot, factor_deltas),
        "change_explanation": explanations,
        "change_summary": f"시장 점수 {previous_score} → {market_score} ({_format_delta(score_delta)}): {', '.join(explanations)}",
    }


def _append_history(out_dir: Path, snapshot: dict[str, Any]) -> None:
    history_path = out_dir / "signal_snapshot_history.jsonl"
    compact = {
        "timestamp": snapshot.get("timestamp") or snapshot.get("generated_at"),
        "generated_at": snapshot.get("generated_at"),
        "market_score": snapshot.get("market_score"),
        "previous_market_score": snapshot.get("previous_market_score"),
        "score_delta": snapshot.get("score_delta"),
        "risk_level": snapshot.get("risk_level"),
        "score_base": snapshot.get("score_base"),
        "score_formula": snapshot.get("score_formula"),
        "factor_scores": snapshot.get("factor_scores"),
        "factor_deltas": snapshot.get("factor_deltas"),
        "factor_breakdown": snapshot.get("factor_breakdown"),
        "change_explanation": snapshot.get("change_explanation"),
        "change_summary": snapshot.get("change_summary"),
    }
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(compact, ensure_ascii=False) + "\n")


def build_snapshot(symbols: list[str], factor_scores: dict[str, Any], *, previous_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    market_state = calculate_market_score(factor_scores)
    symbol_candles = {symbol: _download_candles(symbol) for symbol in symbols}
    snapshot = build_price_table(market_state, symbol_candles)
    snapshot["factor_scores"] = market_state.get("factor_scores", {})
    snapshot.update(_build_change_fields(market_state, previous_snapshot))
    snapshot["live_capital_allowed"] = False
    snapshot["paper_order_only"] = True
    snapshot["source"] = "yfinance_daily_ohlc_plus_operator_macro_scores"
    snapshot["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return snapshot


def update_snapshot_files(out_dir: Path, symbols: list[str], factor_scores: dict[str, Any]) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    previous = _load_previous_snapshot(out_dir)
    snapshot = build_snapshot(symbols, factor_scores, previous_snapshot=previous)
    out_path = out_dir / "signal_snapshot.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    _append_history(out_dir, snapshot)
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Update TradingAgents signal price table snapshot")
    parser.add_argument("--dashboard-dir", default="artifacts/dashboard-pilot-20260430")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--factor-json", default=None)
    args = parser.parse_args()

    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    factor_scores = _load_factor_scores(args.factor_json)
    out_dir = Path(args.dashboard_dir)
    snapshot = update_snapshot_files(out_dir, symbols, factor_scores)
    out_path = out_dir / "signal_snapshot.json"
    print(out_path)
    print(json.dumps({"market_score": snapshot.get("market_score"), "rows": len(snapshot.get("rows", []))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
