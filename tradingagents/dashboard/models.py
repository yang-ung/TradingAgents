from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AnalysisRecord(TypedDict):
    run_id: str
    ticker: str
    trade_date: str
    generated_at: str
    rating: str
    trader_action: str
    research_recommendation: str
    decision_summary: str
    investment_thesis: str
    price_target: Optional[float]
    time_horizon: str
    snippets: Dict[str, str]
    reports: Dict[str, str]
    report_lengths: Dict[str, int]
    raw_log_path: str
    structured_path: str
    metadata: Dict[str, Any]
    raw_state: Dict[str, Any]
    structured_report: Dict[str, Any]
    structured_report_verification: Dict[str, Any]
    structured_report_verified: bool
    structured_report_attempts: int


class BatchSummary(TypedDict):
    artifact_dir: str
    trade_date: str
    tickers: List[str]
    completed: int
    failed: List[Dict[str, str]]
    run_ids: List[str]
