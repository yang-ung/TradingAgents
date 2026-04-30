from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .batch import DEFAULT_ARTIFACT_DIR
from .storage import AnalysisRepository

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DATA_DIR = Path(os.getenv("TRADINGAGENTS_DASHBOARD_DIR", str(DEFAULT_ARTIFACT_DIR))).resolve()


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


def create_dashboard_app(data_dir: str | Path = DEFAULT_DATA_DIR) -> FastAPI:
    app = FastAPI(title="TradingAgents Hybrid Dashboard")
    repository = AnalysisRepository(data_dir)
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

    @app.post("/api/management/reindex")
    def api_reindex(request: Request, payload: dict = Body(default_factory=dict)):
        _require_local_management(request)
        clear = bool(payload.get("clear", False))
        if clear and payload.get("confirm") != "REINDEX":
            raise HTTPException(status_code=400, detail="clear=true requires confirm='REINDEX'")
        return repository.reindex_from_files(clear=clear)

    return app


app = create_dashboard_app()
