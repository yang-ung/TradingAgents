from __future__ import annotations

from tradingagents.automation.crypto_signal_engine import (
    build_crypto_snapshot,
    build_crypto_strategy_row,
    build_live_crypto_chart_payload,
    run_crypto_live_paper_execution,
    run_crypto_paper_simulation,
)
from scripts.update_crypto_signal_snapshot import build_hourly_dashboard_report


def _trend_candles(start: float = 100.0, count: int = 80) -> list[dict]:
    candles = []
    price = start
    for idx in range(count):
        price += 1.2
        candles.append(
            {
                "time": f"2026-05-{idx // 24 + 1:02d}T{idx % 24:02d}:00:00+00:00",
                "open": price - 0.8,
                "high": price + 1.0,
                "low": price - 1.3,
                "close": price,
                "volume": 1000 + idx * 10,
            }
        )
    return candles


def _pullback_candles() -> list[dict]:
    candles = _trend_candles()
    for idx in range(74, 80):
        candles[idx]["close"] -= (idx - 73) * 1.8
        candles[idx]["low"] = min(candles[idx]["low"], candles[idx]["close"] - 1.2)
    return candles


def test_crypto_strategy_row_uses_hourly_chart_indicators_and_llm_bias():
    row = build_crypto_strategy_row("BTC-USD", _trend_candles(), llm_bias="bullish")

    assert row["ticker"] == "BTC-USD"
    assert row["asset_class"] == "crypto"
    assert row["timeframe"] == "1h"
    assert row["strategy_name"] == "crypto_hourly_trend_pullback"
    assert row["signal_score"] >= 70
    assert row["action"] in {"모의매수 후보", "모의보유"}
    assert row["entry_zone_label"] != "-"
    assert row["stop_loss"] < row["current_price"]
    assert row["take_profit_1"] > row["current_price"]
    assert row["paper_order_only"] is True
    assert row["live_capital_allowed"] is False
    assert "LLM" in row["strategy_commentary"]


def test_crypto_paper_simulation_tracks_return_and_strategy_revision_trigger():
    rows = [
        build_crypto_strategy_row("BTC-USD", _pullback_candles(), llm_bias="bearish"),
        build_crypto_strategy_row("ETH-USD", _trend_candles(50), llm_bias="bullish"),
    ]

    result = run_crypto_paper_simulation(rows, starting_cash=1_000_000)

    assert result["starting_cash"] == 1_000_000
    assert result["live_capital_allowed"] is False
    assert result["paper_order_only"] is True
    assert result["positions"]
    assert "portfolio_value" in result
    assert "return_percent" in result
    assert isinstance(result["strategy_revision_required"], bool)
    assert result["strategy_revision_reason"]


def test_crypto_paper_simulation_exposes_trade_history_and_profitability_summary():
    rows = [
        build_crypto_strategy_row("BTC-USD", _trend_candles(100), llm_bias="bullish"),
        build_crypto_strategy_row("ETH-USD", _trend_candles(50), llm_bias="bullish"),
    ]
    previous_portfolio = {
        "positions": [{"ticker": "BTC-USD", "quantity": 10.0, "entry_price": 90.0}],
        "trade_history": [{"time": "이전", "ticker": "BTC-USD", "event": "모의매수", "price": 90.0, "quantity": 10.0, "notional": 900.0, "pnl_label": "0원", "reason": "이전 진입"}],
    }

    result = run_crypto_paper_simulation(rows, starting_cash=1_000_000, previous_portfolio=previous_portfolio)

    assert result["cadence_label"] == "암호화폐 1시간마다 갱신"
    assert result["profit_status_label"] in {"수익 중", "손실 중", "본전권"}
    assert result["return_label"].endswith("%")
    assert result["pnl_amount"] == result["portfolio_value"] - result["starting_cash"]
    assert result["positions"][0]["entry_price"] == 90.0
    assert result["positions"][0]["unrealized_pnl"] > 0
    assert result["trade_history"]
    first_trade = result["trade_history"][0]
    assert first_trade["event"] in {"모의평가", "모의매수", "모의보유", "모의관찰"}
    assert first_trade["ticker"] == "BTC-USD"
    assert first_trade["notional"] > 0
    assert first_trade["pnl_label"].endswith("원")


