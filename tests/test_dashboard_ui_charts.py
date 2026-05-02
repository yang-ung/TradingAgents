from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.storage import AnalysisRepository


@pytest.mark.unit
def test_price_chart_builds_series_svg_path_and_stats():
    from tradingagents.dashboard.charts import build_price_chart

    rows = [
        {"date": "2026-04-24", "open": 98.0, "high": 104.0, "low": 96.0, "close": 100.0, "volume": 1000},
        {"date": "2026-04-25", "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0, "volume": 1200},
        {"date": "2026-04-26", "open": 103.0, "high": 104.0, "low": 100.0, "close": 101.0, "volume": 900},
        {"date": "2026-04-27", "open": 101.0, "high": 109.0, "low": 100.0, "close": 108.0, "volume": 1500},
    ]

    chart = build_price_chart("005930.KS", rows)

    assert chart["available"] is True
    assert chart["ticker"] == "005930.KS"
    assert chart["points"] == rows
    assert chart["path"].startswith("M ")
    assert chart["area_path"].endswith("Z")
    assert chart["latest_close"] == 108.0
    assert chart["first_close"] == 100.0
    assert chart["change"] == 8.0
    assert chart["change_percent"] == 8.0
    assert chart["min_close"] == 100.0
    assert chart["max_close"] == 108.0
    assert chart["candles"] == [
        {"time": "2026-04-24", "open": 98.0, "high": 104.0, "low": 96.0, "close": 100.0},
        {"time": "2026-04-25", "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0},
        {"time": "2026-04-26", "open": 103.0, "high": 104.0, "low": 100.0, "close": 101.0},
        {"time": "2026-04-27", "open": 101.0, "high": 109.0, "low": 100.0, "close": 108.0},
    ]
    assert chart["volume"][-1] == {"time": "2026-04-27", "value": 1500, "color": "rgba(34, 197, 94, 0.42)"}
    assert chart["moving_averages"]["ma5"] == []
    assert chart["moving_average_periods"] == [5, 20, 60]


@pytest.mark.unit
def test_price_chart_computes_moving_averages():
    from tradingagents.dashboard.charts import build_price_chart

    rows = [{"date": f"2026-04-{day:02d}", "close": float(day), "volume": day * 100} for day in range(1, 22)]

    chart = build_price_chart("NVDA", rows)

    assert chart["moving_averages"]["ma5"][0] == {"time": "2026-04-05", "value": 3.0}
    assert chart["moving_averages"]["ma20"][0] == {"time": "2026-04-20", "value": 10.5}
    assert chart["moving_averages"]["ma60"] == []
    assert chart["candles"][0] == {"time": "2026-04-01", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}


@pytest.mark.unit
def test_naver_sise_json_parser_extracts_korean_ohlcv_rows():
    from tradingagents.dashboard.charts import _parse_naver_sise_json

    text = '''[['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],
["20260429", 219500, 228000, 218500, 226000, 20363756, 49.27],
["20260430", 229000, 230000, 220500, 220500, 20519819, 49.27]
]'''

    assert _parse_naver_sise_json(text) == [
        {"date": "2026-04-29", "open": 219500.0, "high": 228000.0, "low": 218500.0, "close": 226000.0, "volume": 20363756.0},
        {"date": "2026-04-30", "open": 229000.0, "high": 230000.0, "low": 220500.0, "close": 220500.0, "volume": 20519819.0},
    ]


@pytest.mark.unit
def test_korean_ticker_code_requires_six_digits():
    from tradingagents.dashboard.charts import _korean_ticker_code

    assert _korean_ticker_code("005930.KS") == "005930"
    with pytest.raises(ValueError):
        _korean_ticker_code("005930.KS/../../bad")


@pytest.mark.unit
def test_korean_ticker_chart_prefers_naver_source(monkeypatch):
    from tradingagents.dashboard import charts

    monkeypatch.setattr(
        charts,
        "_fetch_naver_sise_json",
        lambda code, start, end: '[["날짜","시가","고가","저가","종가","거래량"],["20260430", 229000, 230000, 220500, 220500, 20519819]]',
    )

    chart = charts.get_price_chart("005930.KS", "2026-04-30", lookback_days=14)

    assert chart["available"] is True
    assert chart["data_source"] == "Naver Finance"
    assert chart["currency"] == "KRW"
    assert chart["points"][-1] == {"date": "2026-04-30", "open": 229000.0, "high": 230000.0, "low": 220500.0, "close": 220500.0, "volume": 20519819}


@pytest.mark.unit
def test_price_chart_normalizes_ohlc_and_rejects_negative_volume():
    from tradingagents.dashboard.charts import build_price_chart

    chart = build_price_chart(
        "ZERO",
        [{"date": "2026-04-01", "open": 0.0, "high": 1.0, "low": 1.0, "close": 0.5, "volume": -1}],
    )

    assert chart["points"] == [{"date": "2026-04-01", "open": 0.0, "high": 1.0, "low": 0.0, "close": 0.5}]
    assert chart["candles"] == [{"time": "2026-04-01", "open": 0.0, "high": 1.0, "low": 0.0, "close": 0.5}]
    assert chart["volume"] == []


@pytest.mark.unit
def test_price_chart_drops_non_finite_prices_and_volume():
    from tradingagents.dashboard.charts import build_price_chart

    chart = build_price_chart(
        "NVDA",
        [
            {"date": "2026-04-24", "close": float("nan"), "volume": 1000},
            {"date": "2026-04-25", "close": 101.0, "volume": float("nan")},
            {"date": "2026-04-26", "close": float("inf"), "volume": 900},
        ],
    )

    assert chart["available"] is True
    assert chart["points"] == [{"date": "2026-04-25", "open": 101.0, "high": 101.0, "low": 101.0, "close": 101.0}]
    assert "nan" not in chart["path"].lower()
    assert "inf" not in chart["path"].lower()


@pytest.mark.unit
def test_price_chart_handles_missing_or_flat_data():
    from tradingagents.dashboard.charts import build_price_chart

    empty = build_price_chart("NVDA", [])
    assert empty["available"] is False
    assert empty["reason"] == "no_price_data"

    flat = build_price_chart("NVDA", [{"date": "2026-04-24", "close": 10.0}, {"date": "2026-04-25", "close": 10.0}])
    assert flat["available"] is True
    assert flat["change_percent"] == 0.0
    assert "L" in flat["path"]


@pytest.mark.unit
def test_dashboard_detail_renders_premium_stock_workspace_with_chart(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    def fake_get_price_chart(ticker, trade_date, lookback_days=180):
        return {
            "available": True,
            "ticker": ticker,
            "currency": "KRW",
            "points": [
                {"date": "2026-04-24", "close": 330000.9, "volume": 1000},
                {"date": "2026-04-25", "close": 333000.7, "volume": 1200},
            ],
            "path": "M 0 100 L 720 20",
            "area_path": "M 0 100 L 720 20 L 720 220 L 0 220 Z",
            "latest_close": 333000.7,
            "first_close": 330000.9,
            "change": 2999.8,
            "change_percent": 0.91,
            "min_close": 330000.9,
            "max_close": 333000.7,
            "start_date": "2026-04-24",
            "end_date": "2026-04-25",
        }

    monkeypatch.setattr(dashboard_app, "get_price_chart", fake_get_price_chart)
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "stock-workspace" in response.text
    assert "NVDA" in response.text
    assert "market-chart" in response.text
    assert "lightweight-charts" in response.text
    assert "MA5" in response.text
    assert "MA20" in response.text
    assert "MA60" in response.text
    assert "캔들" in response.text
    assert "데이터 소스" in response.text
    assert "가격 흐름" in response.text
    assert "핵심 판단" in response.text
    assert "핵심 근거" in response.text
    assert "상세 근거" in response.text
    assert "에이전트별 분석" in response.text
    assert "시장 분석" in response.text
    assert "퀀트 전략 분석" in response.text
    assert "Suggested entry 330,000-333,000" in response.text
    assert "Bull case: growth and margins remain elite." in response.text
    assert "Bear case: valuation leaves little room for disappointment." in response.text
    assert "Aggressive analyst: add on recovery." in response.text
    assert "Conservative analyst: do not chase valuation." in response.text
    assert "Neutral analyst: maintain current weight." in response.text
    assert "상세보기" in response.text
    assert '<details class="agent-detail">' in response.text
    assert '<details open' not in response.text
    assert "<p>**Recommendation**" not in response.text
    assert "<p>**Action**" not in response.text
    assert "FINAL TRANSACTION PROPOSAL" in response.text
    assert "333,000" in response.text
    assert "330,000" in response.text
    assert "+2,999" in response.text
    assert "333000.70" not in response.text
    assert "+0.91%" in response.text
    assert "M 0 100 L 720 20" in response.text
    assert "전문 트레이딩 차트" not in response.text
    assert "Professional Market Context" not in response.text
    assert "Lightweight Charts · Apache-2.0" not in response.text
    assert "원문을 보존한 상태에서 읽기 좋게 재구성" not in response.text
    assert "각 섹션은 요약된 핵심 bullet" not in response.text
    assert "필요한 섹션만 빠르게 확인" not in response.text
    assert not re.search(r">\d+자<", response.text)


@pytest.mark.unit
def test_dashboard_detail_renders_trade_plan_signal_and_reanalysis_cards(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    def fake_get_price_chart(ticker, trade_date, lookback_days=180):
        return {
            "available": True,
            "ticker": ticker,
            "currency": "KRW",
            "points": [
                {"date": "2026-05-02", "open": 335000.0, "high": 337000.0, "low": 331000.0, "close": 335000.0, "volume": 1000},
                {"date": "2026-05-04", "open": 334000.0, "high": 334500.0, "low": 332000.0, "close": 333000.0, "volume": 1200},
            ],
            "moving_averages": {
                "ma20": [
                    {"time": "2026-05-02", "value": 334000.0},
                    {"time": "2026-05-04", "value": 334000.0},
                ]
            },
            "path": "M 0 100 L 720 20",
            "area_path": "M 0 100 L 720 20 L 720 220 L 0 220 Z",
            "latest_close": 333000.0,
            "first_close": 335000.0,
            "change": -2000.0,
            "change_percent": -0.6,
            "min_close": 333000.0,
            "max_close": 335000.0,
            "start_date": "2026-05-02",
            "end_date": "2026-05-04",
        }

    monkeypatch.setattr(dashboard_app, "get_price_chart", fake_get_price_chart)
    record = dict(
        sample_record,
        ticker="005930.KS",
        strategy_spec={
            "strategy_id": "005930.KS-2026-05-01-price-timing",
            "ticker": "005930.KS",
            "trade_date": "2026-05-01",
            "strategy_type": "price_timing_long",
            "execution_mode": "programmatic_rule_engine",
            "entry": {"type": "price_zone", "low": 329000, "high": 334000},
            "take_profit": {"type": "fixed_price", "price": 360000},
            "stop_loss": {"type": "fixed_price", "price": 318000},
            "currency": "KRW",
            "source": "quant_strategy_report",
            "valid_until": "2026-05-03",
            "reanalysis_triggers": [
                {"type": "moving_average_cross", "ma": "ma20", "direction": "down", "reason": "20일선 하향 이탈"}
            ],
        },
    )
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "매매 계획" in response.text
    assert "현재 시그널" in response.text
    assert "매수" in response.text
    assert "진입 구간" in response.text
    assert "329,000 ~ 334,000" in response.text
    assert "익절" in response.text
    assert "360,000" in response.text
    assert "손절" in response.text
    assert "318,000" in response.text
    assert "재분석 필요" in response.text
    assert "20일선 하향 이탈" in response.text
    assert "전략 유효기간 만료" in response.text
    assert "Agent 재분석 후보" in response.text
    assert "규칙 기반 자동 실행" in response.text
    assert "moving_average_cross" not in response.text
    assert "time_expired" not in response.text
    assert "programmatic_rule_engine" not in response.text


@pytest.mark.unit
def test_dashboard_detail_shows_readable_company_name_for_korean_ticker(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, lookback_days=180: {"available": False, "reason": "no_price_data"},
    )
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(dict(sample_record, ticker="005930.KS"))
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "<h1>삼성전자</h1>" in response.text
    assert "005930.KS" in response.text


@pytest.mark.unit
def test_dashboard_detail_uses_metadata_company_name_before_ticker(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, lookback_days=180: {"available": False, "reason": "no_price_data"},
    )
    repository = AnalysisRepository(tmp_path)
    record = dict(sample_record, ticker="005930.KS", metadata={"company_name": "삼성전자"})
    stored = repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "<h1>삼성전자</h1>" in response.text
    assert "005930.KS" in response.text


@pytest.mark.unit
def test_dashboard_detail_tolerates_malformed_name_sources(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard.app import _display_company_name

    assert _display_company_name({"ticker": "UNKNOWN", "metadata": "bad", "raw_state": ["bad"]}) == "UNKNOWN"


@pytest.mark.unit
def test_dashboard_detail_ignores_ticker_code_metadata_name_for_display():
    from tradingagents.dashboard.app import _display_company_name

    assert _display_company_name({"ticker": "005930.KS", "metadata": {"name": "005930"}}) == "삼성전자"


@pytest.mark.unit
def test_dashboard_chart_api_returns_chart_payload(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, lookback_days=180: {"available": True, "ticker": ticker, "points": [], "path": "M 0 0"},
    )
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(sample_record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/api/runs/{stored['run_id']}/chart")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["ticker"] == "NVDA"
