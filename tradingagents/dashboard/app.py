from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .backtest import build_strategy_backtest, build_strategy_execution_replay
from tradingagents.strategies import evaluate_reanalysis_triggers, evaluate_signal
from tradingagents.strategies.schema import parse_strategy_spec
from .batch import DEFAULT_ARTIFACT_DIR, run_batch_analysis
from .charts import get_price_chart
from .extract import make_snippet
from .scorecard import build_decision_scorecard
from .storage import AnalysisRepository
from tradingagents.validation.paper_trading import PaperTradingLedger
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
    "NVDA": "NVIDIA",
}


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
    return _KNOWN_COMPANY_NAMES.get(ticker, _KNOWN_COMPANY_NAMES.get(ticker_code, ticker))


def _display_label(record: dict) -> str:
    ticker = str(record.get("ticker") or "").strip()
    company_name = _display_company_name(record).strip()
    if company_name and company_name.upper() != ticker.upper():
        return f"{company_name} ({ticker})"
    return ticker


def _is_krw_currency(currency: object) -> bool:
    return str(currency or "").strip().upper() in {"KRW", "₩", "원"}


def _format_money(value: object, currency: object = None) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    if _is_krw_currency(currency):
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


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


def _prepare_agent_sections(record: dict) -> list[dict[str, str]]:
    reports = record.get("reports", {}) or {}
    sections: list[dict[str, str]] = []
    for key, title in _AGENT_SECTION_TITLES:
        detail = str(reports.get(key) or "").strip()
        if not detail:
            continue
        sections.append(
            {
                "id": _AGENT_SECTION_SLUGS.get(key, re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")),
                "title": title,
                "summary": _visible_summary(detail, limit=180),
                "detail": detail,
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
    tickers = [part.strip() for part in parts if part and part.strip()]
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
) -> FastAPI:
    app = FastAPI(title="TradingAgents Hybrid Dashboard")
    repository = AnalysisRepository(data_dir)
    paper_ledger = PaperTradingLedger(data_dir)
    repository.recover_interrupted_batch_jobs()
    batch_executor = ThreadPoolExecutor(max_workers=max(1, BATCH_WORKERS), thread_name_prefix="dashboard-batch")
    app.state.batch_executor = batch_executor
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["money"] = _format_money
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
                "ticker": ticker,
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
        batch_executor.shutdown(wait=False, cancel_futures=True)

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
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
        latest = _bool_query(latest_only)
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
        batch_jobs = repository.list_batch_jobs(limit=10)
        runs = _prepare_dashboard_runs(runs)
        metric_runs = _prepare_dashboard_runs(repository.list_runs(**filters, latest_only=latest, limit=None, offset=0))
        dashboard_metrics = _dashboard_metrics(metric_runs, total=total, health=health, batch_jobs=batch_jobs)
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
            },
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def detail(request: Request, run_id: str):
        record = repository.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
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
        chart = get_price_chart(record["ticker"], record["trade_date"])
        trade_plan = _prepare_trade_plan_view(record, chart)
        backtest = build_strategy_backtest(record, chart)
        execution_replay = build_strategy_execution_replay(record, chart)
        structured_report = _prepare_structured_report_view(record.get("structured_report") or {})
        scorecard = record.get("decision_scorecard") if isinstance(record.get("decision_scorecard"), dict) else build_decision_scorecard(record)
        pre_live_validation = build_pre_live_validation_report(backtest, scorecard=scorecard)
        structured_verification = record.get("structured_report_verification") or {}
        structured_verified = bool(record.get("structured_report_verified") and structured_verification.get("status") == "pass")
        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "record": record,
                "display_name": _display_company_name(record),
                "sections": sections,
                "agent_sections": agent_sections,
                "chart": chart,
                "trade_plan": trade_plan,
                "backtest": backtest,
                "execution_replay": execution_replay,
                "scorecard": scorecard,
                "pre_live_validation": pre_live_validation,
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
        return {"jobs": repository.list_batch_jobs(limit=limit, offset=offset), "limit": limit, "offset": offset}

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
