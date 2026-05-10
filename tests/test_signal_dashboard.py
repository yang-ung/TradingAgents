from __future__ import annotations

import json
import sqlite3
import threading

from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app


def test_crypto_background_worker_runs_live_paper_execution_without_browser_polling(tmp_path, monkeypatch):
    crypto_snapshot = {
        "asset_class": "crypto",
        "paper_order_only": True,
        "live_capital_allowed": False,
        "paper_portfolio": {"execution_mode": "paper_trade_execution"},
        "rows": [{"ticker": "BTC-USD", "current_price": 81000, "signal_score": 75}],
    }
    (tmp_path / "crypto_signal_snapshot.json").write_text(json.dumps(crypto_snapshot), encoding="utf-8")
    worker_called = threading.Event()
    calls: list[str] = []

    def fake_live_pnl(base_dir, snapshot):
        calls.append(str(base_dir))
        assert snapshot["rows"][0]["ticker"] == "BTC-USD"
        worker_called.set()
        return {
            "execution_mode": "realtime_paper_execution",
            "price_source": "binance_public_ticker",
            "paper_order_only": True,
            "live_capital_allowed": False,
        }

    monkeypatch.setattr("tradingagents.dashboard.app._build_live_pnl_from_snapshot", fake_live_pnl)
    app = create_dashboard_app(tmp_path, crypto_worker_enabled=True, crypto_worker_interval_seconds=3600)

    with TestClient(app) as client:
        assert worker_called.wait(timeout=2)
        status_response = client.get("/api/signals/crypto/worker-status")

    assert calls == [str(tmp_path)]
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["enabled"] is True
    assert status["running"] is True
    assert status["run_count"] == 1
    assert status["last_success_at"]
    assert status["last_error"] is None


def test_crypto_live_pnl_persists_entry_price_in_sqlite_across_api_calls(tmp_path, monkeypatch):
    crypto_snapshot = {
        "asset_class": "crypto",
        "paper_order_only": True,
        "live_capital_allowed": False,
        "paper_portfolio": {"starting_cash": 1_000_000, "cash": 1_000_000, "positions": [], "trade_history": []},
        "rows": [
            {
                "ticker": "BTC-USD",
                "signal_score": 75,
                "entry_low": 80000,
                "entry_high": 83000,
                "stop_loss": 79000,
                "take_profit_1": 85000,
            }
        ],
    }
    (tmp_path / "crypto_signal_snapshot.json").write_text(json.dumps(crypto_snapshot), encoding="utf-8")
    price_batches = iter([{"BTCUSDT": 81000.0}, {"BTCUSDT": 82000.0}])
    monkeypatch.setattr("tradingagents.dashboard.app._fetch_usdkrw_rate", lambda: 1400.0)
    monkeypatch.setattr("tradingagents.dashboard.app._fetch_binance_prices", lambda symbols: next(price_batches))

    client = TestClient(create_dashboard_app(tmp_path))

    first = client.get("/api/signals/crypto/live-pnl")
    second = client.get("/api/signals/crypto/live-pnl")

    assert first.status_code == 200
    assert second.status_code == 200
    first_position = first.json()["positions"][0]
    second_position = second.json()["positions"][0]
    assert first_position["entry_price"] == 81000.0
    assert first_position["current_price"] == 81000.0
    assert second_position["entry_price"] == 81000.0
    assert second_position["current_price"] == 82000.0
    assert second.json()["state_persistence"] == "sqlite"
    with sqlite3.connect(tmp_path / "dashboard.db") as connection:
        stored_json = connection.execute(
            "SELECT state_json FROM crypto_live_paper_state WHERE state_key = 'default'"
        ).fetchone()[0]
    stored = json.loads(stored_json)
    assert stored["positions"][0]["entry_price"] == 81000.0
    assert stored["positions"][0]["current_price"] == 82000.0


