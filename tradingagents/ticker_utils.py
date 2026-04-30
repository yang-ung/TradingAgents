from __future__ import annotations

import re
from functools import lru_cache

import requests
import yfinance as yf
from bs4 import BeautifulSoup

_KOREAN_NUMERIC_TICKER_RE = re.compile(r"^\d{6}$")
_KOREAN_EXCHANGE_SUFFIXES = (".KS", ".KQ")
_KRX_CORP_LIST_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"


@lru_cache(maxsize=1)
def _krx_market_by_code() -> dict[str, str]:
    """Return a mapping of 6-digit KRX ticker codes to market labels from KRX."""
    response = requests.get(
        _KRX_CORP_LIST_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    table = soup.find("table")
    if table is None:
        return {}

    rows = table.find_all("tr")
    mapping: dict[str, str] = {}
    for row in rows[1:]:
        cols = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cols) < 3:
            continue
        market, code = cols[1], cols[2]
        if re.fullmatch(r"\d{6}", code):
            mapping[code] = market
    return mapping


def _lookup_krx_market(code: str) -> str | None:
    """Lookup KRX market label for a 6-digit stock code."""
    try:
        return _krx_market_by_code().get(code)
    except Exception:
        return None


def _has_price_history(symbol: str) -> bool:
    """Return True when Yahoo Finance returns at least one recent bar for symbol."""
    history = yf.Ticker(symbol).history(period="5d")
    return history is not None and not history.empty


def normalize_ticker_symbol(ticker: str) -> str:
    """Normalize ticker input and auto-resolve Korean market suffixes for 6-digit codes."""
    normalized = ticker.strip().upper()
    if not normalized:
        return normalized

    if any(normalized.endswith(suffix) for suffix in _KOREAN_EXCHANGE_SUFFIXES):
        return normalized

    if _KOREAN_NUMERIC_TICKER_RE.fullmatch(normalized):
        market = _lookup_krx_market(normalized)
        if market == "코스피":
            return f"{normalized}.KS"
        if market == "코스닥":
            return f"{normalized}.KQ"

        for suffix in _KOREAN_EXCHANGE_SUFFIXES:
            candidate = f"{normalized}{suffix}"
            try:
                if _has_price_history(candidate):
                    return candidate
            except Exception:
                continue
        return f"{normalized}.KS"

    return normalized


def get_market_benchmark_symbol(ticker: str) -> str:
    """Return an index ETF/symbol used as the alpha benchmark for the ticker's market."""
    normalized = normalize_ticker_symbol(ticker)
    if normalized.endswith(".KS") or normalized.endswith(".KQ"):
        return "^KS11"
    return "SPY"
