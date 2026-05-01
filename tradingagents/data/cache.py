from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9._-]+")


class MarketDataCache:
    """Small JSON cache for deterministic data reuse between agents/backtests."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    def path_for(self, ticker: str, trade_date: str, data_type: str) -> Path:
        safe_ticker = _SAFE_KEY_RE.sub("_", str(ticker).upper()).strip("._") or "UNKNOWN"
        safe_date = _SAFE_KEY_RE.sub("_", str(trade_date)).strip("._") or "latest"
        safe_type = _SAFE_KEY_RE.sub("_", str(data_type)).strip("._") or "data"
        return self.base_dir / safe_ticker / f"{safe_date}-{safe_type}.json"

    def get_or_fetch(self, ticker: str, trade_date: str, data_type: str, fetcher: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        path = self.path_for(ticker, trade_date, data_type)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        payload = fetcher()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return payload