def test_dashboard_exposes_signal_price_table_snapshot(tmp_path):
    snapshot = {
        "timestamp": "2026-05-04T09:00:00+00:00",
        "market_score": 67,
        "risk_level": "weak_risk_on",
        "risk_off": False,
        "previous_market_score": 62,
        "score_delta": 5,
        "factor_scores": {"equity_trend": 8, "volatility": 4, "rates": -1, "fx": 0, "news_sentiment": 5, "macro_risk": 1},
        "factor_deltas": {"equity_trend": 3, "volatility": 1, "rates": -1, "fx": 0, "news_sentiment": 2, "macro_risk": 0},
        "change_explanation": ["지수 추세 +3점", "뉴스 심리 +2점", "금리 -1점"],
        "change_summary": "시장 점수 62 → 67 (+5): 지수 추세 +3점, 뉴스 심리 +2점, 금리 -1점",
        "score_base": 50,
        "score_formula": "기준점수 50 + 지수 추세 +8 + 변동성 +4 + 금리 -1 + 환율 0 + 뉴스 심리 +5 + 거시 리스크 +1 = 67",
        "factor_breakdown": [
            {"key": "equity_trend", "label": "지수 추세", "score": 8, "previous_score": 5, "delta": 3, "contribution_label": "+8점", "delta_label": "+3점", "description": "SPY/QQQ 등 주요 지수의 단기·중기 추세 강도"},
            {"key": "volatility", "label": "변동성", "score": 4, "previous_score": 3, "delta": 1, "contribution_label": "+4점", "delta_label": "+1점", "description": "VIX/변동성 체계가 위험 선호에 주는 영향"},
            {"key": "rates", "label": "금리", "score": -1, "previous_score": 0, "delta": -1, "contribution_label": "-1점", "delta_label": "-1점", "description": "미국 국채금리와 금리 기대 변화"},
        ],
        "rows": [
            {
                "ticker": "QLD",
                "current_price": 91.4,
                "market_score": 67,
                "pattern_score": 81,
                "setup_type": "bullish_fvg_retest",
                "status_label": "진입구간 터치",
                "action": "매수 후보",
                "entry_zone_label": "90.00 ~ 92.00",
                "stop_loss": 87.0,
                "take_profit_1": 98.0,
                "live_capital_allowed": False,
            }
        ],
    }
    crypto_snapshot = {
        "asset_class": "crypto",
        "timeframe": "1h",
        "generated_at": "2026-05-04T09:00:00+00:00",
        "live_capital_allowed": False,
        "paper_order_only": True,
        "llm_market_view": {"bias": "neutral", "summary": "비트코인 주도 변동성 확대, 모의투자만 허용"},
        "crypto_update_cadence": "hourly",
        "crypto_update_cadence_label": "암호화폐 1시간마다 갱신",
        "paper_portfolio": {
            "execution_mode": "paper_trade_execution",
            "starting_cash": 1000000,
            "portfolio_value": 1005000,
            "return_percent": 0.5,
            "return_label": "+0.50%",
            "pnl_amount": 5000,
            "pnl_label": "+5,000원",
            "profit_status_label": "수익 중",
            "positions": [
                {
                    "ticker": "BTC-USD",
                    "quantity": 0.0028,
                    "entry_price": 67500,
                    "current_price": 69000,
                    "market_value": 193200,
                    "unrealized_pnl": 4200,
                    "unrealized_return_percent": 2.22,
                    "stop_loss": 65000,
                    "take_profit_1": 73000,
                }
            ],
            "trade_history": [
                {"time": "2026-05-04 09:00", "ticker": "BTC-USD", "event": "모의매수", "price": 69000, "quantity": 0.0028, "notional": 200000, "pnl_label": "+5,000원", "reason": "시스템 트레이딩이 조건을 만족해 paper 계좌에 모의 매수"}
            ],
            "paper_order_only": True,
            "live_capital_allowed": False,
        },
        "strategy_revision": {"required": False, "reason": "초기 모의투자 관찰", "check_after": "hourly_paper_return_review"},
        "rows": [
            {
                "ticker": "BTC-USD",
                "current_price": 69000,
                "signal_score": 76,
                "action": "모의매수 후보",
                "entry_zone_label": "67500.00 ~ 69000.00",
                "stop_loss": 65000,
                "take_profit_1": 73000,
                "strategy_commentary": "차트 추세는 우상향, LLM 시장뷰는 중립",
                "paper_order_only": True,
                "live_capital_allowed": False,
            }
        ],
    }
    hourly_report = {
        "generated_at": "2026-05-04T09:00:00+00:00",
        "title": "TradingAgents 장 운영 전략 리포트",
        "report_stage": "KRX 시작 전",
        "report_purpose": "정규장 진입 전 최종 판단",
        "report_cadence_label": "장 이벤트 기준 리포트 · 시스템 트레이딩 상시 추적",
        "market_summary": {
            "market_score": 67,
            "previous_market_score": 62,
            "score_delta": 5,
            "risk_level_label": "약한 risk-on",
            "change_summary": "시장 점수 62 → 67 (+5): 지수 추세 +3점, 뉴스 심리 +2점",
        },
        "stock_signals": [
            {"ticker": "QLD", "action": "매수 후보", "status_label": "진입구간 터치", "entry_zone_label": "90.00 ~ 92.00", "risk_reward_label": "목표 98.0 / 손절 87.0"}
        ],
        "crypto_signals": [
            {"ticker": "BTC-USD", "action": "모의매수 후보", "signal_score": 76, "entry_zone_label": "67500.00 ~ 69000.00", "paper_return_label": "+0.50%"}
        ],
        "factor_breakdown": snapshot["factor_breakdown"],
        "dashboard_updates": ["ETF/주식 가격표 갱신", "암호화폐 모의투자 갱신", "공개 대시보드 반영"],
        "safety": {"live_capital_allowed": False, "paper_order_only": True, "label": "실거래 차단 · 모의주문 전용"},
        "api_checks": [
            {"name": "ETF/주식 API", "status": "ok", "url": "/api/signals/price-table"},
            {"name": "Crypto API", "status": "ok", "url": "/api/signals/crypto"},
        ],
    }
    (tmp_path / "signal_snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (tmp_path / "crypto_signal_snapshot.json").write_text(json.dumps(crypto_snapshot), encoding="utf-8")
    (tmp_path / "hourly_report.json").write_text(json.dumps(hourly_report), encoding="utf-8")
    (tmp_path / "crypto_live_paper_state.json").write_text(json.dumps(dict(crypto_snapshot["paper_portfolio"], price_source="binance_public_ticker")), encoding="utf-8")
    client = TestClient(create_dashboard_app(tmp_path))

    api_response = client.get("/api/signals/price-table")
    crypto_response = client.get("/api/signals/crypto")
    live_pnl_response = client.get("/api/signals/crypto/live-pnl")
    chart_response = client.get("/api/signals/crypto/chart/BTC-USD")
    etf_detail = client.get("/signals/price-table/QLD")
    crypto_detail = client.get("/signals/crypto/BTC-USD")
    home_response = client.get("/")
    crypto_page = client.get("/dashboards/crypto")
    etf_page = client.get("/dashboards/etf")
    kospi_page = client.get("/dashboards/kospi")
    nasdaq_page = client.get("/dashboards/nasdaq")

    assert api_response.status_code == 200
    assert crypto_response.status_code == 200
    assert live_pnl_response.status_code == 200
    assert live_pnl_response.json()["price_source"] == "binance_public_ticker"
    assert live_pnl_response.json()["execution_mode"] in {"realtime_paper_execution", "paper_trade_execution"}
    assert chart_response.status_code == 200
    assert etf_detail.status_code == 200
    assert crypto_detail.status_code == 200
    assert chart_response.json()["price_source"] in {"binance_public_klines", "snapshot_fallback"}
    assert crypto_response.json()["rows"][0]["ticker"] == "BTC-USD"
    assert api_response.json()["market_score"] == 67
    assert api_response.json()["rows"][0]["action"] == "매수 후보"
    assert home_response.status_code == 200
    assert crypto_page.status_code == 200
    assert etf_page.status_code == 200
    assert kospi_page.status_code == 200
    assert nasdaq_page.status_code == 200
    assert "종합 요약" in home_response.text
    assert "암호화폐 전략" in home_response.text
    assert "ETF 전략" in home_response.text
    assert "코스피 분석" in home_response.text
    assert "나스닥 분석" in home_response.text
    assert "오늘의 투자 판단" in home_response.text
    assert "장 이벤트마다 전략 리포트 갱신" not in home_response.text
    assert "시스템 트레이딩 추적 중" not in home_response.text
    assert "타이밍 감지 시 모의거래" not in home_response.text
    assert "strategy-update-step" not in home_response.text
    assert "dashboard-tab-icon" in home_response.text
    assert "dashboard-tab-meta" in home_response.text
    assert "시장 점수" in home_response.text
    assert "시장 점수 62 → 67 (+5)" in home_response.text
    assert "지수 추세 +3점" in home_response.text
    assert "뉴스 심리 +2점" in home_response.text
    assert "시장점수 구성" not in home_response.text
    assert "자동매매 가격표" not in home_response.text
    assert "비트코인 주도 변동성 확대" not in home_response.text
    assert "암호화폐 전략" in crypto_page.text
    assert "오늘의 암호화폐 전략 판단" in crypto_page.text
    assert "암호화폐 1시간마다 갱신" in crypto_page.text
    assert "Binance 실시간 가격" in crypto_page.text
    assert "실시간 PnL" in crypto_page.text
    assert "가격 차트" in crypto_page.text
    assert "최근 체결" in crypto_page.text
    assert "거래 수수료" in crypto_page.text
    assert "/api/signals/crypto/live-pnl" in crypto_page.text
    assert "/api/signals/crypto/chart/BTC-USD" in crypto_page.text
    assert "data-live-pnl" in crypto_page.text
    assert "crypto-live-chart" in crypto_page.text
    assert "포트폴리오 수익률" in crypto_page.text
    assert "현재 자산 내역" in crypto_page.text
    assert "보유수량" in crypto_page.text
    assert "매수가" in crypto_page.text
    assert "평가금액" in crypto_page.text
    assert "평가손익" in crypto_page.text
    assert "+2.22%" in crypto_page.text
    assert "+4,200원" in crypto_page.text
    assert "193,200" in crypto_page.text
    assert "data-live-assets" in crypto_page.text
    assert "data-live-trades-body" in crypto_page.text
    assert "renderLiveTrades(data.trade_history)" in crypto_page.text
    assert "수익 중" in crypto_page.text
    assert "+5,000원" in crypto_page.text
    assert "1,005,000" in crypto_page.text
    assert "69,000" in crypto_page.text
    assert "67500.00" not in crypto_page.text
    assert "69000" not in crypto_page.text
    assert "체결 내역" in crypto_page.text
    assert "현재 전략" in crypto_page.text
    assert "코인별 전략 에이전트 의견" in crypto_page.text
    assert "전략 에이전트 의견" in crypto_page.text
    assert "asset-opinion-card" in crypto_page.text
    assert "/signals/crypto/BTC-USD" in crypto_page.text
    assert "타이밍" in crypto_page.text
    assert "신뢰도" in crypto_page.text
    assert "매수가" in crypto_page.text
    assert "목표가" in crypto_page.text
    assert "손절가" in crypto_page.text
    assert "체결 내역" in crypto_page.text
    assert "시간축" in crypto_page.text
    assert "chart-time-label" in crypto_page.text
    assert "chart-price-axis" in crypto_page.text
    assert "paper 계좌 실행" not in crypto_page.text
    assert "paper-only" not in crypto_page.text
    assert "live_capital_allowed=false" not in crypto_page.text
    assert "실거래가 아닌 paper 계좌 모의투자로만 실행합니다" not in crypto_page.text
    assert "신호 전송이 아니라 Binance 가격 기준 paper 계좌 상태를 전환합니다" not in crypto_page.text
    assert "시스템이 paper 계좌에서 실행한 모의 매수/보유/매도" not in crypto_page.text
    assert "보유 포지션을 현재가로 평가" not in crypto_page.text
    assert "근거</th>" not in crypto_page.text
    assert "모의보유" not in crypto_page.text
    assert "모의매수" not in crypto_page.text
    assert "매수" in crypto_page.text
    assert "BTC-USD" in crypto_page.text
    assert "crypto-candlestick-chart" in crypto_page.text
    assert "가격축" in crypto_page.text
    assert "비트코인 주도 변동성 확대" in crypto_page.text
    assert "모의매수 후보 신호" not in crypto_page.text
    assert "ETF 전략" in etf_page.text
    assert "오늘의 ETF 전략 판단" in etf_page.text
    assert "QLD" in etf_page.text
    assert "/signals/price-table/QLD" in etf_page.text
    assert "상세 보기" in etf_page.text
    assert "시장점수 구성" in etf_page.text
    assert "기준점수 50" in etf_page.text
    assert "SPY/QQQ 등 주요 지수의 단기·중기 추세 강도" in etf_page.text
    assert "현재 +8점" in etf_page.text
    assert "ETF/주식 가격 레벨 전략 상세" in etf_detail.text
    assert "오늘의 전략 판단" in etf_detail.text
    assert "상세 가격 레벨" in etf_detail.text
    assert "진입구간 터치" in etf_detail.text
    assert "90.00 ~ 92.00" in etf_detail.text
    assert "암호화폐 개별 전략 상세" in crypto_detail.text
    assert "전략 에이전트 의견" in crypto_detail.text
    assert "가격 차트" in crypto_detail.text
    assert "/api/signals/crypto/chart/BTC-USD" in crypto_detail.text
    assert "RSI14" not in crypto_detail.text
    assert "코스피 분석" in kospi_page.text
    assert "분석 실행 목록" in kospi_page.text
    assert "전략 성과 점검" in kospi_page.text
    assert "수익률 리더보드" not in kospi_page.text
    assert "나스닥 분석" in nasdaq_page.text
    assert "분석 실행 목록" in nasdaq_page.text
    assert "진입구간 터치" in home_response.text
    assert "live_capital_allowed=false" not in home_response.text
    assert "장 운영 전략 리포트" in home_response.text
    assert "ETF/주식 가격표 갱신" not in home_response.text
    assert "암호화폐 모의투자 갱신" not in home_response.text
    assert "실거래 차단 · 모의주문 전용" not in home_response.text
    assert "Crypto API" not in home_response.text
