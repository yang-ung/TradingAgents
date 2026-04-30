from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .extract import dump_record, load_record
from .models import AnalysisRecord

SCHEMA_VERSION = 2


class AnalysisRepository:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.runs_dir = self.base_dir / "runs"
        self.db_path = self.base_dir / "dashboard.db"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    run_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    rating TEXT NOT NULL,
                    trader_action TEXT NOT NULL,
                    research_recommendation TEXT NOT NULL,
                    decision_summary TEXT NOT NULL,
                    structured_path TEXT NOT NULL,
                    raw_log_path TEXT NOT NULL,
                    payload_path TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            existing = {
                row["name"] for row in connection.execute("PRAGMA table_info(analyses)").fetchall()
            }
            additions = {
                "market": "TEXT NOT NULL DEFAULT 'US'",
                "exchange": "TEXT NOT NULL DEFAULT ''",
                "quick_think_llm": "TEXT NOT NULL DEFAULT ''",
                "deep_think_llm": "TEXT NOT NULL DEFAULT ''",
                "elapsed_seconds": "REAL",
                "has_korean_news": "INTEGER NOT NULL DEFAULT 0",
                "has_dart_disclosures": "INTEGER NOT NULL DEFAULT 0",
                "report_version": "TEXT NOT NULL DEFAULT '1'",
                "archived": "INTEGER NOT NULL DEFAULT 0",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in additions.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE analyses ADD COLUMN {column} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_analyses_ticker_trade_date ON analyses(ticker, trade_date DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_analyses_market_generated_at ON analyses(market, generated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_analyses_rating_generated_at ON analyses(rating, generated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_analyses_archived_generated_at ON analyses(archived, generated_at DESC)"
            )
            self._backfill_metadata_columns(connection)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

    def _backfill_metadata_columns(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT run_id, payload_json, market, exchange, quick_think_llm,
                   deep_think_llm, has_korean_news, has_dart_disclosures,
                   report_version, elapsed_seconds
            FROM analyses
            """
        ).fetchall()
        for row in rows:
            try:
                record = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                continue
            meta = self._metadata_row(record)
            should_update = (
                row["market"] in (None, "", "US")
                or row["exchange"] in (None, "")
                or row["quick_think_llm"] in (None, "")
                or row["deep_think_llm"] in (None, "")
                or row["report_version"] in (None, "")
                or row["elapsed_seconds"] is None
                or not bool(row["has_korean_news"])
                or not bool(row["has_dart_disclosures"])
            )
            if should_update:
                connection.execute(
                    """
                    UPDATE analyses
                    SET market = ?, exchange = ?, quick_think_llm = ?,
                        deep_think_llm = ?, elapsed_seconds = ?,
                        has_korean_news = ?, has_dart_disclosures = ?,
                        report_version = ?
                    WHERE run_id = ?
                    """,
                    (
                        meta["market"],
                        meta["exchange"],
                        meta["quick_think_llm"],
                        meta["deep_think_llm"],
                        meta["elapsed_seconds"],
                        meta["has_korean_news"],
                        meta["has_dart_disclosures"],
                        meta["report_version"],
                        row["run_id"],
                    ),
                )

    def payload_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    @staticmethod
    def _derive_market(ticker: str) -> tuple[str, str]:
        symbol = (ticker or "").upper()
        if symbol.endswith(".KS"):
            return "KR", "KOSPI"
        if symbol.endswith(".KQ"):
            return "KR", "KOSDAQ"
        if len(symbol) == 6 and symbol.isdigit():
            return "KR", "KRX"
        return "US", ""

    @staticmethod
    def _source_flags(record: AnalysisRecord) -> tuple[bool, bool]:
        news_report = (record.get("reports", {}) or {}).get("news_report", "") or ""
        return "Korean Local Market News" in news_report, "Korean Electronic Disclosures" in news_report

    @classmethod
    def _metadata_row(cls, record: AnalysisRecord) -> Dict[str, Any]:
        metadata = record.get("metadata", {}) or {}
        market, exchange = cls._derive_market(record.get("ticker", ""))
        has_korean_news, has_dart_disclosures = cls._source_flags(record)
        return {
            "market": metadata.get("market") or market,
            "exchange": metadata.get("exchange") or exchange,
            "quick_think_llm": metadata.get("quick_think_llm", ""),
            "deep_think_llm": metadata.get("deep_think_llm", ""),
            "elapsed_seconds": metadata.get("elapsed_seconds"),
            "has_korean_news": int(bool(metadata.get("has_korean_news", has_korean_news))),
            "has_dart_disclosures": int(bool(metadata.get("has_dart_disclosures", has_dart_disclosures))),
            "report_version": str(metadata.get("report_version", "1")),
        }

    def save(self, record: AnalysisRecord) -> AnalysisRecord:
        payload_path = self.payload_path(record["run_id"])
        materialized = dict(record)
        materialized["structured_path"] = str(payload_path)
        payload_path.write_text(dump_record(materialized), encoding="utf-8")
        meta = self._metadata_row(materialized)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analyses (
                    run_id, ticker, trade_date, generated_at, rating, trader_action,
                    research_recommendation, decision_summary, structured_path,
                    raw_log_path, payload_path, payload_json, market, exchange,
                    quick_think_llm, deep_think_llm, elapsed_seconds,
                    has_korean_news, has_dart_disclosures, report_version, archived,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT archived FROM analyses WHERE run_id = ?), 0), ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    ticker=excluded.ticker,
                    trade_date=excluded.trade_date,
                    generated_at=excluded.generated_at,
                    rating=excluded.rating,
                    trader_action=excluded.trader_action,
                    research_recommendation=excluded.research_recommendation,
                    decision_summary=excluded.decision_summary,
                    structured_path=excluded.structured_path,
                    raw_log_path=excluded.raw_log_path,
                    payload_path=excluded.payload_path,
                    payload_json=excluded.payload_json,
                    market=excluded.market,
                    exchange=excluded.exchange,
                    quick_think_llm=excluded.quick_think_llm,
                    deep_think_llm=excluded.deep_think_llm,
                    elapsed_seconds=excluded.elapsed_seconds,
                    has_korean_news=excluded.has_korean_news,
                    has_dart_disclosures=excluded.has_dart_disclosures,
                    report_version=excluded.report_version,
                    updated_at=excluded.updated_at
                """,
                (
                    materialized["run_id"],
                    materialized["ticker"],
                    materialized["trade_date"],
                    materialized["generated_at"],
                    materialized["rating"],
                    materialized["trader_action"],
                    materialized["research_recommendation"],
                    materialized["decision_summary"],
                    materialized["structured_path"],
                    materialized["raw_log_path"],
                    str(payload_path),
                    json.dumps(materialized, ensure_ascii=False),
                    meta["market"],
                    meta["exchange"],
                    meta["quick_think_llm"],
                    meta["deep_think_llm"],
                    meta["elapsed_seconds"],
                    meta["has_korean_news"],
                    meta["has_dart_disclosures"],
                    meta["report_version"],
                    materialized["run_id"],
                    materialized["generated_at"],
                ),
            )
            connection.commit()
        return materialized

    def _filter_sql(
        self,
        *,
        ticker: str | None = None,
        market: str | None = None,
        rating: str | None = None,
        action: str | None = None,
        query: str | None = None,
        trade_date_from: str | None = None,
        trade_date_to: str | None = None,
        include_archived: bool = False,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_archived:
            clauses.append("archived = 0")
        if ticker:
            clauses.append("UPPER(ticker) = UPPER(?)")
            params.append(ticker)
        if market:
            clauses.append("UPPER(market) = UPPER(?)")
            params.append(market)
        if rating:
            clauses.append("UPPER(rating) = UPPER(?)")
            params.append(rating)
        if action:
            clauses.append("UPPER(trader_action) = UPPER(?)")
            params.append(action)
        if trade_date_from:
            clauses.append("trade_date >= ?")
            params.append(trade_date_from)
        if trade_date_to:
            clauses.append("trade_date <= ?")
            params.append(trade_date_to)
        if query:
            like = f"%{query}%"
            clauses.append("(ticker LIKE ? OR run_id LIKE ? OR decision_summary LIKE ? OR payload_json LIKE ?)")
            params.extend([like, like, like, like])
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    @staticmethod
    def _overview_columns() -> str:
        return """
            run_id, ticker, trade_date, generated_at, rating,
            trader_action, research_recommendation, decision_summary,
            structured_path, raw_log_path, payload_path, market, exchange,
            quick_think_llm, deep_think_llm, elapsed_seconds,
            has_korean_news, has_dart_disclosures, report_version, archived,
            updated_at
        """

    def list_runs(
        self,
        *,
        ticker: str | None = None,
        market: str | None = None,
        rating: str | None = None,
        action: str | None = None,
        query: str | None = None,
        trade_date_from: str | None = None,
        trade_date_to: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        latest_only: bool = False,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        where_sql, params = self._filter_sql(
            ticker=ticker,
            market=market,
            rating=rating,
            action=action,
            query=query,
            trade_date_from=trade_date_from,
            trade_date_to=trade_date_to,
            include_archived=include_archived,
        )
        base = f"SELECT {self._overview_columns()} FROM analyses{where_sql}"
        if latest_only:
            base = f"""
                SELECT {self._overview_columns()}
                FROM (
                    SELECT filtered.*, ROW_NUMBER() OVER (
                        PARTITION BY filtered.ticker
                        ORDER BY filtered.generated_at DESC, filtered.run_id ASC
                    ) AS latest_rank
                    FROM ({base}) AS filtered
                ) AS ranked
                WHERE latest_rank = 1
            """
        sql = base + " ORDER BY generated_at DESC, ticker ASC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([max(0, limit), max(0, offset)])
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._coerce_row(row) for row in rows]

    def count_runs(self, **filters: Any) -> int:
        filters.pop("limit", None)
        filters.pop("offset", None)
        latest_only = bool(filters.pop("latest_only", False))
        if latest_only:
            return len(self.list_runs(latest_only=True, **filters))
        where_sql, params = self._filter_sql(**filters)
        with self._connect() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS count FROM analyses{where_sql}", params).fetchone()
        return int(row["count"])

    @staticmethod
    def _coerce_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        for key in ("has_korean_news", "has_dart_disclosures", "archived"):
            if key in data:
                data[key] = bool(data[key])
        return data

    def get_run(self, run_id: str, *, include_archived: bool = False) -> Optional[AnalysisRecord]:
        sql = "SELECT payload_path, payload_json FROM analyses WHERE run_id = ?"
        params: list[Any] = [run_id]
        if not include_archived:
            sql += " AND archived = 0"
        with self._connect() as connection:
            row = connection.execute(sql, params).fetchone()
        if row is None:
            return None
        payload_path = Path(row["payload_path"])
        if payload_path.exists():
            return load_record(payload_path)
        return json.loads(row["payload_json"])

    def get_latest_run_by_ticker(self, ticker: str) -> Optional[Dict[str, Any]]:
        rows = self.list_runs(ticker=ticker, limit=1)
        return rows[0] if rows else None

    def latest_generated_at(self) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(generated_at) AS generated_at FROM analyses WHERE archived = 0"
            ).fetchone()
        return row["generated_at"] if row and row["generated_at"] else None

    def archive_run(self, run_id: str, *, archived: bool = True) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE analyses SET archived = ? WHERE run_id = ?",
                (int(archived), run_id),
            )
            connection.commit()
        return cursor.rowcount > 0

    def delete_run(self, run_id: str, *, delete_payload: bool = False) -> bool:
        payload: Optional[Path] = None
        with self._connect() as connection:
            row = connection.execute("SELECT payload_path FROM analyses WHERE run_id = ?", (run_id,)).fetchone()
            if row is not None:
                payload = Path(row["payload_path"])
            cursor = connection.execute("DELETE FROM analyses WHERE run_id = ?", (run_id,))
            connection.commit()
        if delete_payload and payload and payload.exists():
            payload.unlink()
        return cursor.rowcount > 0

    def reindex_from_files(self, *, clear: bool = False) -> Dict[str, Any]:
        archived_by_run: dict[str, bool] = {}
        if clear:
            with self._connect() as connection:
                archived_by_run = {
                    row["run_id"]: bool(row["archived"])
                    for row in connection.execute("SELECT run_id, archived FROM analyses").fetchall()
                }
                connection.execute("DELETE FROM analyses")
                connection.commit()
        indexed = 0
        failed: list[dict[str, str]] = []
        for path in sorted(self.runs_dir.glob("*.json")):
            try:
                record = load_record(path)
                self.save(record)
                if archived_by_run.get(record["run_id"]):
                    self.archive_run(record["run_id"], archived=True)
                indexed += 1
            except Exception as exc:  # pragma: no cover - defensive maintenance path
                failed.append({"path": str(path), "error": str(exc)})
        return {"indexed": indexed, "failed": failed}

    def doctor(self) -> Dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT run_id, payload_path FROM analyses").fetchall()
            db_rows = len(rows)
        payload_files = sorted(self.runs_dir.glob("*.json"))
        row_paths = {Path(row["payload_path"]) for row in rows}
        row_ids = {row["run_id"] for row in rows}
        missing_payloads = [row["run_id"] for row in rows if not Path(row["payload_path"]).exists()]
        orphan_payloads = []
        for path in payload_files:
            if path not in row_paths and path.stem not in row_ids:
                orphan_payloads.append(str(path))
        return {
            "schema_version": SCHEMA_VERSION,
            "database_path": str(self.db_path),
            "runs_dir": str(self.runs_dir),
            "database_rows": db_rows,
            "payload_files": len(payload_files),
            "missing_payloads": missing_payloads,
            "orphan_payloads": orphan_payloads,
            "latest_generated_at": self.latest_generated_at(),
        }
