from __future__ import annotations

import json
import sqlite3
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.storage import AnalysisRepository


def _record(base, *, ticker, trade_date, generated_at, rating="Hold", action="Hold", summary=None, news=""):
    record = deepcopy(base)
    record["ticker"] = ticker
    record["trade_date"] = trade_date
    record["generated_at"] = generated_at
    record["rating"] = rating
    record["trader_action"] = action
    record["research_recommendation"] = rating
    record["decision_summary"] = summary or f"{ticker} {rating} summary"
    record["run_id"] = f"{ticker.lower()}-{trade_date}-{generated_at[-8:].replace(':', '')}"
    record["reports"]["news_report"] = news
    record["metadata"] = {
        "quick_think_llm": "gpt-5.4-mini",
        "deep_think_llm": "gpt-5.5",
        "elapsed_seconds": 12.5,
    }
    return record


@pytest.mark.unit
def test_repository_schema_tracks_version_indexes_and_query_metadata(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    samsung = _record(
        sample_record,
        ticker="005930.KS",
        trade_date="2026-04-30",
        generated_at="2026-04-30T01:00:00+00:00",
        rating="Buy",
        action="Buy",
        summary="삼성전자 매수 관점",
        news="## Korean Local Market News\n뉴스\n## Korean Electronic Disclosures\n공시",
    )
    nvda_old = _record(
        sample_record,
        ticker="NVDA",
        trade_date="2026-04-29",
        generated_at="2026-04-29T01:00:00+00:00",
        rating="Hold",
        action="Hold",
        summary="NVDA older hold",
    )
    nvda_new = _record(
        sample_record,
        ticker="NVDA",
        trade_date="2026-04-30",
        generated_at="2026-04-30T02:00:00+00:00",
        rating="Sell",
        action="Reduce",
        summary="NVDA latest reduce",
    )
    for record in [samsung, nvda_old, nvda_new]:
        repository.save(record)

    with sqlite3.connect(repository.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] >= 2
        indexes = {row[1] for row in connection.execute("PRAGMA index_list('analyses')")}
    assert "idx_analyses_ticker_trade_date" in indexes
    assert "idx_analyses_market_generated_at" in indexes

    kr_rows = repository.list_runs(market="KR")
    assert [row["ticker"] for row in kr_rows] == ["005930.KS"]
    assert kr_rows[0]["exchange"] == "KOSPI"
    assert kr_rows[0]["has_korean_news"] is True
    assert kr_rows[0]["has_dart_disclosures"] is True
    assert kr_rows[0]["quick_think_llm"] == "gpt-5.4-mini"
    assert kr_rows[0]["deep_think_llm"] == "gpt-5.5"

    assert [row["run_id"] for row in repository.list_runs(ticker="nvda", latest_only=True)] == [nvda_new["run_id"]]
    assert repository.list_runs(rating="Hold", latest_only=True)[0]["run_id"] == nvda_old["run_id"]
    assert repository.list_runs(trade_date_to="2026-04-29", latest_only=True)[0]["run_id"] == nvda_old["run_id"]
    assert repository.count_runs(rating="Sell") == 1
    assert repository.list_runs(query="삼성전자")[0]["run_id"] == samsung["run_id"]
    assert repository.get_latest_run_by_ticker("NVDA")["run_id"] == nvda_new["run_id"]


@pytest.mark.unit
def test_repository_migrates_existing_rows_from_payload_json(tmp_path, sample_record):
    tmp_path.mkdir(parents=True, exist_ok=True)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    legacy_record = _record(
        sample_record,
        ticker="005930.KS",
        trade_date="2026-04-30",
        generated_at="2026-04-30T01:00:00+00:00",
        news="## Korean Local Market News\n뉴스\n## Korean Electronic Disclosures\n공시",
    )
    payload_path = runs_dir / f"{legacy_record['run_id']}.json"
    payload_path.write_text(json.dumps(legacy_record, ensure_ascii=False), encoding="utf-8")
    with sqlite3.connect(tmp_path / "dashboard.db") as connection:
        connection.execute(
            """
            CREATE TABLE analyses (
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
        connection.execute(
            "INSERT INTO analyses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                legacy_record["run_id"],
                legacy_record["ticker"],
                legacy_record["trade_date"],
                legacy_record["generated_at"],
                legacy_record["rating"],
                legacy_record["trader_action"],
                legacy_record["research_recommendation"],
                legacy_record["decision_summary"],
                str(payload_path),
                legacy_record["raw_log_path"],
                str(payload_path),
                json.dumps(legacy_record, ensure_ascii=False),
            ),
        )
        connection.commit()

    repository = AnalysisRepository(tmp_path)
    rows = repository.list_runs(market="KR")

    assert len(rows) == 1
    assert rows[0]["exchange"] == "KOSPI"
    assert rows[0]["has_korean_news"] is True
    assert rows[0]["has_dart_disclosures"] is True


@pytest.mark.unit
def test_repository_archive_delete_reindex_and_doctor(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    first = repository.save(_record(sample_record, ticker="NVDA", trade_date="2026-04-30", generated_at="2026-04-30T01:00:00+00:00"))
    second = repository.save(_record(sample_record, ticker="005930.KS", trade_date="2026-04-30", generated_at="2026-04-30T02:00:00+00:00"))

    repository.archive_run(first["run_id"], archived=True)
    assert repository.get_run(first["run_id"]) is None
    assert repository.get_run(first["run_id"], include_archived=True)["run_id"] == first["run_id"]
    assert repository.count_runs() == 1
    assert repository.count_runs(include_archived=True) == 2

    reindexed_before_delete = repository.reindex_from_files(clear=True)
    assert reindexed_before_delete["indexed"] == 2
    assert repository.get_run(first["run_id"]) is None
    assert repository.get_run(first["run_id"], include_archived=True)["run_id"] == first["run_id"]

    payload_path = repository.payload_path(second["run_id"])
    assert payload_path.exists()
    assert repository.delete_run(second["run_id"], delete_payload=True) is True
    assert not payload_path.exists()

    reindexed = repository.reindex_from_files(clear=True)
    assert reindexed["indexed"] == 1
    assert reindexed["failed"] == []
    health = repository.doctor()
    assert health["database_rows"] == 1
    assert health["payload_files"] == 1
    assert health["missing_payloads"] == []
    assert health["orphan_payloads"] == []


@pytest.mark.unit
def test_dashboard_filters_api_latest_and_management_pages(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    repository.save(_record(sample_record, ticker="NVDA", trade_date="2026-04-29", generated_at="2026-04-29T01:00:00+00:00", rating="Hold"))
    repository.save(_record(sample_record, ticker="NVDA", trade_date="2026-04-30", generated_at="2026-04-30T01:00:00+00:00", rating="Sell"))
    repository.save(_record(sample_record, ticker="005930.KS", trade_date="2026-04-30", generated_at="2026-04-30T02:00:00+00:00", rating="Buy", news="## Korean Local Market News\n뉴스"))

    client = TestClient(create_dashboard_app(tmp_path))

    home = client.get("/?market=KR&latest_only=1")
    assert home.status_code == 200
    assert "필터" in home.text
    assert "005930.KS" in home.text
    assert "NVDA" not in home.text
    assert "최신만" in home.text

    runs = client.get("/api/runs?market=US&latest_only=true").json()
    assert runs["total"] == 1
    assert runs["runs"][0]["ticker"] == "NVDA"
    assert runs["runs"][0]["rating"] == "Sell"

    latest = client.get("/api/tickers/NVDA/latest")
    assert latest.status_code == 200
    assert latest.json()["run_id"].startswith("nvda-2026-04-30")

    health = client.get("/management")
    assert health.status_code == 200
    assert "DB 관리" in health.text
    assert "누락 Payload" in health.text

    detail = client.get(f"/runs/{latest.json()['run_id']}")
    assert detail.status_code == 200
    assert "원본 JSON" in detail.text
    assert "섹션 목차" in detail.text
    assert "data-section" in detail.text


@pytest.mark.unit
def test_dashboard_reindex_api_recovers_database_from_payload_files(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    repository.save(_record(sample_record, ticker="NVDA", trade_date="2026-04-30", generated_at="2026-04-30T01:00:00+00:00"))
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("DELETE FROM analyses")
        connection.commit()

    client = TestClient(create_dashboard_app(tmp_path))
    response = client.post("/api/management/reindex", json={"clear": True})
    assert response.status_code == 400

    response = client.post("/api/management/reindex", json={"clear": True, "confirm": "REINDEX"})

    assert response.status_code == 200
    assert response.json()["indexed"] == 1
    assert client.get("/api/runs").json()["total"] == 1