def test_crypto_paper_simulation_executes_paper_trades_not_only_signals():
    rows = [
        {
            "ticker": "BTC-USD",
            "current_price": 120.0,
            "action": "모의매수 후보",
            "signal_score": 82,
            "entry_low": 118.0,
            "entry_high": 121.0,
            "stop_loss": 105.0,
            "take_profit_1": 140.0,
            "time": "2026-05-05T09:00:00+00:00",
        }
    ]

    first = run_crypto_paper_simulation(rows, starting_cash=1_000_000)

    assert first["execution_mode"] == "paper_trade_execution"
    assert first["trade_history"][0]["event"] == "모의매수"
    assert first["trade_history"][0]["reason"] == "시스템 트레이딩이 조건을 만족해 paper 계좌에 모의 매수"
    assert first["positions"][0]["status"] == "paper_open"
    assert first["positions"][0]["take_profit_1"] == 140.0

    exit_rows = [dict(rows[0], current_price=142.0, time="2026-05-05T10:00:00+00:00")]
    second = run_crypto_paper_simulation(exit_rows, starting_cash=1_000_000, previous_portfolio=first)

    assert second["positions"] == []
    assert second["closed_trades"]
    assert second["trade_history"][0]["event"] == "모의매도(익절)"
    assert second["realized_pnl_amount"] > 0
    assert second["cash"] > first["cash"]
    assert "신호" not in second["trade_history"][0]["reason"]


def test_live_crypto_paper_execution_uses_binance_prices_for_fills_pnl_and_fees():
    snapshot = {
        "rows": [
            {"ticker": "BTC-USD", "signal_score": 82, "entry_low": 100.0, "entry_high": 105.0, "stop_loss": 92.0, "take_profit_1": 120.0, "paper_execution_label": "관망"},
            {"ticker": "ETH-USD", "signal_score": 76, "entry_low": 50.0, "entry_high": 55.0, "stop_loss": 45.0, "take_profit_1": 65.0, "paper_execution_label": "관망"},
        ],
        "paper_portfolio": {"starting_cash": 1_000_000, "cash": 1_000_000, "positions": [], "trade_history": []},
        "live_capital_allowed": False,
        "paper_order_only": True,
    }

    first = run_crypto_live_paper_execution(
        snapshot,
        {"BTCUSDT": 102.0, "ETHUSDT": 70.0},
        commission_bps=10,
        quote_to_krw=1.0,
        now="2026-05-05T12:00:00+00:00",
    )

    assert first["price_source"] == "binance_public_ticker"
    assert first["execution_mode"] == "realtime_paper_execution"
    assert first["positions"][0]["ticker"] == "BTC-USD"
    assert first["positions"][0]["entry_price"] == 102.0
    assert first["trade_history"][0]["event"] == "실시간 모의매수"
    assert first["trade_history"][0]["reason"] == "진입 체결"
    assert first["trade_history"][0]["price_label"] == "102"
    assert first["trade_history"][0]["notional_label"] == "200,000"
    assert first["positions"][0]["current_price_label"] == "102"
    assert first["portfolio_value_label"] == "999,800"
    assert first["trade_history"][0]["fee_amount"] > 0
    assert first["total_fees_amount"] == first["trade_history"][0]["fee_amount"]
    assert first["paper_order_only"] is True
    assert first["live_capital_allowed"] is False

    second_snapshot = dict(snapshot, paper_portfolio=first)
    second = run_crypto_live_paper_execution(
        second_snapshot,
        {"BTCUSDT": 121.0, "ETHUSDT": 70.0},
        commission_bps=10,
        quote_to_krw=1.0,
        now="2026-05-05T12:01:00+00:00",
    )

    assert second["positions"] == []
    assert second["closed_trades"][0]["event"] == "실시간 모의매도(익절)"
    assert second["closed_trades"][0]["reason"] == "익절 체결"
    assert second["closed_trades"][0]["price_label"] == "121"
    assert second["realized_pnl_amount"] > 0
    assert second["total_fees_amount"] > first["total_fees_amount"]
    assert second["return_label"].endswith("%")
    assert second["total_pnl_label"].endswith("원")


def test_live_crypto_execution_converts_binance_usdt_prices_to_krw_asset_values():
    snapshot = {
        "rows": [
            {"ticker": "BTC-USD", "signal_score": 82, "entry_low": 80000.0, "entry_high": 82000.0, "stop_loss": 76000.0, "take_profit_1": 90000.0},
        ],
        "paper_portfolio": {"starting_cash": 1_000_000, "cash": 1_000_000, "positions": [], "trade_history": []},
        "live_capital_allowed": False,
        "paper_order_only": True,
    }

    result = run_crypto_live_paper_execution(
        snapshot,
        {"BTCUSDT": 81000.0},
        commission_bps=10,
        quote_to_krw=1400.0,
        now="2026-05-05T12:00:00+00:00",
    )

    position = result["positions"][0]
    assert result["quote_currency"] == "USDT"
    assert result["asset_value_currency"] == "KRW"
    assert result["quote_to_krw"] == 1400.0
    assert position["quantity"] < 0.01
    assert position["market_value"] == 200000.0
    assert position["market_value_label"] == "200,000원"
    assert position["current_price_label"] == "113,400,000원"
    assert position["entry_price_label"] == "113,400,000원"
    assert result["trade_history"][0]["price_label"] == "113,400,000원"
    assert result["trade_history"][0]["notional_label"] == "200,000원"


