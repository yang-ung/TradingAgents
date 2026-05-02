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


def _paper_signal_payload(**overrides):
    payload = {
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
    }
    payload.update(overrides)
    return payload


@pytest.mark.unit
def test_paper_trading_ledger_updates_fill_exit_and_cost_adjusted_pnl(tmp_path):
    from tradingagents.validation.paper_trading import PaperTradingLedger

    ledger = PaperTradingLedger(tmp_path)
    signal = ledger.record_signal(_paper_signal_payload(entry_low=100, entry_high=101, take_profit=110, stop_loss=95))

    updated = ledger.update_signal_with_ohlc(
        signal["signal_id"],
        [
            {"date": "2026-03-02", "open": 104, "high": 105, "low": 102, "close": 104},
            {"date": "2026-03-03", "open": 102, "high": 103, "low": 99, "close": 102},
            {"date": "2026-03-04", "open": 108, "high": 111, "low": 106, "close": 110},
        ],
        capital=100_000_000,
    )

    assert updated["status"] == "paper_closed"
    assert updated["live_capital_allowed"] is False
    assert updated["fill"]["fillable"] is True
    assert updated["fill"]["assumed_fill_price"] == 101.0
    assert updated["fill"]["entry_date"] == "2026-03-03"
    assert updated["fill"]["exit_price"] == 110.0
    assert updated["fill"]["exit_date"] == "2026-03-04"
    assert updated["fill"]["exit_reason"] == "익절"
    assert updated["fill"]["return_percent"] == 8.61
    assert updated["fill"]["pnl"] == 8_610_000
    assert ledger.list_signals()[0]["fill"]["exit_reason"] == "익절"


@pytest.mark.unit
def test_paper_trading_ledger_uses_stop_first_when_target_and_stop_touch_same_day(tmp_path):
    from tradingagents.validation.paper_trading import PaperTradingLedger

    ledger = PaperTradingLedger(tmp_path)
    signal = ledger.record_signal(_paper_signal_payload(entry_low=100, entry_high=101, take_profit=110, stop_loss=95))

    updated = ledger.update_signal_with_ohlc(
        signal["signal_id"],
        [{"date": "2026-03-02", "open": 100, "high": 112, "low": 94, "close": 108}],
        capital=100_000_000,
    )

    assert updated["status"] == "paper_closed"
    assert updated["fill"]["exit_reason"] == "손절"
    assert updated["fill"]["exit_price"] == 95.0
    assert updated["fill"]["return_percent"] == -6.24
    assert updated["fill"]["pnl"] == -6_240_000


@pytest.mark.unit
def test_paper_trading_ledger_preserves_open_state_across_incremental_updates(tmp_path):
    from tradingagents.validation.paper_trading import PaperTradingLedger

    ledger = PaperTradingLedger(tmp_path)
    signal = ledger.record_signal(_paper_signal_payload(entry_low=100, entry_high=101, take_profit=110, stop_loss=95))

    open_signal = ledger.update_signal_with_ohlc(
        signal["signal_id"],
        [{"date": "2026-03-02", "open": 102, "high": 103, "low": 99, "close": 102}],
        capital=100_000_000,
    )
    assert open_signal["status"] == "paper_open"

    closed = ledger.update_signal_with_ohlc(
        signal["signal_id"],
        [{"date": "2026-03-03", "open": 108, "high": 111, "low": 106, "close": 110}],
        capital=100_000_000,
    )

    assert closed["status"] == "paper_closed"
    assert closed["fill"]["entry_date"] == "2026-03-02"
    assert closed["fill"]["exit_reason"] == "익절"


@pytest.mark.unit
def test_paper_trading_ledger_does_not_reopen_or_downgrade_closed_signal(tmp_path):
    from tradingagents.validation.paper_trading import PaperTradingLedger

    ledger = PaperTradingLedger(tmp_path)
    signal = ledger.record_signal(_paper_signal_payload(entry_low=100, entry_high=101, take_profit=110, stop_loss=95))
    closed = ledger.update_signal_with_ohlc(
        signal["signal_id"],
        [{"date": "2026-03-02", "open": 100, "high": 112, "low": 99, "close": 111}],
        capital=100_000_000,
    )

    second_update = ledger.update_signal_with_ohlc(
        signal["signal_id"],
        [{"date": "2026-03-03", "open": 120, "high": 121, "low": 119, "close": 120}],
        capital=100_000_000,
    )

    assert second_update == closed
    assert ledger.list_signals()[0]["status"] == "paper_closed"


@pytest.mark.unit
def test_paper_trading_ledger_rejects_negative_update_capital(tmp_path):
    from tradingagents.validation.paper_trading import PaperTradingLedger

    ledger = PaperTradingLedger(tmp_path)
    signal = ledger.record_signal(_paper_signal_payload())

    with pytest.raises(ValueError, match="capital"):
        ledger.update_signal_with_ohlc(
            signal["signal_id"],
            [{"date": "2026-03-02", "open": 196000, "high": 223000, "low": 195000, "close": 222500}],
            capital=-100_000_000,
        )


@pytest.mark.unit
def test_paper_trading_ledger_ignores_pre_entry_candles_for_existing_open_signal(tmp_path):
    from tradingagents.validation.paper_trading import PaperTradingLedger

    ledger = PaperTradingLedger(tmp_path)
    signal = ledger.record_signal(_paper_signal_payload(entry_low=100, entry_high=101, take_profit=110, stop_loss=95))
    open_signal = ledger.update_signal_with_ohlc(
        signal["signal_id"],
        [{"date": "2026-03-03", "open": 102, "high": 103, "low": 99, "close": 102}],
        capital=100_000_000,
    )
    assert open_signal["status"] == "paper_open"

    closed = ledger.update_signal_with_ohlc(
        signal["signal_id"],
        [
            {"date": "2026-03-02", "open": 100, "high": 103, "low": 94, "close": 96},
            {"date": "2026-03-03", "open": 102, "high": 103, "low": 99, "close": 102},
            {"date": "2026-03-04", "open": 108, "high": 111, "low": 106, "close": 110},
        ],
        capital=100_000_000,
    )

    assert closed["status"] == "paper_closed"
    assert closed["fill"]["entry_date"] == "2026-03-03"
    assert closed["fill"]["exit_date"] == "2026-03-04"
    assert closed["fill"]["exit_reason"] == "익절"
    assert "unrealized_pnl" not in closed["fill"]
    assert "unrealized_return_percent" not in closed["fill"]


@pytest.mark.unit
def test_dashboard_paper_trading_signal_api_records_lists_and_updates_fill(tmp_path):
    from tradingagents.dashboard.app import create_dashboard_app

    client = TestClient(create_dashboard_app(tmp_path))

    response = client.post(
        "/api/paper-trading/signals",
        json=_paper_signal_payload(),
    )

    assert response.status_code == 201
    signal_id = response.json()["signal_id"]
    assert response.json()["status"] == "paper_trading"
    assert response.json()["live_capital_allowed"] is False

    update_response = client.patch(
        f"/api/paper-trading/signals/{signal_id}/fill",
        json={"capital": 100_000_000, "candles": [{"date": "2026-03-02", "open": 196000, "high": 223000, "low": 195000, "close": 222500}]},
    )
    assert update_response.status_code == 200
    assert update_response.json()["status"] == "paper_closed"
    assert update_response.json()["fill"]["exit_reason"] == "익절"

    list_response = client.get("/api/paper-trading/signals?ticker=005930.KS")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] == 1
    assert payload["signals"][0]["ticker"] == "005930.KS"
    assert payload["signals"][0]["fill"]["exit_reason"] == "익절"
