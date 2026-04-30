from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.storage import AnalysisRepository


@pytest.mark.unit
def test_price_chart_builds_series_svg_path_and_stats():
    from tradingagents.dashboard.charts import build_price_chart

    rows = [
        {"date": "2026-04-24", "close": 100.0, "volume": 1000},
        {"date": "2026-04-25", "close": 103.0, "volume": 1200},
        {"date": "2026-04-26", "close": 101.0, "volume": 900},
        {"date": "2026-04-27", "close": 108.0, "volume": 1500},
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
    assert chart["points"] == [{"date": "2026-04-25", "close": 101.0}]
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
                {"date": "2026-04-24", "close": 100.0, "volume": 1000},
                {"date": "2026-04-25", "close": 108.0, "volume": 1200},
            ],
            "path": "M 0 100 L 720 20",
            "area_path": "M 0 100 L 720 20 L 720 220 L 0 220 Z",
            "latest_close": 108.0,
            "first_close": 100.0,
            "change": 8.0,
            "change_percent": 8.0,
            "min_close": 100.0,
            "max_close": 108.0,
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
    assert "가격 차트" in response.text
    assert "투자 판단" in response.text
    assert "108.00" in response.text
    assert "+8.00%" in response.text
    assert "M 0 100 L 720 20" in response.text


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
