from __future__ import annotations

import json
import sqlite3

import pytest

from tradingagents.dashboard.storage import AnalysisRepository


@pytest.mark.unit
def test_repository_save_and_list_round_trip(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)

    runs = repository.list_runs()

    assert len(runs) == 1
    assert runs[0]["ticker"] == "NVDA"
    assert runs[0]["rating"] == "Hold"
    assert stored["structured_path"].endswith(f"{stored['run_id']}.json")
    assert repository.latest_generated_at() == "2026-04-29T23:00:00+00:00"


@pytest.mark.unit
def test_repository_get_run_returns_full_payload(tmp_path, sample_record):
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)

    fetched = repository.get_run(stored["run_id"])

    assert fetched is not None
    assert fetched["run_id"] == stored["run_id"]
    assert fetched["reports"]["final_trade_decision"].startswith("**Rating**: Hold")


@pytest.mark.unit
def test_repository_persists_crypto_live_paper_state_in_sqlite(tmp_path):
    repository = AnalysisRepository(tmp_path)
    state = {
        "execution_mode": "realtime_paper_execution",
        "generated_at": "2026-05-06T03:10:00+00:00",
        "state_version": 2,
        "quote_currency": "USDT",
        "display_currency": "KRW",
        "quote_to_krw": 1469.38,
        "positions": [{"ticker": "BTC-USD", "entry_price": 81335.36, "current_price": 81382.9}],
        "trade_history": [{"ticker": "BTC-USD", "event": "매수"}],
        "paper_order_only": True,
        "live_capital_allowed": False,
    }

    repository.save_crypto_live_paper_state(state)
    loaded = AnalysisRepository(tmp_path).get_crypto_live_paper_state()

    assert loaded is not None
    assert loaded["generated_at"] == "2026-05-06T03:10:00+00:00"
    assert loaded["positions"][0]["entry_price"] == 81335.36
    assert loaded["positions"][0]["current_price"] == 81382.9
    with sqlite3.connect(tmp_path / "dashboard.db") as connection:
        rows = connection.execute("SELECT state_key, state_json FROM crypto_live_paper_state").fetchall()
    assert rows == [("default", json.dumps(state, ensure_ascii=False, sort_keys=True))]


@pytest.mark.unit
def test_repository_migrates_legacy_crypto_live_state_json_into_sqlite(tmp_path):
    legacy_state = {
        "generated_at": "2026-05-06T02:57:43+00:00",
        "state_version": 2,
        "quote_currency": "USDT",
        "display_currency": "KRW",
        "quote_to_krw": 1469.385911,
        "positions": [{"ticker": "XRP-USD", "entry_price": 1.4163, "current_price": 1.4168}],
    }
    (tmp_path / "crypto_live_paper_state.json").write_text(json.dumps(legacy_state), encoding="utf-8")

    repository = AnalysisRepository(tmp_path)

    assert repository.get_crypto_live_paper_state() == legacy_state
    (tmp_path / "crypto_live_paper_state.json").unlink()
    assert AnalysisRepository(tmp_path).get_crypto_live_paper_state() == legacy_state
