from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .batch import DEFAULT_ARTIFACT_DIR, run_batch_analysis
from .storage import AnalysisRepository

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DATA_DIR = Path(os.getenv("TRADINGAGENTS_DASHBOARD_DIR", str(DEFAULT_ARTIFACT_DIR))).resolve()
MAX_BATCH_TICKERS = int(os.getenv("TRADINGAGENTS_DASHBOARD_MAX_BATCH_TICKERS", "20"))
MAX_ACTIVE_BATCH_JOBS = int(os.getenv("TRADINGAGENTS_DASHBOARD_MAX_ACTIVE_BATCH_JOBS", "3"))
BATCH_WORKERS = int(os.getenv("TRADINGAGENTS_DASHBOARD_BATCH_WORKERS", "1"))
TICKER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,20}$")


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


def create_dashboard_app(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    *,
    batch_runner: Callable[..., dict] = run_batch_analysis,
) -> FastAPI:
    app = FastAPI(title="TradingAgents Hybrid Dashboard")
    repository = AnalysisRepository(data_dir)
    repository.recover_interrupted_batch_jobs()
    batch_executor = ThreadPoolExecutor(max_workers=max(1, BATCH_WORKERS), thread_name_prefix="dashboard-batch")
    app.state.batch_executor = batch_executor
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
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
            ("final_trade_decision", "최종 결정"),
        ]
        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "record": record,
                "sections": sections,
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
