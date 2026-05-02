from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PaperTradingLedger:
    """Append-only paper trading signal ledger.

    This ledger records simulation signals only. It never authorizes live capital
    and rejects payloads that try to opt into live execution.
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

        signal = {
            "signal_id": f"paper-{uuid.uuid4().hex[:12]}",
            "created_at": _utc_now_iso(),
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
                "exit_price": None,
                "pnl": None,
                "return_percent": None,
            },
            "notes": str(payload.get("notes") or ""),
        }
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(signal, ensure_ascii=False) + "\n")
        return signal

    def list_signals(self, *, ticker: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        rows = self._read_all()
        if ticker:
            ticker_upper = ticker.upper()
            rows = [row for row in rows if str(row.get("ticker") or "").upper() == ticker_upper]
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows[max(offset, 0) : max(offset, 0) + max(limit, 1)]

    def count_signals(self, *, ticker: str | None = None) -> int:
        return len(self.list_signals(ticker=ticker, limit=1_000_000, offset=0))

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


def _required_float(payload: dict[str, Any], key: str) -> float:
    value = _optional_float(payload.get(key))
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{value!r} must be a finite number") from exc
    if not isfinite(parsed):
        raise ValueError(f"{value!r} must be a finite number")
    return parsed


def _optional_int(value: Any) -> int | None:
    parsed = _optional_float(value)
    return int(round(parsed)) if parsed is not None else None
