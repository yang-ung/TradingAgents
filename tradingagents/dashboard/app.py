from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .backtest import build_strategy_backtest, build_strategy_execution_replay
from tradingagents.strategies import evaluate_reanalysis_triggers, evaluate_signal
from tradingagents.strategies.schema import parse_strategy_spec
from .batch import DEFAULT_ARTIFACT_DIR, run_batch_analysis
from .charts import get_price_chart
from .extract import make_snippet
from .scorecard import build_decision_scorecard
from .storage import AnalysisRepository
from tradingagents.ticker_utils import get_krx_company_info
from tradingagents.validation.paper_trading import PaperTradingLedger
from tradingagents.automation.crypto_signal_engine import (
    CRYPTO_BINANCE_SYMBOLS,
    build_live_crypto_chart_payload,
    run_crypto_live_paper_execution,
)
from tradingagents.automation.cross_market_context import build_cross_market_context_from_files
from tradingagents.validation.pre_live import build_batch_pre_live_validation_report, build_pre_live_validation_report

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DATA_DIR = Path(os.getenv("TRADINGAGENTS_DASHBOARD_DIR", str(DEFAULT_ARTIFACT_DIR))).resolve()
MAX_BATCH_TICKERS = int(os.getenv("TRADINGAGENTS_DASHBOARD_MAX_BATCH_TICKERS", "20"))
MAX_ACTIVE_BATCH_JOBS = int(os.getenv("TRADINGAGENTS_DASHBOARD_MAX_ACTIVE_BATCH_JOBS", "3"))
BATCH_WORKERS = int(os.getenv("TRADINGAGENTS_DASHBOARD_BATCH_WORKERS", "1"))
TICKER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,20}$")
_KNOWN_COMPANY_NAMES = {
    "005930": "삼성전자",
    "005930.KS": "삼성전자",
    "000660": "SK하이닉스",
    "000660.KS": "SK하이닉스",
    "035420": "NAVER",
    "035420.KS": "NAVER",
    "035720": "카카오",
    "035720.KQ": "카카오",
    "005380": "현대차",
    "005380.KS": "현대차",
    "068270": "셀트리온",
    "068270.KS": "셀트리온",
    "NVDA": "NVIDIA",
}
_COMPANY_NAME_TO_TICKER = {
    "삼성전자": "005930.KS",
    "SK하이닉스": "000660.KS",
    "셀트리온": "068270.KS",
    "NAVER": "035420.KS",
    "네이버": "035420.KS",
    "카카오": "035720.KQ",
    "현대차": "005380.KS",
    "NVIDIA": "NVDA",
    "엔비디아": "NVDA",
}


