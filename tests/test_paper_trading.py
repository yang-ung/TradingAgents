from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.unit
def test_paper_trading_ledger_records_signal_without_allowing_live_capital(tmp_path):
    from tradingagents.validation.paper_trading import PaperTradingLedger

    ledger = PaperTradingLedger(tmp_path)

    signal = ledger.record_signal(
        {
            "run_id": "run-1",
            "ticker": "005930.KS",
            "trade_date": "2026-03-01",
            "strategy_id": "005930.KS-2026-03-01-price-timing",
            "signal_date": "2026-03-02",
            "current_price": 196000,
            "entry_low": 196000,
            "entry_high": 196000,
            "take_profit": 222500,
            "stop_loss": 188000,
            "direction_score": 13,
            "entry_timing_score": -36,
            "market_risk_score": -10,
            "pre_live_status_label": "paper trading 후보",
        }
    )

    assert signal["signal_id"].startswith("paper-")
    assert signal["status"] == "paper_trading"
    assert signal["live_capital_allowed"] is False
    assert signal["ticker"] == "005930.KS"
    assert signal["levels"] == {"entry_low": 196000.0, "entry_high": 196000.0, "take_profit": 222500.0, "stop_loss": 188000.0}
    assert ledger.list_signals()[0]["signal_id"] == signal["signal_id"]


@pytest.mark.unit
def test_paper_trading_ledger_rejects_live_or_incomplete_payloads(tmp_path):
    from tradingagents.validation.paper_trading import PaperTradingLedger

    ledger = PaperTradingLedger(tmp_path)

    with pytest.raises(ValueError, match="ticker"):
        ledger.record_signal({"run_id": "run-1"})
    with pytest.raises(ValueError, match="live capital"):
        ledger.record_signal({"ticker": "005930.KS", "live_capital_allowed": True})


@pytest.mark.unit
def test_dashboard_paper_trading_signal_api_records_and_lists(tmp_path):
    from tradingagents.dashboard.app import create_dashboard_app

    client = TestClient(create_dashboard_app(tmp_path))

    response = client.post(
        "/api/paper-trading/signals",
        json={
            "run_id": "run-1",
            "ticker": "005930.KS",
            "trade_date": "2026-03-01",
            "strategy_id": "strategy-1",
            "signal_date": "2026-03-02",
            "current_price": 196000,
            "entry_low": 196000,
            "entry_high": 196000,
            "take_profit": 222500,
            "stop_loss": 188000,
            "pre_live_status_label": "paper trading 후보",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "paper_trading"
    assert response.json()["live_capital_allowed"] is False

    list_response = client.get("/api/paper-trading/signals?ticker=005930.KS")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] == 1
    assert payload["signals"][0]["ticker"] == "005930.KS"
