from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PaperTradingLedger:
    """Append-only paper trading signal ledger.

    This ledger records simulation signals only. It never authorizes live capital
    and rejects payloads that try to opt into live execution. Updates append a
    new materialized snapshot with the same signal_id; list APIs collapse to the
    latest snapshot per signal.
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.ledger_dir = self.base_dir / "paper_trading"
        self.ledger_path = self.ledger_dir / "signals.jsonl"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

    def record_signal(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        if payload.get("live_capital_allowed") is True:
            raise ValueError("paper trading ledger cannot allow live capital")
        ticker = str(payload.get("ticker") or "").strip().upper()
        if not ticker:
            raise ValueError("ticker is required")

        now = _utc_now_iso()
        signal = {
            "signal_id": f"paper-{uuid.uuid4().hex[:12]}",
            "created_at": now,
            "updated_at": now,
            "status": "paper_trading",
            "live_capital_allowed": False,
            "run_id": str(payload.get("run_id") or ""),
            "ticker": ticker,
            "trade_date": str(payload.get("trade_date") or ""),
            "strategy_id": str(payload.get("strategy_id") or ""),
            "signal_date": str(payload.get("signal_date") or payload.get("trade_date") or ""),
            "current_price": _optional_float(payload.get("current_price")),
            "levels": {
                "entry_low": _required_float(payload, "entry_low"),
                "entry_high": _required_float(payload, "entry_high"),
                "take_profit": _required_float(payload, "take_profit"),
                "stop_loss": _required_float(payload, "stop_loss"),
            },
            "scores": {
                "direction_score": _optional_int(payload.get("direction_score")),
                "entry_timing_score": _optional_int(payload.get("entry_timing_score")),
                "market_risk_score": _optional_int(payload.get("market_risk_score")),
            },
            "pre_live_status_label": str(payload.get("pre_live_status_label") or ""),
            "fill": {
                "fillable": None,
                "assumed_fill_price": None,
                "entry_date": None,
                "exit_price": None,
                "exit_date": None,
                "exit_reason": None,
                "pnl": None,
                "return_percent": None,
                "cost_drag_percent": None,
            },
            "notes": str(payload.get("notes") or ""),
        }
        self._append(signal)
        return signal

    def update_signal_with_ohlc(
        self,
        signal_id: str,
        candles: list[dict[str, Any]],
        *,
        capital: float = 100_000_000,
        commission_bps: float = 5.0,
        slippage_bps: float = 10.0,
    ) -> dict[str, Any]:
        signal = self.get_signal(signal_id)
        if signal is None:
            raise ValueError(f"unknown signal_id: {signal_id}")
        if not isinstance(candles, list) or not candles:
            raise ValueError("candles are required")
        capital_value = _required_positive(capital, "capital")
        cost_drag = 2.0 * (_required_non_negative(commission_bps, "commission_bps") + _required_non_negative(slippage_bps, "slippage_bps")) / 100.0
        if signal.get("status") == "paper_closed" or (signal.get("fill") or {}).get("exit_price") is not None:
            return signal

        updated = deepcopy(signal)
        updated["live_capital_allowed"] = False
        updated["updated_at"] = _utc_now_iso()
        updated["fill"] = self._evaluate_fill(updated, candles, capital=capital_value, cost_drag_percent=cost_drag)
        if updated["fill"].get("exit_price") is not None:
            updated["status"] = "paper_closed"
        elif updated["fill"].get("fillable") is True:
            updated["status"] = "paper_open"
        else:
            updated["status"] = "paper_not_filled"
        self._append(updated)
        return updated

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        if not signal_id:
            return None
        latest = self._latest_by_signal_id()
        return latest.get(signal_id)

    def list_signals(self, *, ticker: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        rows = list(self._latest_by_signal_id().values())
        if ticker:
            ticker_upper = ticker.upper()
            rows = [row for row in rows if str(row.get("ticker") or "").upper() == ticker_upper]
        rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
        return rows[max(offset, 0) : max(offset, 0) + max(limit, 1)]

    def count_signals(self, *, ticker: str | None = None) -> int:
        return len(self.list_signals(ticker=ticker, limit=1_000_000, offset=0))

    def _evaluate_fill(self, signal: dict[str, Any], candles: list[dict[str, Any]], *, capital: float, cost_drag_percent: float) -> dict[str, Any]:
        levels = signal.get("levels") or {}
        entry_low = _required_float(levels, "entry_low")
        entry_high = _required_float(levels, "entry_high")
        take_profit = _required_float(levels, "take_profit")
        stop_loss = _required_float(levels, "stop_loss")
        if entry_low > entry_high:
            raise ValueError("entry_low must be <= entry_high")

        existing_fill = signal.get("fill") if isinstance(signal.get("fill"), dict) else {}
        if existing_fill.get("fillable") is True and existing_fill.get("assumed_fill_price") is not None:
            fill = deepcopy(existing_fill)
            fill["cost_drag_percent"] = round(cost_drag_percent, 4)
            entry_price = _required_finite_number(fill.get("assumed_fill_price"), "assumed_fill_price")
        else:
            fill = {
                "fillable": False,
                "assumed_fill_price": None,
                "entry_date": None,
                "exit_price": None,
                "exit_date": None,
                "exit_reason": None,
                "pnl": None,
                "return_percent": None,
                "cost_drag_percent": round(cost_drag_percent, 4),
            }
            entry_price = None
        last_close: float | None = None
        existing_entry_date = str(fill.get("entry_date") or "") if entry_price is not None else ""
        for candle in sorted(candles, key=lambda row: str(row.get("date") or "")):
            date = str(candle.get("date") or "")
            if existing_entry_date and date <= existing_entry_date:
                continue
            low = _required_float(candle, "low")
            high = _required_float(candle, "high")
            close = _optional_float(candle.get("close"))
            last_close = close if close is not None else last_close
            if entry_price is None:
                if low <= entry_high and high >= entry_low:
                    entry_price = entry_high
                    fill["fillable"] = True
                    fill["assumed_fill_price"] = round(entry_price, 4)
                    fill["entry_date"] = date
                else:
                    continue

            # Conservative daily-bar ordering: when target and stop both touch in
            # the same candle, assume the stop happened first.
            if low <= stop_loss:
                self._close_fill(fill, entry_price, stop_loss, date, "손절", capital, cost_drag_percent)
                return fill
            if high >= take_profit:
                self._close_fill(fill, entry_price, take_profit, date, "익절", capital, cost_drag_percent)
                return fill

        if entry_price is not None and last_close is not None:
            ret = (last_close / entry_price - 1.0) * 100.0 - cost_drag_percent
            rounded_ret = round(ret, 2)
            fill["unrealized_price"] = round(last_close, 4)
            fill["unrealized_return_percent"] = rounded_ret
            fill["unrealized_pnl"] = int(round(capital * rounded_ret / 100.0))
        return fill

    @staticmethod
    def _close_fill(fill: dict[str, Any], entry_price: float, exit_price: float, date: str, reason: str, capital: float, cost_drag_percent: float) -> None:
        ret = (exit_price / entry_price - 1.0) * 100.0 - cost_drag_percent
        rounded_ret = round(ret, 2)
        for key in ("unrealized_price", "unrealized_return_percent", "unrealized_pnl"):
            fill.pop(key, None)
        fill["exit_price"] = round(exit_price, 4)
        fill["exit_date"] = date
        fill["exit_reason"] = reason
        fill["return_percent"] = rounded_ret
        fill["pnl"] = int(round(capital * rounded_ret / 100.0))

    def _append(self, signal: dict[str, Any]) -> None:
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(signal, ensure_ascii=False) + "\n")

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
        return rows

    def _latest_by_signal_id(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in self._read_all():
            signal_id = str(row.get("signal_id") or "")
            if not signal_id:
                continue
            latest[signal_id] = row
        return latest


def _required_float(payload: dict[str, Any], key: str) -> float:
    value = _optional_float(payload.get(key))
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _required_finite_number(value, "value")


def _required_finite_number(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite number")
    return parsed


def _required_positive(value: Any, field_name: str) -> float:
    parsed = _required_finite_number(value, field_name)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be a positive finite number")
    return parsed


def _required_non_negative(value: Any, field_name: str) -> float:
    parsed = _required_finite_number(value, field_name)
    if parsed < 0:
        raise ValueError(f"{field_name} must be a non-negative finite number")
    return parsed


def _optional_int(value: Any) -> int | None:
    parsed = _optional_float(value)
    return int(round(parsed)) if parsed is not None else None