def test_live_crypto_chart_payload_exposes_binance_klines_and_execution_levels():
    snapshot = {
        "rows": [
            {"ticker": "BTC-USD", "entry_low": 100.0, "entry_high": 105.0, "stop_loss": 92.0, "take_profit_1": 120.0},
        ],
        "paper_portfolio": {"positions": [{"ticker": "BTC-USD", "entry_price": 102.0, "quantity": 1.0}]},
    }
    klines = [
        ["2026-05-05T11:58:00+00:00", 100.0, 103.0, 99.0, 101.0, 10.0],
        ["2026-05-05T11:59:00+00:00", 101.0, 106.0, 100.0, 104.0, 12.0],
    ]

    chart = build_live_crypto_chart_payload("BTC-USD", klines, snapshot=snapshot)

    assert chart["available"] is True
    assert chart["price_source"] == "binance_public_klines"
    assert chart["binance_symbol"] == "BTCUSDT"
    assert chart["candles"][0]["close"] == 101.0
    assert chart["levels"]["entry_low"] == 100.0
    assert chart["levels"]["take_profit_1"] == 120.0
    assert chart["position"]["entry_price"] == 102.0
    assert chart["paper_order_only"] is True
    assert chart["live_capital_allowed"] is False


def test_crypto_snapshot_contains_major_assets_and_paper_portfolio():
    snapshot = build_crypto_snapshot(
        {"BTC-USD": _trend_candles(), "ETH-USD": _pullback_candles()},
        llm_market_view={"bias": "neutral", "summary": "고변동성 구간이므로 포지션 크기를 제한"},
    )

    assert snapshot["asset_class"] == "crypto"
    assert snapshot["timeframe"] == "1h"
    assert snapshot["llm_market_view"]["summary"] == "고변동성 구간이므로 포지션 크기를 제한"
    assert len(snapshot["rows"]) == 2
    assert snapshot["paper_portfolio"]["paper_order_only"] is True
    assert snapshot["paper_portfolio"]["trade_history"]
    assert snapshot["paper_portfolio"]["profit_status_label"]
    assert snapshot["paper_portfolio"]["total_pnl_label"]
    assert snapshot["crypto_update_cadence_label"] == "암호화폐 1시간마다 갱신"
    assert snapshot["paper_portfolio"]["execution_mode"] == "paper_trade_execution"
    assert snapshot["rows"][0]["paper_execution_label"] in {"모의매수", "모의보유", "모의매도(익절)", "모의매도(손절)", "관망"}
    assert "후보" not in snapshot["rows"][0]["paper_execution_label"]
    assert snapshot["live_capital_allowed"] is False
    assert snapshot["paper_order_only"] is True


def test_hourly_dashboard_report_combines_stock_crypto_and_safety_sections():
    stock_snapshot = {
        "generated_at": "2026-05-04T09:00:00+00:00",
        "market_score": 67,
        "previous_market_score": 62,
        "score_delta": 5,
        "risk_level_label": "약한 risk-on",
        "change_summary": "시장 점수 62 → 67 (+5): 지수 추세 +3점",
        "factor_breakdown": [{"label": "지수 추세", "contribution_label": "+8점", "delta_label": "+3점"}],
        "rows": [
            {"ticker": "QLD", "action": "매수 후보", "status_label": "진입구간 터치", "entry_zone_label": "90.00 ~ 92.00", "take_profit_1": 98.0, "stop_loss": 87.0}
        ],
    }
    crypto_snapshot = build_crypto_snapshot({"BTC-USD": _trend_candles()}, llm_market_view={"bias": "neutral", "summary": "중립"})

    report = build_hourly_dashboard_report(
        stock_snapshot,
        crypto_snapshot,
        report_stage="KRX 시작 전",
        report_purpose="정규장 진입 전 최종 판단",
        api_checks=[{"name": "Crypto API", "status": "ok", "url": "/api/signals/crypto"}],
    )

    assert report["title"] == "TradingAgents 장 운영 전략 리포트"
    assert report["report_stage"] == "KRX 시작 전"
    assert report["report_purpose"] == "정규장 진입 전 최종 판단"
    assert report["report_cadence_label"] == "장 이벤트 기준 리포트 · 시스템 트레이딩 상시 추적"
    assert report["market_summary"]["market_score"] == 67
    assert report["stock_signals"][0]["ticker"] == "QLD"
    assert report["stock_signals"][0]["risk_reward_label"] == "목표 98.0 / 손절 87.0"
    assert report["crypto_signals"][0]["ticker"] == "BTC-USD"
    assert report["dashboard_updates"] == ["ETF/주식 가격표 갱신", "암호화폐 모의투자 갱신", "장 운영 리포트 보드 갱신"]
    assert report["safety"]["live_capital_allowed"] is False
    assert report["safety"]["paper_order_only"] is True
    assert report["api_checks"][0]["name"] == "Crypto API"