def _resolve_ticker_alias(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _COMPANY_NAME_TO_TICKER.get(text, _COMPANY_NAME_TO_TICKER.get(text.upper(), text))


def _display_company_name(record: dict) -> str:
    ticker = str(record.get("ticker", "")).upper()
    ticker_code = ticker.split(".", 1)[0]
    sources = [record.get("metadata", {}) or {}, record.get("raw_state", {}) or {}]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("company_name", "display_name", "name"):
            value = str(source.get(key, "")).strip()
            if value and value.upper() not in {ticker, ticker_code}:
                return value
    krx_info = get_krx_company_info(ticker)
    if krx_info:
        krx_name = str(krx_info.get("name") or "").strip()
        if krx_name and krx_name.upper() not in {ticker, ticker_code}:
            return krx_name
    return _KNOWN_COMPANY_NAMES.get(ticker, _KNOWN_COMPANY_NAMES.get(ticker_code, ticker))


def _display_label(record: dict) -> str:
    """Primary user-facing label: company/name first, no raw ticker for known names."""
    ticker = str(record.get("ticker") or "").strip()
    company_name = _display_company_name(record).strip()
    if company_name and company_name.upper() != ticker.upper():
        return company_name
    return ticker


def _display_label_with_code(record: dict) -> str:
    """Secondary/admin label when an identifier is intentionally needed."""
    ticker = str(record.get("ticker") or "").strip()
    company_name = _display_company_name(record).strip()
    if company_name and company_name.upper() != ticker.upper():
        return f"{company_name} ({ticker})"
    return ticker


def _display_ticker_name(ticker: object) -> str:
    return _display_label({"ticker": str(ticker or "").strip()})


def _has_hangul_final_consonant(value: str) -> bool:
    for char in reversed(value.strip()):
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            return (code - 0xAC00) % 28 != 0
        if char.isalnum():
            return False
    return False


def _match_korean_particle(company_name: str, particle: str) -> str:
    has_final = _has_hangul_final_consonant(company_name)
    if particle in {"은", "는"}:
        return "은" if has_final else "는"
    if particle in {"이", "가"}:
        return "이" if has_final else "가"
    if particle in {"을", "를"}:
        return "을" if has_final else "를"
    return particle


def _replace_ticker_with_company_name(record: dict, value: object) -> object:
    """Return a display-only copy where known ticker codes in prose use company names."""
    ticker = str(record.get("ticker") or "").strip()
    company_name = _display_company_name(record).strip()
    ticker_code = ticker.split(".", 1)[0]
    is_korean_code = ticker.upper().endswith((".KS", ".KQ")) or ticker_code.isdigit()
    if not ticker or not company_name or company_name.upper() == ticker.upper() or not is_korean_code:
        return value
    patterns = [ticker]
    if ticker_code and ticker_code != ticker:
        patterns.append(ticker_code)

    def replace_text(text: str) -> str:
        replaced = text
        for pattern in patterns:
            escaped = re.escape(pattern)
            replaced = re.sub(
                rf"(?<![A-Za-z0-9]){escaped}(?P<particle>[은는이가을를])(?![A-Za-z0-9])",
                lambda match: company_name + _match_korean_particle(company_name, match.group("particle")),
                replaced,
            )
            replaced = re.sub(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", company_name, replaced)
        return replaced

    if isinstance(value, str):
        return replace_text(value)
    if isinstance(value, list):
        return [_replace_ticker_with_company_name(record, item) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_ticker_with_company_name(record, item) for item in value)
    if isinstance(value, dict):
        return {key: _replace_ticker_with_company_name(record, item) for key, item in value.items()}
    return value


_TECHNICAL_TERM_PATTERNS = (
    (re.compile(r"(?<![A-Za-z0-9])(?P<period>\d{1,3})\s*SMA(?![A-Za-z0-9])", re.IGNORECASE), lambda m: f"{m.group('period')}일 단순이동평균"),
    (re.compile(r"(?<![A-Za-z0-9])SMA\s*(?P<period>\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE), lambda m: f"{m.group('period')}일 단순이동평균"),
    (re.compile(r"(?<![A-Za-z0-9])(?P<period>\d{1,3})\s*EMA(?![A-Za-z0-9])", re.IGNORECASE), lambda m: f"{m.group('period')}일 지수이동평균"),
    (re.compile(r"(?<![A-Za-z0-9])EMA\s*(?P<period>\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE), lambda m: f"{m.group('period')}일 지수이동평균"),
    (re.compile(r"(?<![A-Za-z0-9])MA\s*(?P<period>\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE), lambda m: f"{m.group('period')}일 이동평균"),
    (re.compile(r"(?<![A-Za-z0-9])RSI\s*(?P<period>\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE), lambda m: f"{m.group('period')}일 상대강도지수"),
    (re.compile(r"(?<![A-Za-z0-9])ATR\s*(?P<period>\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE), lambda m: f"{m.group('period')}일 평균변동폭"),
    (re.compile(r"(?<![A-Za-z0-9])RSI(?![A-Za-z0-9])", re.IGNORECASE), lambda m: "상대강도지수"),
    (re.compile(r"(?<![A-Za-z0-9])ATR(?![A-Za-z0-9])", re.IGNORECASE), lambda m: "평균변동폭"),
    (re.compile(r"(?<![A-Za-z0-9])MACD(?![A-Za-z0-9])", re.IGNORECASE), lambda m: "이동평균 수렴·확산 지표"),
    (re.compile(r"(?<![A-Za-z0-9])SMA(?![A-Za-z0-9])", re.IGNORECASE), lambda m: "단순이동평균"),
    (re.compile(r"(?<![A-Za-z0-9])EMA(?![A-Za-z0-9])", re.IGNORECASE), lambda m: "지수이동평균"),
)


def _localize_technical_terms(value: object) -> object:
    """Display-only localization for common chart/technical-analysis acronyms."""
    if isinstance(value, str):
        localized = value
        for pattern, replacement in _TECHNICAL_TERM_PATTERNS:
            localized = pattern.sub(replacement, localized)
        return localized
    if isinstance(value, list):
        return [_localize_technical_terms(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_localize_technical_terms(item) for item in value)
    if isinstance(value, dict):
        return {key: _localize_technical_terms(item) for key, item in value.items()}
    return value


def _prepare_display_text(record: dict, value: object) -> object:
    return _localize_technical_terms(_replace_ticker_with_company_name(record, value))


def _is_krw_currency(currency: object) -> bool:
    return str(currency or "").strip().upper() in {"KRW", "₩", "원"}


def _format_money(value: object, currency: object = None) -> str:
    try:
        amount = float(value)
    except Exception:
        return "-"
    if _is_krw_currency(currency):
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def _format_integer_display(value: object) -> str:
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return "-"


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _format_quote_krw_display(value: object, quote_to_krw: float) -> str:
    amount = _safe_float(value, 0.0)
    if amount <= 0:
        return "-"
    label = _format_integer_display(amount * quote_to_krw)
    return f"{label}원" if quote_to_krw != 1.0 else label


def _format_entry_zone_display(row: Mapping[str, Any], quote_to_krw: float = 1.0) -> str:
    def fmt(value: object) -> str:
        return _format_quote_krw_display(value, quote_to_krw) if quote_to_krw != 1.0 else _format_integer_display(value)
    if row.get("entry_low") is not None and row.get("entry_high") is not None:
        return f"{fmt(row.get('entry_low'))} ~ {fmt(row.get('entry_high'))}"
    raw = str(row.get("entry_zone_label") or "").strip()
    if not raw:
        return "-"
    numbers = re.findall(r"-?\d+(?:\.\d+)?", raw)
    if len(numbers) >= 2:
        return f"{fmt(numbers[0])} ~ {fmt(numbers[1])}"
    if len(numbers) == 1:
        return fmt(numbers[0])
    return raw


def _format_signed_percent(value: object) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if amount >= 0 else ""
    return f"{sign}{amount:.2f}%"


def _sample_status_label(trade_count: object) -> str:
    try:
        count = int(trade_count)
    except (TypeError, ValueError):
        count = 0
    if count >= 20:
        return "강 후보"
    if count >= 5:
        return "탐색 후보"
    return "참고용"


def _load_signal_snapshot(base_dir: Path) -> dict[str, Any]:
    path = base_dir / "signal_snapshot.json"
    if not path.exists():
        return {"available": False, "reason": "signal_snapshot_unavailable", "rows": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "reason": "signal_snapshot_invalid", "rows": []}
    if not isinstance(payload, dict):
        return {"available": False, "reason": "signal_snapshot_invalid", "rows": []}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    prepared_rows: list[dict[str, Any]] = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        prepared = dict(row)
        prepared["live_capital_allowed"] = False
        prepared["live_capital_allowed_label"] = "live_capital_allowed=false"
        prepared_rows.append(prepared)
    snapshot = dict(payload)
    snapshot["available"] = True
    snapshot["live_capital_allowed"] = False
    snapshot["paper_order_only"] = True
    snapshot["rows"] = prepared_rows
    snapshot["risk_level_label"] = {
        "strong_risk_on": "강한 risk-on",
        "weak_risk_on": "약한 risk-on",
        "neutral": "중립",
        "risk_off": "risk-off",
        "strong_risk_off": "강한 risk-off",
    }.get(str(payload.get("risk_level") or ""), str(payload.get("risk_level") or "-"))
    return snapshot


def _find_snapshot_row(snapshot: Mapping[str, Any], ticker: str) -> dict[str, Any] | None:
    normalized = str(ticker or "").strip().upper()
    rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("ticker") or "").strip().upper() == normalized:
            return dict(row)
    return None


def _display_signal_number(value: object, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    try:
        number = float(value)
    except Exception:
        return str(value)
    if not math.isfinite(number):
        return "-"
    if abs(number) >= 1000:
        rendered = f"{number:,.0f}"
    else:
        rendered = f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def _prepare_etf_signal_detail(row: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "-")
    action = str(row.get("action") or "관망")
    status = str(row.get("status_label") or "전략 점검")
    thesis = f"{ticker}는 시장점수 {snapshot.get('market_score', '-')}, 패턴점수 {row.get('pattern_score', '-')} 기준으로 {status} 상태입니다."
    return {
        "title": ticker,
        "subtitle": "ETF/주식 가격 레벨 전략 상세",
        "eyebrow": "ETF Strategy Detail",
        "back_href": "/dashboards/etf",
        "back_label": "ETF 전략",
        "api_href": "/api/signals/price-table",
        "score_label": "시장/패턴",
        "score_value": f"{row.get('market_score', snapshot.get('market_score', '-'))} / {row.get('pattern_score', '-')}",
        "action_label": action,
        "thesis": thesis,
        "opinion": None,
        "chart_endpoint": None,
        "metrics": [
            {"label": "현재가", "value": _display_signal_number(row.get("current_price")), "note": "스냅샷 기준"},
            {"label": "상태", "value": status, "note": "가격·패턴 조건"},
            {"label": "진입구간", "value": str(row.get("entry_zone_label") or "-"), "note": "조건부 진입 기준"},
            {"label": "목표가", "value": _display_signal_number(row.get("take_profit_1")), "note": "1차 목표"},
        ],
        "rows": [
            {"label": "종목", "value": ticker},
            {"label": "현재가", "value": _display_signal_number(row.get("current_price"))},
            {"label": "상태", "value": status},
            {"label": "현재 액션", "value": action},
            {"label": "진입구간", "value": str(row.get("entry_zone_label") or "-")},
            {"label": "손절가", "value": _display_signal_number(row.get("stop_loss"))},
            {"label": "목표가", "value": _display_signal_number(row.get("take_profit_1"))},
            {"label": "시장 점수", "value": str(row.get("market_score", snapshot.get("market_score", "-")))},
            {"label": "패턴 점수", "value": str(row.get("pattern_score", "-"))},
        ],
    }


def _prepare_crypto_signal_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "-")
    opinion = row.get("strategy_agent_opinion") if isinstance(row.get("strategy_agent_opinion"), dict) else _crypto_strategy_agent_opinion(row)
    action = str(row.get("display_action") or opinion.get("action") or row.get("action") or "관망")
    return {
        "title": ticker,
        "subtitle": "암호화폐 개별 전략 상세",
        "eyebrow": "Crypto Strategy Detail",
        "back_href": "/dashboards/crypto",
        "back_label": "암호화폐 전략",
        "api_href": "/api/signals/crypto",
        "score_label": "신호 점수",
        "score_value": str(row.get("signal_score", "-")),
        "action_label": action,
        "thesis": str(opinion.get("thesis") or row.get("strategy_commentary") or "전략 의견을 확인합니다."),
        "opinion": opinion,
        "chart_endpoint": f"/api/signals/crypto/chart/{ticker}",
        "metrics": [
            {"label": "현재가", "value": str(row.get("current_price_label") or "-"), "note": "KRW 환산"},
            {"label": "현재 전략", "value": action, "note": "1시간 전략 기준"},
            {"label": "매수가", "value": str(row.get("entry_zone_display") or row.get("entry_zone_label") or "-"), "note": "진입구간"},
            {"label": "목표가", "value": str(row.get("take_profit_1_label") or "-"), "note": "1차 목표"},
        ],
        "rows": [
            {"label": "코인", "value": ticker},
            {"label": "현재가", "value": str(row.get("current_price_label") or "-")},
            {"label": "신호 점수", "value": str(row.get("signal_score", "-"))},
            {"label": "현재 전략", "value": action},
            {"label": "매수가", "value": str(row.get("entry_zone_display") or row.get("entry_zone_label") or "-")},
            {"label": "손절가", "value": str(row.get("stop_loss_label") or "-")},
            {"label": "목표가", "value": str(row.get("take_profit_1_label") or "-")},
            {"label": "전략 에이전트 의견", "value": str(opinion.get("thesis") or "-")},
        ],
    }


def _load_hourly_report(base_dir: Path) -> dict[str, Any]:
    path = base_dir / "hourly_report.json"
    if not path.exists():
        return {"available": False, "reason": "hourly_report_unavailable", "stock_signals": [], "crypto_signals": [], "dashboard_updates": [], "api_checks": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "reason": "hourly_report_invalid", "stock_signals": [], "crypto_signals": [], "dashboard_updates": [], "api_checks": []}
    if not isinstance(payload, dict):
        return {"available": False, "reason": "hourly_report_invalid", "stock_signals": [], "crypto_signals": [], "dashboard_updates": [], "api_checks": []}
    report = dict(payload)
    report["available"] = True
    report["stock_signals"] = report.get("stock_signals") if isinstance(report.get("stock_signals"), list) else []
    report["crypto_signals"] = report.get("crypto_signals") if isinstance(report.get("crypto_signals"), list) else []
    report["factor_breakdown"] = report.get("factor_breakdown") if isinstance(report.get("factor_breakdown"), list) else []
    report["dashboard_updates"] = report.get("dashboard_updates") if isinstance(report.get("dashboard_updates"), list) else []
    report["api_checks"] = report.get("api_checks") if isinstance(report.get("api_checks"), list) else []
    safety = report.get("safety") if isinstance(report.get("safety"), dict) else {}
    report["safety"] = {
        "live_capital_allowed": False,
        "paper_order_only": True,
        "label": safety.get("label") or "실거래 차단 · 모의주문 전용",
    }
    return report


def _load_cross_market_context(repository: AnalysisRepository) -> dict[str, Any]:
    base_dir = repository.base_dir
    source_paths = [base_dir / "signal_snapshot.json", base_dir / "crypto_signal_snapshot.json", base_dir / "hourly_report.json"]
    latest_source_mtime = max((source.stat().st_mtime for source in source_paths if source.exists()), default=0.0)
    source_updated_at = str(latest_source_mtime)
    cached = repository.get_cross_market_context()
    if cached and cached.get("available") and (str(cached.get("source_updated_at") or "") == source_updated_at or latest_source_mtime == 0.0):
        return cached
    context = build_cross_market_context_from_files(base_dir)
    if context.get("available"):
        repository.save_cross_market_context(context, source_updated_at=source_updated_at)
        context = dict(context)
        context["state_persistence"] = "sqlite"
        context["source_updated_at"] = source_updated_at
    return context


def _display_trade_event(event: object) -> str:
    text = str(event or "").strip()
    replacements = {
        "모의매수": "매수",
        "실시간 모의매수": "매수",
        "모의매도(익절)": "매도 · 익절",
        "모의매도(손절)": "매도 · 손절",
        "실시간 모의매도(익절)": "매도 · 익절",
        "실시간 모의매도(손절)": "매도 · 손절",
        "모의보유": "보유중",
        "실시간 모의보유": "보유중",
        "모의관찰": "관망",
    }
    return replacements.get(text, text.replace("모의", "").strip() or "-")


def _display_strategy_action(action: object) -> str:
    text = str(action or "").strip()
    replacements = {
        "모의매수 후보": "매수 대기",
        "모의관찰": "관망",
        "모의보유": "유지",
        "실시간 모의보유": "유지",
    }
    return replacements.get(text, text.replace("모의", "").strip() or "-")


def _crypto_strategy_agent_opinion(row: Mapping[str, Any]) -> dict[str, str]:
    ticker = str(row.get("ticker") or "-")
    score = _safe_float(row.get("signal_score"), 0.0)
    action = _display_strategy_action(row.get("paper_execution_label") or row.get("action"))
    entry_zone = str(row.get("entry_zone_display") or row.get("entry_zone_label") or "-")
    stop_loss = str(row.get("stop_loss_label") or "-")
    take_profit = str(row.get("take_profit_1_label") or "-")
    commentary = _commercial_commentary(row.get("strategy_commentary"))
    if score >= 75:
        tone = "positive"
        thesis = f"{ticker}는 추세·눌림 점수가 우위라 진입구간 접근 시 매수 대기 의견입니다."
        confidence = "높음"
    elif score >= 60:
        tone = "neutral"
        thesis = f"{ticker}는 방향성은 유지되지만 확인이 필요해 조건부 관찰 의견입니다."
        confidence = "보통"
    else:
        tone = "warning"
        thesis = f"{ticker}는 신호 강도가 약해 신규 진입보다 관망이 우선입니다."
        confidence = "낮음"
    if commentary:
        thesis = commentary
    return {
        "ticker": ticker,
        "action": action,
        "thesis": thesis,
        "timing": f"진입 {entry_zone} · 목표 {take_profit}",
        "risk": f"손절 {stop_loss}",
        "confidence": confidence,
        "tone": tone,
    }


def _commercial_commentary(text: object) -> str:
    value = str(text or "")
    value = value.replace("실거래가 아닌 paper 계좌 모의투자로만 실행합니다.", "")
    value = value.replace("실거래가 아닌 paper 계좌 모의투자로만 실행합니다", "")
    value = value.replace("paper 계좌 수익률 기준 전략 유지, ", "")
    value = value.replace("paper 계좌 수익률 기준", "수익률 기준")
    value = value.replace("paper 계좌", "")
    value = re.sub(r"LLM 시장뷰는 [^,\.]+[로으로]+ 반영했으며,?\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ,.")
    return str(_localize_technical_terms(value))


def _format_signed_won(value: object) -> str:
    try:
        amount = float(value)
    except Exception:
        return "-"
    sign = "+" if amount >= 0 else ""
    return f"{sign}{int(amount):,}원"


def _prepare_crypto_position(position: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(position)
    item["quantity_label"] = item.get("quantity_label") or f"{float(item.get('quantity') or 0):,.8f}".rstrip("0").rstrip(".")
    item["entry_price_label"] = item.get("entry_price_label") or _format_integer_display(item.get("entry_price"))
    item["current_price_label"] = item.get("current_price_label") or _format_integer_display(item.get("current_price"))
    item["market_value_label"] = item.get("market_value_label") or _format_integer_display(item.get("market_value"))
    item["unrealized_pnl_label"] = item.get("unrealized_pnl_label") or _format_signed_won(item.get("unrealized_pnl"))
    item["unrealized_return_label"] = item.get("unrealized_return_label") or _format_signed_percent(item.get("unrealized_return_percent"))
    item["stop_loss_label"] = item.get("stop_loss_label") or _format_integer_display(item.get("stop_loss"))
    item["take_profit_1_label"] = item.get("take_profit_1_label") or _format_integer_display(item.get("take_profit_1"))
    item["display_action"] = _display_strategy_action(item.get("action") or item.get("status"))
    return item


def _load_crypto_signal_snapshot(base_dir: Path) -> dict[str, Any]:
    path = base_dir / "crypto_signal_snapshot.json"
    if not path.exists():
        return {"available": False, "reason": "crypto_signal_snapshot_unavailable", "rows": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "reason": "crypto_signal_snapshot_invalid", "rows": []}
    if not isinstance(payload, dict):
        return {"available": False, "reason": "crypto_signal_snapshot_invalid", "rows": []}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    quote_to_krw = 1.0
    try:
        quote_to_krw = _fetch_usdkrw_rate()
    except Exception:
        portfolio = payload.get("paper_portfolio") if isinstance(payload.get("paper_portfolio"), dict) else {}
        quote_to_krw = _safe_float(portfolio.get("quote_to_krw"), 1.0)
    prepared_rows: list[dict[str, Any]] = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        prepared = dict(row)
        prepared["live_capital_allowed"] = False
        prepared["live_capital_allowed_label"] = "live_capital_allowed=false"
        prepared["paper_order_only"] = True
        prepared["display_action"] = _display_strategy_action(prepared.get("paper_execution_label") or prepared.get("action"))
        prepared["strategy_commentary"] = _commercial_commentary(prepared.get("strategy_commentary"))
        prepared["current_price_label"] = _format_quote_krw_display(prepared.get("current_price"), quote_to_krw)
        prepared["entry_zone_display"] = _format_entry_zone_display(prepared, quote_to_krw)
        prepared["stop_loss_label"] = _format_quote_krw_display(prepared.get("stop_loss"), quote_to_krw) if prepared.get("stop_loss") is not None else "-"
        prepared["take_profit_1_label"] = _format_quote_krw_display(prepared.get("take_profit_1"), quote_to_krw) if prepared.get("take_profit_1") is not None else "-"
        detail = str(prepared.get("paper_execution_detail") or "").strip()
        if detail in {"보유 포지션을 현재가로 평가", "Binance 실시간 가격으로 paper 계좌 평가손익 갱신"}:
            detail = ""
        prepared["paper_execution_detail"] = detail
        prepared["strategy_agent_opinion"] = _crypto_strategy_agent_opinion(prepared)
        prepared_rows.append(prepared)
    snapshot = dict(payload)
    snapshot["available"] = True
    snapshot["live_capital_allowed"] = False
    snapshot["paper_order_only"] = True
    snapshot["rows"] = prepared_rows
    market_view = snapshot.get("llm_market_view") if isinstance(snapshot.get("llm_market_view"), dict) else {}
    if market_view:
        prepared_view = dict(market_view)
        prepared_view["display_summary"] = _commercial_commentary(prepared_view.get("summary"))
        prepared_view["display_summary"] = prepared_view["display_summary"].replace("모의전략", "전략").replace("모의투자만 허용", "전략 점검")
        snapshot["llm_market_view"] = prepared_view
    revision = snapshot.get("strategy_revision") if isinstance(snapshot.get("strategy_revision"), dict) else {}
    if revision:
        prepared_revision = dict(revision)
        prepared_revision["display_reason"] = _commercial_commentary(prepared_revision.get("reason"))
        snapshot["strategy_revision"] = prepared_revision
    portfolio = snapshot.get("paper_portfolio") if isinstance(snapshot.get("paper_portfolio"), dict) else {}
    if portfolio:
        prepared_portfolio = dict(portfolio)
        prepared_portfolio["portfolio_value_label"] = prepared_portfolio.get("portfolio_value_label") or _format_integer_display(prepared_portfolio.get("portfolio_value"))
        prepared_portfolio["cash_label"] = prepared_portfolio.get("cash_label") or _format_integer_display(prepared_portfolio.get("cash"))
        prepared_positions: list[dict[str, Any]] = []
        for position in prepared_portfolio.get("positions", []) if isinstance(prepared_portfolio.get("positions"), list) else []:
            if isinstance(position, dict):
                prepared_positions.append(_prepare_crypto_position(position))
        prepared_portfolio["positions"] = prepared_positions
        prepared_history: list[dict[str, Any]] = []
        for trade in prepared_portfolio.get("trade_history", []) if isinstance(prepared_portfolio.get("trade_history"), list) else []:
            if not isinstance(trade, dict):
                continue
            prepared_trade = dict(trade)
            prepared_trade["price_label"] = prepared_trade.get("price_label") or _format_integer_display(prepared_trade.get("price"))
            prepared_trade["notional_label"] = prepared_trade.get("notional_label") or _format_integer_display(prepared_trade.get("notional"))
            reason = str(prepared_trade.get("reason") or "").strip()
            if reason in {"보유 포지션을 현재가로 평가", "Binance 실시간 가격으로 paper 계좌 평가손익 갱신"}:
                reason = ""
            prepared_trade["display_event"] = _display_trade_event(prepared_trade.get("event"))
            if prepared_trade["display_event"] == "보유중":
                continue
            prepared_history.append(prepared_trade)
        prepared_portfolio["trade_history"] = prepared_history
        snapshot["paper_portfolio"] = prepared_portfolio
    return snapshot


def _load_crypto_live_paper_state(base_dir: Path) -> dict[str, Any] | None:
    repository = AnalysisRepository(base_dir)
    state = repository.get_crypto_live_paper_state()
    if state is not None:
        return state
    path = base_dir / "crypto_live_paper_state.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        repository.save_crypto_live_paper_state(payload)
        return payload
    return None


def _write_crypto_live_paper_state(base_dir: Path, state: Mapping[str, object]) -> None:
    materialized = dict(state)
    materialized["state_persistence"] = "sqlite"
    AnalysisRepository(base_dir).save_crypto_live_paper_state(materialized)
    try:
        (base_dir / "crypto_live_paper_state.json").write_text(json.dumps(materialized, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _sanitize_trade_history_copy(state: Mapping[str, object]) -> dict[str, Any]:
    cleaned = dict(state)
    cleaned_positions: list[dict[str, Any]] = []
    positions = cleaned.get("positions") if isinstance(cleaned.get("positions"), list) else []
    for position in positions:
        if isinstance(position, dict):
            cleaned_positions.append(_prepare_crypto_position(position))
    cleaned["positions"] = cleaned_positions
    cleaned["has_open_positions"] = bool(cleaned_positions)
    cleaned_history: list[dict[str, Any]] = []
    history = cleaned.get("trade_history") if isinstance(cleaned.get("trade_history"), list) else []
    for trade in history:
        if not isinstance(trade, dict):
            continue
        item = dict(trade)
        reason = str(item.get("reason") or "").strip()
        if reason in {"보유 포지션을 현재가로 평가", "Binance 실시간 가격으로 paper 계좌 평가손익 갱신"}:
            reason = ""
        item["display_event"] = _display_trade_event(item.get("event"))
        if item["display_event"] == "보유중":
            continue
        item["price_label"] = item.get("price_label") or _format_integer_display(item.get("price"))
        item["notional_label"] = item.get("notional_label") or _format_integer_display(item.get("notional"))
        cleaned_history.append(item)
    cleaned["trade_history"] = cleaned_history
    return cleaned


def _fetch_binance_prices(symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    url = "https://api.binance.com/api/v3/ticker/price?" + urllib.parse.urlencode({"symbols": json.dumps(symbols, separators=(",", ":"))})
    with urllib.request.urlopen(url, timeout=6) as response:
        payload = json.loads(response.read().decode("utf-8"))
    prices: dict[str, float] = {}
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("symbol"):
                try:
                    prices[str(item["symbol"])] = float(item.get("price"))
                except (TypeError, ValueError):
                    continue
    return prices


def _fetch_usdkrw_rate() -> float:
    """Fetch a public USD/KRW reference rate for KRW portfolio valuation."""
    url = "https://open.er-api.com/v6/latest/USD"
    with urllib.request.urlopen(url, timeout=6) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rates = payload.get("rates") if isinstance(payload, dict) else {}
    try:
        rate = float(rates.get("KRW"))
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0:
        raise ValueError("empty_usdkrw_rate")
    return rate


def _crypto_state_matches_fx_basis(state: Mapping[str, object] | None, quote_to_krw: float) -> bool:
    if not isinstance(state, Mapping):
        return False
    if int(_safe_float(state.get("state_version"), 0)) < 2:
        return False
    previous_rate = _safe_float(state.get("quote_to_krw"), 0)
    return previous_rate > 0 and abs(previous_rate - quote_to_krw) / quote_to_krw < 0.05


def _reset_crypto_portfolio_basis(snapshot: Mapping[str, object]) -> dict[str, object]:
    portfolio = snapshot.get("paper_portfolio") if isinstance(snapshot.get("paper_portfolio"), Mapping) else {}
    starting_cash = _safe_float(portfolio.get("starting_cash"), 1_000_000.0)
    return {"starting_cash": starting_cash, "cash": starting_cash, "positions": [], "trade_history": []}


def _fetch_binance_klines(symbol: str, *, interval: str = "1m", limit: int = 120) -> list[list[Any]]:
    url = "https://api.binance.com/api/v3/klines?" + urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    with urllib.request.urlopen(url, timeout=6) as response:
        payload = json.loads(response.read().decode("utf-8"))
    normalized: list[list[Any]] = []
    if not isinstance(payload, list):
        return normalized
    for item in payload:
        if not isinstance(item, list) or len(item) < 6:
            continue
        normalized.append([item[0], item[1], item[2], item[3], item[4], item[5]])
    return normalized


def _build_live_pnl_from_snapshot(base_dir: Path, snapshot: Mapping[str, object]) -> dict[str, Any]:
    rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
    symbols = [CRYPTO_BINANCE_SYMBOLS.get(str(row.get("ticker")), str(row.get("ticker", "")).replace("-USD", "USDT")) for row in rows if isinstance(row, Mapping) and row.get("ticker")]
    live_state = _load_crypto_live_paper_state(base_dir)
    try:
        quote_to_krw = _fetch_usdkrw_rate()
    except Exception:
        quote_to_krw = _safe_float(live_state.get("quote_to_krw") if isinstance(live_state, Mapping) else None, 1400.0)
    working_snapshot = dict(snapshot)
    if _crypto_state_matches_fx_basis(live_state, quote_to_krw):
        working_snapshot["paper_portfolio"] = live_state
    else:
        working_snapshot["paper_portfolio"] = _reset_crypto_portfolio_basis(snapshot)
    try:
        prices = _fetch_binance_prices(symbols)
        if not prices:
            raise ValueError("empty_binance_prices")
        state = _sanitize_trade_history_copy(run_crypto_live_paper_execution(working_snapshot, prices, commission_bps=10, quote_to_krw=quote_to_krw))
        state["state_persistence"] = "sqlite"
        _write_crypto_live_paper_state(base_dir, state)
        return state
    except Exception:
        fallback = live_state if _crypto_state_matches_fx_basis(live_state, quote_to_krw) else working_snapshot.get("paper_portfolio")
        if not isinstance(fallback, dict):
            fallback = _reset_crypto_portfolio_basis(snapshot)
        state = _sanitize_trade_history_copy(fallback)
        state.setdefault("execution_mode", "paper_trade_execution")
        state["price_source"] = "binance_public_ticker"
        state["paper_order_only"] = True
        state["live_capital_allowed"] = False
        return state


def _build_crypto_chart_from_snapshot(snapshot: Mapping[str, object], ticker: str) -> dict[str, Any]:
    symbol = CRYPTO_BINANCE_SYMBOLS.get(str(ticker), str(ticker).replace("-USD", "USDT"))
    try:
        klines = _fetch_binance_klines(symbol)
    except Exception:
        klines = []
    return build_live_crypto_chart_payload(ticker, klines, snapshot=snapshot)


def _format_weight_label(weights: object) -> str:
    if not isinstance(weights, dict):
        return "-"
    asset_names = {
        "SPY": "S&P500",
        "QQQ": "나스닥100",
        "QLD": "나스닥100 2배",
        "SOXL": "반도체 3배",
        "SCHD": "배당/퀄리티",
        "IEF": "미국중기채",
        "GLD": "금",
        "BIL": "현금성",
    }
    parts: list[str] = []
    for symbol, value in weights.items():
        try:
            weight = float(value)
        except (TypeError, ValueError):
            continue
        if abs(weight) <= 1e-9:
            continue
        parts.append(f"{asset_names.get(str(symbol), str(symbol))} {weight * 100:.0f}%")
    return " / ".join(parts) or "-"


def _prepare_portfolio_backtest_view(portfolio_backtest: object) -> dict[str, Any]:
    if not isinstance(portfolio_backtest, dict) or not portfolio_backtest.get("available"):
        return {"available": False, "reason": "portfolio_backtest_unavailable"}
    raw_results = portfolio_backtest.get("results") if isinstance(portfolio_backtest.get("results"), list) else []
    results: list[dict[str, Any]] = []
    for rank, result in enumerate(raw_results, 1):
        if not isinstance(result, dict):
            continue
        total_return = float(result.get("total_return_percent") or 0.0)
        max_drawdown = float(result.get("max_drawdown_percent") or 0.0)
        profit = float(result.get("profit") or 0.0)
        final_value = float(result.get("final_value") or 0.0)
        item = dict(result)
        item.update(
            {
                "rank": rank,
                "total_return_label": _format_signed_percent(total_return),
                "max_drawdown_label": _format_signed_percent(max_drawdown),
                "profit_label": _format_money(profit, "KRW"),
                "final_value_label": _format_money(final_value, "KRW"),
                "weights_label": _format_weight_label(result.get("last_weights")),
            }
        )
        results.append(item)
    if not results:
        return {"available": False, "reason": "portfolio_results_unavailable"}
    best = results[0]
    benchmark_id = str(portfolio_backtest.get("benchmark_strategy_id") or "simple_dca_70_spy_30_qqq")
    benchmark = next((item for item in results if str(item.get("strategy_id")) == benchmark_id), None)
    if benchmark is None:
        benchmark_name = str(portfolio_backtest.get("benchmark_name") or "단순 DCA")
        benchmark = next((item for item in results if benchmark_name in str(item.get("display_name") or "")), results[-1])
    excess_return = float(best.get("total_return_percent") or 0.0) - float(benchmark.get("total_return_percent") or 0.0)
    excess_profit = float(best.get("profit") or 0.0) - float(benchmark.get("profit") or 0.0)
    data_range = portfolio_backtest.get("data_range") if isinstance(portfolio_backtest.get("data_range"), list) else []
    asset_returns = []
    raw_asset_returns = portfolio_backtest.get("asset_returns") if isinstance(portfolio_backtest.get("asset_returns"), dict) else {}
    for symbol, value in sorted(raw_asset_returns.items(), key=lambda item: float(item[1] or 0.0), reverse=True):
        asset_returns.append({"symbol": symbol, "return_label": _format_signed_percent(float(value or 0.0))})
    raw_live_readiness = portfolio_backtest.get("live_readiness") if isinstance(portfolio_backtest.get("live_readiness"), dict) else {}
    live_readiness = dict(raw_live_readiness)
    if live_readiness:
        live_readiness["failure_reasons"] = raw_live_readiness.get("failure_reasons") if isinstance(raw_live_readiness.get("failure_reasons"), list) else []
        live_readiness["leveraged_weight_label"] = _format_signed_percent(float(raw_live_readiness.get("leveraged_weight_percent") or 0.0)).replace("+", "")
        worst_mdd = raw_live_readiness.get("worst_window_drawdown_percent")
        live_readiness["worst_window_drawdown_label"] = "-" if worst_mdd is None else _format_signed_percent(float(worst_mdd or 0.0))
    raw_windows = portfolio_backtest.get("evaluation_windows") if isinstance(portfolio_backtest.get("evaluation_windows"), list) else []
    evaluation_windows = []
    for window in raw_windows:
        if not isinstance(window, dict):
            continue
        result = window.get("result") if isinstance(window.get("result"), dict) else {}
        benchmark_window = window.get("benchmark") if isinstance(window.get("benchmark"), dict) else {}
        evaluation_windows.append(
            {
                "label": window.get("label"),
                "strategy_return_label": _format_signed_percent(float(result.get("total_return_percent") or 0.0)),
                "strategy_mdd_label": _format_signed_percent(float(result.get("max_drawdown_percent") or 0.0)),
                "benchmark_return_label": _format_signed_percent(float(benchmark_window.get("total_return_percent") or 0.0)),
            }
        )
    return {
        "available": True,
        "results": results,
        "best": best,
        "benchmark": benchmark,
        "benchmark_name": portfolio_backtest.get("benchmark_name") or benchmark.get("display_name") or "단순 DCA",
        "excess_return_percent": excess_return,
        "excess_return_label": _format_signed_percent(excess_return),
        "excess_profit": excess_profit,
        "excess_profit_label": _format_money(excess_profit, "KRW"),
        "start_date": data_range[0] if len(data_range) >= 1 else portfolio_backtest.get("start_date"),
        "end_date": data_range[1] if len(data_range) >= 2 else portfolio_backtest.get("end_date"),
        "assumptions": portfolio_backtest.get("assumptions") if isinstance(portfolio_backtest.get("assumptions"), list) else [],
        "asset_returns": asset_returns,
        "live_readiness": live_readiness,
        "evaluation_windows": evaluation_windows,
        "invested": best.get("invested"),
    }


def _prepare_return_summary(record: dict[str, Any], backtest: dict[str, Any], *, initial_capital: int = 100_000_000) -> dict[str, Any]:
    portfolio_backtest = _prepare_portfolio_backtest_view(record.get("portfolio_backtest"))
    if portfolio_backtest.get("available"):
        best = portfolio_backtest.get("best") or {}
        benchmark = portfolio_backtest.get("benchmark") or {}
        invested = float(best.get("invested") or portfolio_backtest.get("invested") or initial_capital)
        strategy_return = float(best.get("total_return_percent") or 0.0)
        benchmark_return = float(benchmark.get("total_return_percent") or 0.0)
        profit = float(best.get("profit") or 0.0)
        excess_return = float(portfolio_backtest.get("excess_return_percent") or strategy_return - benchmark_return)
        return {
            "available": True,
            "run_id": record.get("run_id"),
            "ticker": record.get("ticker"),
            "display_label": _display_label(record),
            "period_label": "전체",
            "start_date": portfolio_backtest.get("start_date"),
            "end_date": portfolio_backtest.get("end_date"),
            "currency": "KRW",
            "initial_capital": invested,
            "strategy_return_percent": strategy_return,
            "strategy_return_label": _format_signed_percent(strategy_return),
            "benchmark_return_percent": benchmark_return,
            "benchmark_return_label": _format_signed_percent(benchmark_return),
            "excess_return_percent": excess_return,
            "excess_return_label": _format_signed_percent(excess_return),
            "pnl": int(round(profit)),
            "pnl_label": _format_money(profit, "KRW"),
            "benchmark_pnl": int(round(float(benchmark.get("profit") or 0.0))),
            "trade_count": int(best.get("contribution_count") or 0),
            "win_rate_percent": 0.0,
            "sample_status_label": "월납입 포트폴리오",
        }
    if not isinstance(backtest, dict) or not backtest.get("available"):
        return {"available": False, "reason": (backtest or {}).get("reason") if isinstance(backtest, dict) else "backtest_unavailable"}
    periods = backtest.get("periods") if isinstance(backtest.get("periods"), dict) else {}
    period = periods.get("전체") or next(iter(periods.values()), None)
    if not isinstance(period, dict):
        return {"available": False, "reason": "period_unavailable"}
    strategy_return = float(period.get("strategy_return_percent") or 0.0)
    benchmark_return = float(period.get("benchmark_return_percent") or 0.0)
    excess_return = float(period.get("excess_return_percent") or strategy_return - benchmark_return)
    trade_count = int(period.get("trade_count") or 0)
    pnl = int(round(initial_capital * strategy_return / 100))
    benchmark_pnl = int(round(initial_capital * benchmark_return / 100))
    return {
        "available": True,
        "run_id": record.get("run_id"),
        "ticker": record.get("ticker"),
        "display_label": _display_label(record),
        "period_label": "전체",
        "start_date": period.get("start_date"),
        "end_date": period.get("end_date"),
        "currency": (backtest.get("levels") or {}).get("currency") or "KRW",
        "initial_capital": initial_capital,
        "strategy_return_percent": strategy_return,
        "strategy_return_label": _format_signed_percent(strategy_return),
        "benchmark_return_percent": benchmark_return,
        "benchmark_return_label": _format_signed_percent(benchmark_return),
        "excess_return_percent": excess_return,
        "excess_return_label": _format_signed_percent(excess_return),
        "pnl": pnl,
        "pnl_label": _format_money(pnl, "KRW"),
        "benchmark_pnl": benchmark_pnl,
        "trade_count": trade_count,
        "win_rate_percent": float(period.get("win_rate_percent") or 0.0),
        "sample_status_label": _sample_status_label(trade_count),
    }


_AGENT_SECTION_TITLES = [
    ("market_report", "시장 분석"),
    ("quant_strategy_report", "퀀트 전략 분석"),
    ("sentiment_report", "심리 분석"),
    ("news_report", "뉴스/공시"),
    ("fundamentals_report", "펀더멘털"),
    ("bull_history", "강세 논리"),
    ("bear_history", "약세 논리"),
    ("research_manager_decision", "리서치 매니저"),
    ("trader_investment_decision", "트레이더"),
    ("aggressive_history", "공격적 리스크"),
    ("conservative_history", "보수적 리스크"),
    ("neutral_history", "중립 리스크"),
    ("portfolio_manager_decision", "포트폴리오 매니저"),
    ("investment_plan", "투자 계획"),
    ("final_trade_decision", "최종 포트폴리오 결정"),
]


_AGENT_SECTION_SLUGS = {
    "market_report": "market",
    "quant_strategy_report": "quant-strategy",
    "sentiment_report": "sentiment",
    "news_report": "news",
    "fundamentals_report": "fundamentals",
    "bull_history": "bull",
    "bear_history": "bear",
    "research_manager_decision": "research-manager",
    "trader_investment_decision": "trader",
    "aggressive_history": "risk-aggressive",
    "conservative_history": "risk-conservative",
    "neutral_history": "risk-neutral",
    "portfolio_manager_decision": "portfolio-manager",
    "investment_plan": "investment-plan",
    "final_trade_decision": "final-decision",
}


def _render_markdown_text(text: object) -> Markup:
    """Render a small, safe Markdown subset used by stored agent reports."""
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")

    def render_inline(value: str) -> str:
        escaped = str(escape(value))
        escaped = re.sub(r"`([^`]+)`", lambda match: f"<code>{match.group(1)}</code>", escaped)
        escaped = re.sub(r"\*\*([^*]+)\*\*", lambda match: f"<strong>{match.group(1)}</strong>", escaped)
        escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", lambda match: f"<em>{match.group(1)}</em>", escaped)
        return escaped

    html: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        html.append(f"<p>{render_inline(' '.join(paragraph).strip())}</p>")
        paragraph.clear()

    def flush_list() -> None:
        if not list_items:
            return
        html.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
        list_items.clear()

    for raw_line in source.split("\n"):
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            flush_list()
            level = min(len(heading.group(1)), 4)
            html.append(f"<h{level}>{render_inline(heading.group(2).strip())}</h{level}>")
            continue
        item = re.match(r"^[-*]\s+(.+)$", line)
        if item:
            flush_paragraph()
            list_items.append(render_inline(item.group(1).strip()))
            continue
        ordered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if ordered:
            flush_paragraph()
            list_items.append(render_inline(ordered.group(1).strip()))
            continue
        flush_list()
        paragraph.append(line)

    flush_paragraph()
    flush_list()
    return Markup("\n".join(html))


def _prepare_agent_sections(record: dict) -> list[dict[str, str]]:
    reports = record.get("reports", {}) or {}
    sections: list[dict[str, str]] = []
    for key, title in _AGENT_SECTION_TITLES:
        detail = str(reports.get(key) or "").strip()
        if not detail:
            continue
        display_detail = str(_prepare_display_text(record, detail))
        sections.append(
            {
                "id": _AGENT_SECTION_SLUGS.get(key, re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")),
                "title": title,
                "summary": _visible_summary(display_detail, limit=180),
                "detail": display_detail,
            }
        )
    return sections


_SOURCE_LABELS = {key: title for key, title in _AGENT_SECTION_TITLES}


def _source_labels(refs: object) -> list[str]:
    if not isinstance(refs, list):
        return []
    return [_SOURCE_LABELS.get(str(ref), str(ref)) for ref in refs if str(ref).strip()]


def _visible_summary(text: str, *, limit: int = 180) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"FINAL\s+TRANSACTION\s+PROPOSAL\s*:\s*\*{0,2}[A-Za-z ]+\*{0,2}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\*{0,2}(Recommendation|Rationale|Strategic Actions|Action|Reasoning|Rating|Executive Summary|Investment Thesis|Time Horizon)\*{0,2}\s*:",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[#*_`>-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -·")
    return make_snippet(cleaned or text, limit=limit)


def _prepare_structured_report_view(structured_report: dict) -> dict:
    prepared = deepcopy(structured_report or {})
    cards = prepared.get("decision_cards")
    if isinstance(cards, list):
        for card in cards:
            if isinstance(card, dict) and card.get("description"):
                description = str(card.get("description", ""))
                if "<" not in description and ">" not in description:
                    card["description"] = _visible_summary(description, limit=180)
    for key in ("positive_factors", "risk_factors"):
        factors = prepared.get(key)
        if isinstance(factors, list):
            for factor in factors:
                if isinstance(factor, dict):
                    if factor.get("text"):
                        text = str(factor.get("text", ""))
                        if "<" not in text and ">" not in text:
                            factor["text"] = _visible_summary(text, limit=220)
                    factor["source_labels"] = _source_labels(factor.get("source_refs"))
    strategy = prepared.get("action_strategy")
    if isinstance(strategy, dict):
        strategy["source_labels"] = _source_labels(strategy.get("source_refs"))
    return prepared


def _bool_query(value: bool | str | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return value.lower() in {"1", "true", "yes", "on"}


def _require_local_management(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Management endpoints are local-only")


def _parse_tickers(value: object) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[\s,]+", value)
    elif isinstance(value, list):
        parts = [str(item) for item in value]
    else:
        parts = []
    tickers = [_resolve_ticker_alias(part.strip()) for part in parts if part and part.strip()]
    if len(tickers) > MAX_BATCH_TICKERS:
        raise HTTPException(status_code=400, detail=f"tickers cannot exceed {MAX_BATCH_TICKERS}")
    invalid = [ticker for ticker in tickers if not TICKER_PATTERN.fullmatch(ticker)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"invalid ticker: {invalid[0]}")
    return tickers


def _parse_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise HTTPException(status_code=400, detail="boolean fields must be true or false")


def _parse_trade_date(value: object) -> str:
    trade_date = str(value or "").strip()
    if not trade_date:
        raise HTTPException(status_code=400, detail="trade_date is required")
    try:
        date.fromisoformat(trade_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="trade_date must be YYYY-MM-DD") from exc
    return trade_date


def _run_batch_job(repository: AnalysisRepository, job_id: str, batch_runner: Callable[..., dict]) -> None:
    job = repository.get_batch_job(job_id)
    if job is None:
        return
    if not repository.mark_batch_job_running(job_id):
        return
    try:
        summary = batch_runner(
            job["tickers"],
            job["trade_date"],
            artifact_dir=repository.base_dir,
            use_hermes_codex_auth=job["use_hermes_codex_auth"],
            debug=job["debug"],
        )
        repository.mark_batch_job_completed(job_id, summary)
    except Exception as exc:  # pragma: no cover - exercised by tests with injected runner
        repository.mark_batch_job_failed(job_id, str(exc))


def _compact_datetime(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    return text.replace("T", " ")[:16]


def _points_with_moving_averages(chart: dict[str, Any]) -> list[dict[str, Any]]:
    points = [dict(point) for point in chart.get("points") or []]
    if not points:
        return []
    by_date = {str(point.get("date") or ""): point for point in points}
    moving_averages = chart.get("moving_averages") or {}
    if not isinstance(moving_averages, dict):
        return points
    for key, series in moving_averages.items():
        if not isinstance(series, list):
            continue
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        aliases = {normalized_key}
        if normalized_key.startswith("ma") and normalized_key[2:].isdigit():
            aliases.add(f"sma{normalized_key[2:]}")
        for row in series:
            if not isinstance(row, dict):
                continue
            point = by_date.get(str(row.get("time") or row.get("date") or ""))
            if point is None:
                continue
            value = row.get("value")
            for alias in aliases:
                point[alias] = value
    return points


_SIGNAL_ACTION_LABELS = {
    "BUY": "매수",
    "WAIT": "관망",
    "HOLD": "보유",
    "SELL_TAKE_PROFIT": "익절",
    "SELL_STOP_LOSS": "손절",
    "AVOID": "거래회피",
    "NO_DATA": "데이터 없음",
}

_SIGNAL_ACTION_TONES = {
    "BUY": "positive",
    "WAIT": "info",
    "HOLD": "info",
    "SELL_TAKE_PROFIT": "positive",
    "SELL_STOP_LOSS": "negative",
    "AVOID": "warning",
    "NO_DATA": "neutral",
}


_TRIGGER_TYPE_LABELS = {
    "price_below": "가격 하향 이탈",
    "price_above": "가격 상향 돌파",
    "volume_spike": "거래량 급증",
    "moving_average_cross": "이동평균 이탈",
    "time_expired": "유효기간 만료",
}

_EXECUTION_MODE_LABELS = {
    "programmatic_rule_engine": "규칙 기반 자동 실행",
}


def _prepare_trade_plan_view(record: dict[str, Any], chart: dict[str, Any]) -> dict[str, Any]:
    spec = parse_strategy_spec(record.get("strategy_spec"))
    if spec is None:
        return {"available": False, "reason": "strategy_spec_unavailable"}
    points = _points_with_moving_averages(chart) if isinstance(chart, dict) else []
    if not points:
        return {"available": False, "reason": "price_data_unavailable"}

    latest = points[-1]
    signal = evaluate_signal(spec, latest)
    reanalysis = evaluate_reanalysis_triggers(spec, points)
    action = str(signal.get("action") or "NO_DATA")
    latest_price = signal.get("price") or latest.get("close") or latest.get("price")
    distance_to_entry: float | None = None
    try:
        price = float(latest_price)
        if price > spec.entry.high:
            distance_to_entry = price - spec.entry.high
        elif price < spec.entry.low:
            distance_to_entry = spec.entry.low - price
        else:
            distance_to_entry = 0.0
    except (TypeError, ValueError):
        distance_to_entry = None

    triggers = []
    for trigger in reanalysis.get("triggers") or []:
        if not isinstance(trigger, dict):
            continue
        trigger_type = str(trigger.get("type") or "")
        triggers.append(
            {
                "reason": trigger.get("reason") or _TRIGGER_TYPE_LABELS.get(trigger_type, "재분석 조건 충족"),
                "type_label": _TRIGGER_TYPE_LABELS.get(trigger_type, "재분석 조건"),
            }
        )

    return {
        "available": True,
        "strategy_id": spec.strategy_id,
        "execution_mode": spec.execution_mode,
        "execution_mode_label": _EXECUTION_MODE_LABELS.get(spec.execution_mode, "전략 규칙 실행"),
        "strategy_label": "가격 구간 기반 롱 전략",
        "source": spec.source,
        "signal": signal,
        "action_label": _SIGNAL_ACTION_LABELS.get(action, action),
        "action_tone": _SIGNAL_ACTION_TONES.get(action, "neutral"),
        "reason": signal.get("reason") or "-",
        "signal_date": signal.get("date") or latest.get("date") or "-",
        "latest_price": latest_price,
        "distance_to_entry": distance_to_entry,
        "levels": {
            "entry_low": spec.entry.low,
            "entry_high": spec.entry.high,
            "take_profit": spec.take_profit.price,
            "stop_loss": spec.stop_loss.price,
            "currency": spec.currency,
        },
        "valid_until": spec.valid_until,
        "avoid_conditions": spec.avoid_conditions,
        "reanalysis": reanalysis,
        "reanalysis_required": bool(reanalysis.get("reanalysis_required")),
        "reanalysis_label": "재분석 필요" if reanalysis.get("reanalysis_required") else "현재 전략 유지 가능",
        "reanalysis_tone": "warning" if reanalysis.get("reanalysis_required") else "positive",
        "triggers": triggers,
    }


def _prepare_dashboard_runs(runs: list[dict]) -> list[dict]:
    prepared: list[dict] = []
    for run in runs:
        item = dict(run)
        item["updated_label"] = _compact_datetime(item.get("updated_at"))
        item["created_label"] = _compact_datetime(item.get("created_at"))
        item["display_name"] = _display_company_name(item)
        item["display_label"] = _display_label(item)
        item["display_label_with_code"] = _display_label_with_code(item)
        item["decision_summary"] = _prepare_display_text(item, item.get("decision_summary"))
        prepared.append(item)
    return prepared


def _attach_run_return_summaries(runs: list[dict], repository: AnalysisRepository) -> list[dict]:
    for item in runs:
        try:
            record = repository.get_run(str(item.get("run_id") or "")) or item
            portfolio_backtest = _prepare_portfolio_backtest_view(record.get("portfolio_backtest"))
            if portfolio_backtest.get("available"):
                item["return_summary"] = _prepare_return_summary(record, {"available": False, "reason": "portfolio_run"})
                continue
            chart = get_price_chart(record["ticker"], record["trade_date"])
            backtest = build_strategy_backtest(record, chart)
            item["return_summary"] = _prepare_return_summary(record, backtest)
        except Exception:
            item["return_summary"] = {"available": False, "reason": "return_summary_unavailable"}
    return runs


def _prepare_batch_jobs_for_dashboard(jobs: list[dict]) -> list[dict]:
    prepared: list[dict] = []
    for job in jobs:
        item = dict(job)
        tickers = item.get("tickers") or []
        item["display_tickers"] = [_display_ticker_name(ticker) for ticker in tickers]
        prepared.append(item)
    return prepared


def _dashboard_metrics(runs: list[dict], *, total: int, health: dict, batch_jobs: list[dict]) -> dict[str, object]:
    rating_counts: dict[str, int] = {}
    market_counts: dict[str, int] = {}
    verified_count = 0
    kr_count = 0
    active_jobs = 0
    failed_jobs = 0
    for run in runs:
        rating = str(run.get("rating") or "-")
        market = str(run.get("market") or "-")
        rating_counts[rating] = rating_counts.get(rating, 0) + 1
        market_counts[market] = market_counts.get(market, 0) + 1
        if market == "KR":
            kr_count += 1
        if run.get("structured_report_verified"):
            verified_count += 1
    for job in batch_jobs:
        status = str(job.get("status") or "")
        if status in {"queued", "running"}:
            active_jobs += 1
        if status == "failed":
            failed_jobs += 1
    visible_count = len(runs)
    verified_ratio = round((verified_count / visible_count) * 100) if visible_count else 0
    return {
        "total_runs": total,
        "visible_runs": visible_count,
        "kr_runs": kr_count,
        "verified_count": verified_count,
        "verified_ratio": verified_ratio,
        "rating_counts": rating_counts,
        "market_counts": market_counts,
        "active_jobs": active_jobs,
        "failed_jobs": failed_jobs,
        "db_rows": health.get("database_rows", 0),
        "payload_files": health.get("payload_files", 0),
        "latest_updated_label": _compact_datetime(runs[0].get("updated_at") if runs else None),
    }


def create_dashboard_app(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    *,
    batch_runner: Callable[..., dict] = run_batch_analysis,
    crypto_worker_enabled: bool | None = None,
    crypto_worker_interval_seconds: float | None = None,
) -> FastAPI:
    app = FastAPI(title="TradingAgents Hybrid Dashboard")
    repository = AnalysisRepository(data_dir)
    paper_ledger = PaperTradingLedger(data_dir)
    repository.recover_interrupted_batch_jobs()
    batch_executor = ThreadPoolExecutor(max_workers=max(1, BATCH_WORKERS), thread_name_prefix="dashboard-batch")
    app.state.batch_executor = batch_executor
    if crypto_worker_enabled is None:
        crypto_worker_enabled = os.getenv("TRADINGAGENTS_CRYPTO_WORKER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    if crypto_worker_interval_seconds is None:
        crypto_worker_interval_seconds = float(os.getenv("TRADINGAGENTS_CRYPTO_WORKER_INTERVAL_SECONDS", "30"))
    crypto_worker_interval_seconds = max(5.0, float(crypto_worker_interval_seconds))
    crypto_worker_stop = threading.Event()
    app.state.crypto_worker_enabled = bool(crypto_worker_enabled)
    app.state.crypto_worker_running = False
    app.state.crypto_worker_thread = None
    app.state.crypto_worker_interval_seconds = crypto_worker_interval_seconds
    app.state.crypto_worker_run_count = 0
    app.state.crypto_worker_last_success_at = None
    app.state.crypto_worker_last_error = None
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["money"] = _format_money
    templates.env.filters["markdown"] = _render_markdown_text
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _filters(
        *,
        ticker: Optional[str] = None,
        market: Optional[str] = None,
        rating: Optional[str] = None,
        action: Optional[str] = None,
        query: Optional[str] = None,
        trade_date_from: Optional[str] = None,
        trade_date_to: Optional[str] = None,
    ) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "ticker": _resolve_ticker_alias(ticker) if ticker else ticker,
                "market": market,
                "rating": rating,
                "action": action,
                "query": query,
                "trade_date_from": trade_date_from,
                "trade_date_to": trade_date_to,
            }.items()
            if value not in (None, "")
        }

    @app.on_event("shutdown")
    def shutdown_batch_executor() -> None:
        crypto_worker_stop.set()
        worker_thread = getattr(app.state, "crypto_worker_thread", None)
        if worker_thread is not None and worker_thread.is_alive():
            worker_thread.join(timeout=2)
        app.state.crypto_worker_running = False
        batch_executor.shutdown(wait=False, cancel_futures=True)

    def _run_crypto_worker_once() -> None:
        snapshot = _load_crypto_signal_snapshot(repository.base_dir)
        _build_live_pnl_from_snapshot(repository.base_dir, snapshot)
        app.state.crypto_worker_run_count += 1
        app.state.crypto_worker_last_success_at = datetime.now(timezone.utc).isoformat()
        app.state.crypto_worker_last_error = None

    def _crypto_worker_loop() -> None:
        while not crypto_worker_stop.is_set():
            try:
                _run_crypto_worker_once()
            except Exception as exc:  # pragma: no cover - defensive background guard
                app.state.crypto_worker_last_error = f"{type(exc).__name__}: {exc}"
            crypto_worker_stop.wait(crypto_worker_interval_seconds)

    @app.on_event("startup")
    def startup_crypto_worker() -> None:
        if not app.state.crypto_worker_enabled or app.state.crypto_worker_running:
            return
        crypto_worker_stop.clear()
        worker_thread = threading.Thread(target=_crypto_worker_loop, name="crypto-paper-worker", daemon=True)
        app.state.crypto_worker_thread = worker_thread
        app.state.crypto_worker_running = True
        worker_thread.start()

    dashboard_profiles = {
        "summary": {
            "key": "summary",
            "title": "종합 요약",
            "short_label": "요약",
            "eyebrow": "Overview",
            "description": "시장 흐름, 전략 업데이트, 주요 판단을 같은 형식으로 확인합니다.",
            "href": "/dashboards/summary",
            "icon": "⌁",
            "meta": "전체 상태",
            "accent": "violet",
        },
        "crypto": {
            "key": "crypto",
            "title": "암호화폐 전략",
            "short_label": "크립토",
            "eyebrow": "Crypto Strategy",
            "description": "BTC/ETH/SOL/BNB/XRP의 개별 전략 의견, 가격 레벨, 손익을 확인합니다.",
            "summary": "BTC/ETH/SOL 등 주요 암호화폐의 전략 레벨, 체결 이벤트, 손익과 수수료를 한 화면에서 추적합니다.",
            "href": "/dashboards/crypto",
            "icon": "₿",
            "meta": "1h 전략",
            "accent": "cyan",
        },
        "etf": {
            "key": "etf",
            "title": "ETF 전략",
            "short_label": "ETF",
            "eyebrow": "ETF Signals",
            "description": "ETF·레버리지 ETF의 시장점수, 가격표, 진입/손절/목표를 같은 카드 구조로 봅니다.",
            "href": "/dashboards/etf",
            "icon": "ETF",
            "meta": "가격 레벨",
            "accent": "green",
        },
        "kospi": {
            "key": "kospi",
            "title": "코스피 분석",
            "short_label": "코스피",
            "eyebrow": "KOSPI Research",
            "description": "한국 대형주 리포트의 오늘 판단, 전략 성과, 상세 근거를 확인합니다.",
            "href": "/dashboards/kospi",
            "icon": "KR",
            "meta": "한국 주식",
            "accent": "amber",
        },
        "nasdaq": {
            "key": "nasdaq",
            "title": "나스닥 분석",
            "short_label": "나스닥",
            "eyebrow": "NASDAQ Research",
            "description": "미국 성장주 리포트의 오늘 판단, 전략 성과, 상세 근거를 확인합니다.",
            "href": "/dashboards/nasdaq",
            "icon": "NDQ",
            "meta": "미국 성장주",
            "accent": "blue",
        },
    }

    self_feedback_profile = {
        "key": "self-feedback",
        "title": "10회 셀프 피드백",
        "short_label": "피드백",
        "eyebrow": "Strategy Self-Feedback",
        "description": "반복별 전략 수정, 백테스트 매수·매도 이력, 검증 피드백을 추적합니다.",
        "href": "/strategy-self-feedback",
        "icon": "↻",
        "meta": "전략 루프",
        "accent": "violet",
    }

    def _dashboard_nav() -> list[dict[str, Any]]:
        return [*dashboard_profiles.values(), self_feedback_profile]

    def _normalize_dashboard(dashboard: Optional[str]) -> str:
        key = (dashboard or "summary").strip().lower()
        return key if key in dashboard_profiles else "summary"

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboards/{dashboard}", response_class=HTMLResponse)
    def index(
        request: Request,
        dashboard: Optional[str] = None,
        ticker: Optional[str] = None,
        market: Optional[str] = None,
        rating: Optional[str] = None,
        action: Optional[str] = None,
        query: Optional[str] = None,
        trade_date_from: Optional[str] = None,
        trade_date_to: Optional[str] = None,
        latest_only: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        active_dashboard_key = _normalize_dashboard(dashboard)
        active_dashboard = dashboard_profiles[active_dashboard_key]
        latest = _bool_query(latest_only)
        if active_dashboard_key in {"kospi", "nasdaq"} and latest_only is None:
            latest = True
        if active_dashboard_key == "kospi" and market in (None, ""):
            market = "KR"
        if active_dashboard_key == "nasdaq" and market in (None, ""):
            market = "US"
        filters = _filters(
            ticker=ticker,
            market=market,
            rating=rating,
            action=action,
            query=query,
            trade_date_from=trade_date_from,
            trade_date_to=trade_date_to,
        )
        runs = repository.list_runs(**filters, latest_only=latest, limit=limit, offset=offset)
        total = repository.count_runs(**filters, latest_only=latest)
        health = repository.doctor()
        batch_jobs = _prepare_batch_jobs_for_dashboard(repository.list_batch_jobs(limit=10))
        runs = _attach_run_return_summaries(_prepare_dashboard_runs(runs), repository)
        metric_runs = _prepare_dashboard_runs(repository.list_runs(**filters, latest_only=latest, limit=None, offset=0))
        dashboard_metrics = _dashboard_metrics(metric_runs, total=total, health=health, batch_jobs=batch_jobs)
        return_leaderboard: list[dict[str, Any]] = []
        for row in metric_runs:
            record = repository.get_run(str(row.get("run_id") or ""))
            if record is None:
                continue
            portfolio_backtest = _prepare_portfolio_backtest_view(record.get("portfolio_backtest"))
            if portfolio_backtest.get("available"):
                summary = _prepare_return_summary(record, {"available": False, "reason": "portfolio_run"})
            else:
                chart = get_price_chart(record["ticker"], record["trade_date"])
                backtest = build_strategy_backtest(record, chart)
                summary = _prepare_return_summary(record, backtest)
            if summary.get("available"):
                return_leaderboard.append(summary)
        return_leaderboard.sort(key=lambda item: float(item.get("strategy_return_percent") or 0.0), reverse=True)
        return_leaderboard = return_leaderboard[:5]
        signal_snapshot = _load_signal_snapshot(repository.base_dir)
        crypto_signal_snapshot = _load_crypto_signal_snapshot(repository.base_dir)
        hourly_report = _load_hourly_report(repository.base_dir)
        cross_market_context = _load_cross_market_context(repository)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "runs": runs,
                "total": total,
                "limit": limit,
                "offset": offset,
                "filters": {
                    "ticker": ticker or "",
                    "market": market or "",
                    "rating": rating or "",
                    "action": action or "",
                    "query": query or "",
                    "trade_date_from": trade_date_from or "",
                    "trade_date_to": trade_date_to or "",
                    "latest_only": latest,
                },
                "data_dir": repository.base_dir,
                "latest_generated_at": repository.latest_generated_at(),
                "health": health,
                "batch_jobs": batch_jobs,
                "dashboard_metrics": dashboard_metrics,
                "return_leaderboard": return_leaderboard,
                "signal_snapshot": signal_snapshot,
                "crypto_signal_snapshot": crypto_signal_snapshot,
                "hourly_report": hourly_report,
                "cross_market_context": cross_market_context,
                "dashboard_nav": _dashboard_nav(),
                "active_dashboard": active_dashboard,
            },
        )

    @app.get("/strategy-self-feedback", response_class=HTMLResponse)
    def strategy_self_feedback_index(
        request: Request,
        ticker: Optional[str] = None,
        limit: int = Query(20, ge=1, le=100),
    ):
        loops = repository.list_strategy_self_feedback_loops(ticker=ticker, limit=limit)
        for loop in loops:
            loop["live_capital_allowed"] = False
        return templates.TemplateResponse(
            request,
            "self_feedback.html",
            {
                "mode": "list",
                "loops": loops,
                "loop_detail": None,
                "ticker": ticker or "",
                "limit": limit,
                "dashboard_nav": _dashboard_nav(),
                "active_dashboard": self_feedback_profile,
            },
        )

    @app.get("/strategy-self-feedback/{loop_id}", response_class=HTMLResponse)
    def strategy_self_feedback_detail(request: Request, loop_id: str):
        loop = repository.get_strategy_self_feedback_loop(loop_id)
        if loop is None:
            raise HTTPException(status_code=404, detail=f"Unknown strategy self-feedback loop: {loop_id}")
        loop["live_capital_allowed"] = False
        return templates.TemplateResponse(
            request,
            "self_feedback.html",
            {
                "mode": "detail",
                "loops": repository.list_strategy_self_feedback_loops(ticker=loop.get("ticker"), limit=20),
                "loop_detail": loop,
                "ticker": loop.get("ticker") or "",
                "limit": 20,
                "dashboard_nav": _dashboard_nav(),
                "active_dashboard": self_feedback_profile,
            },
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def detail(request: Request, run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        display_record = dict(record)
        display_record["decision_summary"] = _prepare_display_text(record, record.get("decision_summary"))
        display_record["investment_thesis"] = _prepare_display_text(record, record.get("investment_thesis"))
        sections = [
            ("market_report", "시장 분석"),
            ("sentiment_report", "심리 분석"),
            ("news_report", "뉴스/공시"),
            ("fundamentals_report", "펀더멘털"),
            ("investment_plan", "리서치 매니저"),
            ("trader_investment_decision", "트레이더 제안"),
            ("final_trade_decision", "최종 포트폴리오 결정"),
        ]
        agent_sections = _prepare_agent_sections(record)
        portfolio_backtest = _prepare_portfolio_backtest_view(record.get("portfolio_backtest"))
        if portfolio_backtest.get("available"):
            chart = {"available": False, "reason": "portfolio_run", "currency": "KRW"}
            trade_plan = {"available": False, "reason": "portfolio_run"}
            backtest = {"available": False, "reason": "portfolio_run"}
            execution_replay = {"available": False, "reason": "portfolio_run"}
        else:
            chart = get_price_chart(record["ticker"], record["trade_date"])
            trade_plan = _prepare_display_text(record, _prepare_trade_plan_view(record, chart))
            backtest = build_strategy_backtest(record, chart)
            execution_replay = _prepare_display_text(record, build_strategy_execution_replay(record, chart))
        return_summary = _prepare_return_summary(record, backtest)
        structured_report = _prepare_display_text(record, _prepare_structured_report_view(record.get("structured_report") or {}))
        scorecard = record.get("decision_scorecard") if isinstance(record.get("decision_scorecard"), dict) else build_decision_scorecard(record)
        scorecard = _prepare_display_text(record, deepcopy(scorecard))
        pre_live_validation = _prepare_display_text(record, build_pre_live_validation_report(backtest, scorecard=scorecard))
        structured_verification = record.get("structured_report_verification") or {}
        structured_verified = bool(record.get("structured_report_verified") and structured_verification.get("status") == "pass")
        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "record": display_record,
                "display_name": _display_company_name(record),
                "sections": sections,
                "agent_sections": agent_sections,
                "chart": chart,
                "trade_plan": trade_plan,
                "backtest": backtest,
                "portfolio_backtest": portfolio_backtest,
                "return_summary": return_summary,
                "execution_replay": execution_replay,
                "scorecard": scorecard,
                "pre_live_validation": pre_live_validation,
                "reanalysis_execution": record.get("reanalysis_execution") if isinstance(record.get("reanalysis_execution"), dict) else None,
                "structured_report": structured_report,
                "structured_verification": structured_verification,
                "structured_verified": structured_verified,
            },
        )

    @app.get("/runs/{run_id}/json")
    def run_json(run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        return JSONResponse(record)

    @app.get("/signals/price-table/{ticker}", response_class=HTMLResponse)
    def price_table_signal_detail(request: Request, ticker: str):
        snapshot = _load_signal_snapshot(repository.base_dir)
        if not snapshot.get("available"):
            raise HTTPException(status_code=404, detail="ETF/주식 가격표 스냅샷이 없습니다.")
        row = _find_snapshot_row(snapshot, ticker)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown signal ticker: {ticker}")
        detail = _prepare_etf_signal_detail(row, snapshot)
        return templates.TemplateResponse(request, "signal_detail.html", {"detail": detail})

    @app.get("/signals/crypto/{ticker}", response_class=HTMLResponse)
    def crypto_signal_detail(request: Request, ticker: str):
        snapshot = _load_crypto_signal_snapshot(repository.base_dir)
        if not snapshot.get("available"):
            raise HTTPException(status_code=404, detail="암호화폐 전략 스냅샷이 없습니다.")
        row = _find_snapshot_row(snapshot, ticker)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown crypto ticker: {ticker}")
        detail = _prepare_crypto_signal_detail(row)
        return templates.TemplateResponse(request, "signal_detail.html", {"detail": detail})

    @app.get("/management", response_class=HTMLResponse)
    def management(request: Request):
        _require_local_management(request)
        return templates.TemplateResponse(
            request,
            "management.html",
            {"health": repository.doctor(), "data_dir": repository.base_dir},
        )

    @app.get("/api/paper-trading/signals")
    def api_paper_trading_signals(
        ticker: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        signals = paper_ledger.list_signals(ticker=ticker, limit=limit, offset=offset)
        return {"total": paper_ledger.count_signals(ticker=ticker), "limit": limit, "offset": offset, "signals": signals}

    @app.post("/api/paper-trading/signals", status_code=201)
    def api_record_paper_trading_signal(payload: dict[str, Any] = Body(...)):
        try:
            return paper_ledger.record_signal(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/paper-trading/signals/{signal_id}/fill")
    def api_update_paper_trading_fill(signal_id: str, payload: dict[str, Any] = Body(...)):
        try:
            return paper_ledger.update_signal_with_ohlc(
                signal_id,
                payload.get("candles") or [],
                capital=payload.get("capital", 100_000_000),
                commission_bps=payload.get("commission_bps", 5.0),
                slippage_bps=payload.get("slippage_bps", 10.0),
            )
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if "unknown signal_id" in message else 400
            raise HTTPException(status_code=status_code, detail=message) from exc

    @app.get("/api/signals/price-table")
    def api_signal_price_table():
        snapshot = _load_signal_snapshot(repository.base_dir)
        if not snapshot.get("available"):
            raise HTTPException(status_code=404, detail=snapshot.get("reason") or "signal snapshot unavailable")
        return snapshot

    @app.get("/api/signals/crypto")
    def api_crypto_signals():
        snapshot = _load_crypto_signal_snapshot(repository.base_dir)
        if not snapshot.get("available"):
            raise HTTPException(status_code=404, detail=snapshot.get("reason") or "crypto signal snapshot unavailable")
        return snapshot

    @app.get("/api/signals/crypto/live-pnl")
    def api_crypto_live_pnl():
        snapshot = _load_crypto_signal_snapshot(repository.base_dir)
        if not snapshot:
            raise HTTPException(status_code=404, detail="crypto signal snapshot not found")
        return _build_live_pnl_from_snapshot(repository.base_dir, snapshot)

    @app.get("/api/signals/crypto/worker-status")
    def api_crypto_worker_status():
        worker_thread = getattr(app.state, "crypto_worker_thread", None)
        return {
            "enabled": bool(getattr(app.state, "crypto_worker_enabled", False)),
            "running": bool(getattr(app.state, "crypto_worker_running", False)) and bool(worker_thread is not None and worker_thread.is_alive()),
            "interval_seconds": getattr(app.state, "crypto_worker_interval_seconds", None),
            "run_count": int(getattr(app.state, "crypto_worker_run_count", 0) or 0),
            "last_success_at": getattr(app.state, "crypto_worker_last_success_at", None),
            "last_error": getattr(app.state, "crypto_worker_last_error", None),
            "price_source": "binance_public_ticker",
            "paper_order_only": True,
            "live_capital_allowed": False,
        }

    @app.get("/api/signals/crypto/chart/{ticker}")
    def api_crypto_live_chart(ticker: str):
        snapshot = _load_crypto_signal_snapshot(repository.base_dir)
        if not snapshot.get("available"):
            raise HTTPException(status_code=404, detail=snapshot.get("reason") or "crypto signal snapshot unavailable")
        known = {str(row.get("ticker")) for row in snapshot.get("rows", []) if isinstance(row, dict)}
        if ticker not in known:
            raise HTTPException(status_code=404, detail="unknown crypto ticker")
        return _build_crypto_chart_from_snapshot(snapshot, ticker)

    @app.get("/api/signals/hourly-report")
    def api_hourly_report():
        report = _load_hourly_report(repository.base_dir)
        if not report.get("available"):
            raise HTTPException(status_code=404, detail=report.get("reason") or "hourly report unavailable")
        return report

    @app.get("/api/signals/cross-market-context")
    def api_cross_market_context():
        context = _load_cross_market_context(repository)
        if not context.get("available"):
            raise HTTPException(status_code=404, detail=context.get("reason") or "cross market context unavailable")
        return context

    @app.get("/api/market-context")
    def api_market_context():
        context = _load_cross_market_context(repository)
        if not context.get("available"):
            raise HTTPException(status_code=404, detail=context.get("reason") or "cross market context unavailable")
        return context

    @app.get("/market-context", response_class=HTMLResponse)
    def market_context_detail(request: Request):
        context = _load_cross_market_context(repository)
        if not context.get("available"):
            raise HTTPException(status_code=404, detail=context.get("reason") or "cross market context unavailable")
        return templates.TemplateResponse(
            request,
            "cross_market_context.html",
            {
                "context": context,
                "dashboard_nav": _dashboard_nav(),
                "active_dashboard": dashboard_profiles.get("summary"),
            },
        )

    @app.get("/api/walk-forward-backtest")
    def api_walk_forward_backtest():
        raise HTTPException(
            status_code=403,
            detail="Walk-forward backtests are restricted to the internal agent workflow.",
        )

    @app.get("/api/runs")
    def api_runs(
        ticker: Optional[str] = None,
        market: Optional[str] = None,
        rating: Optional[str] = None,
        action: Optional[str] = None,
        query: Optional[str] = None,
        trade_date_from: Optional[str] = None,
        trade_date_to: Optional[str] = None,
        latest_only: bool = False,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        filters = _filters(
            ticker=ticker,
            market=market,
            rating=rating,
            action=action,
            query=query,
            trade_date_from=trade_date_from,
            trade_date_to=trade_date_to,
        )
        return {
            "total": repository.count_runs(**filters, latest_only=latest_only),
            "limit": limit,
            "offset": offset,
            "runs": repository.list_runs(**filters, latest_only=latest_only, limit=limit, offset=offset),
        }

    @app.get("/api/strategy-self-feedback")
    def api_strategy_self_feedback_loops(
        ticker: Optional[str] = None,
        limit: int = Query(20, ge=1, le=100),
    ):
        loops = repository.list_strategy_self_feedback_loops(ticker=ticker, limit=limit)
        return {"total": len(loops), "limit": limit, "ticker": ticker, "loops": loops}

    @app.get("/api/strategy-self-feedback/{loop_id}")
    def api_strategy_self_feedback_loop(loop_id: str):
        loop = repository.get_strategy_self_feedback_loop(loop_id)
        if loop is None:
            raise HTTPException(status_code=404, detail=f"Unknown strategy self-feedback loop: {loop_id}")
        loop["live_capital_allowed"] = False
        return loop

    @app.get("/api/runs/{run_id}")
    def api_run_detail(run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        return record

    @app.get("/api/runs/{run_id}/scorecard")
    def api_run_scorecard(run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        scorecard = record.get("decision_scorecard") if isinstance(record.get("decision_scorecard"), dict) else build_decision_scorecard(record)
        if not scorecard or not scorecard.get("available"):
            raise HTTPException(status_code=404, detail="decision scorecard unavailable")
        return scorecard

    @app.get("/api/runs/{run_id}/chart")
    def api_run_chart(run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        return get_price_chart(record["ticker"], record["trade_date"])

    @app.get("/api/runs/{run_id}/backtest")
    def api_run_backtest(run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        chart = get_price_chart(record["ticker"], record["trade_date"])
        return build_strategy_backtest(record, chart)

    @app.get("/api/runs/{run_id}/execution-replay")
    def api_run_execution_replay(run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        chart = get_price_chart(record["ticker"], record["trade_date"])
        return build_strategy_execution_replay(record, chart)

    @app.get("/api/runs/{run_id}/pre-live-validation")
    def api_run_pre_live_validation(run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        chart = get_price_chart(record["ticker"], record["trade_date"])
        backtest = build_strategy_backtest(record, chart)
        scorecard = record.get("decision_scorecard") if isinstance(record.get("decision_scorecard"), dict) else build_decision_scorecard(record)
        return build_pre_live_validation_report(backtest, scorecard=scorecard)

    @app.get("/api/pre-live-validation")
    def api_batch_pre_live_validation(
        ticker: Optional[str] = None,
        market: Optional[str] = None,
        rating: Optional[str] = None,
        action: Optional[str] = None,
        query: Optional[str] = None,
        trade_date_from: Optional[str] = None,
        trade_date_to: Optional[str] = None,
        latest_only: bool = True,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        filters = _filters(
            ticker=ticker,
            market=market,
            rating=rating,
            action=action,
            query=query,
            trade_date_from=trade_date_from,
            trade_date_to=trade_date_to,
        )
        rows = repository.list_runs(**filters, latest_only=latest_only, limit=limit, offset=offset)
        items: list[dict[str, Any]] = []
        for row in rows:
            record = repository.get_run(str(row.get("run_id") or ""))
            if record is None:
                continue
            chart = get_price_chart(record["ticker"], record["trade_date"])
            backtest = build_strategy_backtest(record, chart)
            scorecard = record.get("decision_scorecard") if isinstance(record.get("decision_scorecard"), dict) else build_decision_scorecard(record)
            items.append({"run_id": record.get("run_id"), "ticker": record.get("ticker"), "backtest": backtest, "scorecard": scorecard})
        result = build_batch_pre_live_validation_report(items)
        result["limit"] = limit
        result["offset"] = offset
        result["latest_only"] = latest_only
        result["filters"] = filters
        return result

    @app.get("/api/runs/{run_id}/signal")
    def api_run_signal(run_id: str, position_open: bool = False):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        spec = parse_strategy_spec(record.get("strategy_spec"))
        if spec is None:
            raise HTTPException(status_code=404, detail="strategy_spec unavailable")
        chart = get_price_chart(record["ticker"], record["trade_date"])
        points = _points_with_moving_averages(chart) if isinstance(chart, dict) else None
        if not points:
            raise HTTPException(status_code=404, detail="price data unavailable")
        signal = evaluate_signal(spec, points[-1], position_open=position_open)
        signal["execution_mode"] = spec.execution_mode
        signal["strategy_id"] = spec.strategy_id
        signal["source"] = spec.source
        return signal

    @app.get("/api/runs/{run_id}/reanalysis-check")
    def api_run_reanalysis_check(run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        spec = parse_strategy_spec(record.get("strategy_spec"))
        if spec is None:
            raise HTTPException(status_code=404, detail="strategy_spec unavailable")
        chart = get_price_chart(record["ticker"], record["trade_date"])
        points = _points_with_moving_averages(chart) if isinstance(chart, dict) else None
        if not points:
            raise HTTPException(status_code=404, detail="price data unavailable")
        result = evaluate_reanalysis_triggers(spec, points)
        result["execution_mode"] = spec.execution_mode
        result["strategy_id"] = spec.strategy_id
        result["source"] = spec.source
        return result

    @app.get("/api/tickers/{ticker}/latest")
    def api_latest_ticker(ticker: str):
        row = repository.get_latest_run_by_ticker(ticker)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
        return row

    @app.get("/api/management/doctor")
    def api_doctor(request: Request):
        _require_local_management(request)
        return repository.doctor()

    @app.get("/api/batch-jobs")
    def api_list_batch_jobs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        return {"jobs": _prepare_batch_jobs_for_dashboard(repository.list_batch_jobs(limit=limit, offset=offset)), "limit": limit, "offset": offset}

    @app.get("/api/batch-jobs/{job_id}")
    def api_get_batch_job(job_id: str):
        job = repository.get_batch_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown batch job: {job_id}")
        return job

    @app.post("/api/batch-jobs", status_code=202)
    def api_create_batch_job(payload: dict = Body(default_factory=dict)):
        tickers = _parse_tickers(payload.get("tickers"))
        trade_date = _parse_trade_date(payload.get("trade_date"))
        if not tickers:
            raise HTTPException(status_code=400, detail="tickers is required")
        try:
            job = repository.create_batch_job(
                tickers=tickers,
                trade_date=trade_date,
                use_hermes_codex_auth=_parse_bool(payload.get("use_hermes_codex_auth", False)),
                debug=_parse_bool(payload.get("debug", False)),
                max_active_jobs=MAX_ACTIVE_BATCH_JOBS,
            )
        except ValueError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        batch_executor.submit(_run_batch_job, repository, job["job_id"], batch_runner)
        return job

    @app.post("/api/management/reindex")
    def api_reindex(request: Request, payload: dict = Body(default_factory=dict)):
        _require_local_management(request)
        clear = bool(payload.get("clear", False))
        if clear and payload.get("confirm") != "REINDEX":
            raise HTTPException(status_code=400, detail="clear=true requires confirm='REINDEX'")
        return repository.reindex_from_files(clear=clear)

    return app


app = create_dashboard_app()
