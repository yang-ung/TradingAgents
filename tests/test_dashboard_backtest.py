from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.storage import AnalysisRepository


@pytest.mark.unit
def test_backtest_simulates_entry_take_profit_stop_and_benchmark():
    from tradingagents.dashboard.backtest import build_strategy_backtest

    chart = {
        "available": True,
        "currency": "KRW",
        "points": [
            {"date": "2026-01-01", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"date": "2026-01-02", "open": 101.0, "high": 103.0, "low": 98.0, "close": 102.0},
            {"date": "2026-01-03", "open": 102.0, "high": 112.0, "low": 101.0, "close": 110.0},
        ],
    }
    record = {
        "reports": {
            "quant_strategy_report": "진입 99-101 KRW, 익절 110 KRW, 손절 94 KRW",
            "trader_investment_decision": "",
            "final_trade_decision": "",
        }
    }

    backtest = build_strategy_backtest(record, chart)

    assert backtest["available"] is True
    assert backtest["levels"] == {"entry_low": 99.0, "entry_high": 101.0, "take_profit": 110.0, "stop_loss": 94.0, "currency": "KRW"}
    assert backtest["periods"]["전체"]["strategy_return_percent"] == 8.91
    assert backtest["periods"]["전체"]["benchmark_return_percent"] == 10.0
    assert backtest["periods"]["전체"]["trades"][0]["exit_reason"] == "take_profit"
    assert backtest["summary"]["best_period"] == "전체"


@pytest.mark.unit
def test_backtest_uses_conservative_same_day_stop_after_entry():
    from tradingagents.dashboard.backtest import build_strategy_backtest

    chart = {
        "available": True,
        "currency": "KRW",
        "points": [
            {"date": "2026-01-01", "open": 100.0, "high": 102.0, "low": 94.0, "close": 99.0},
            {"date": "2026-01-02", "open": 99.0, "high": 111.0, "low": 98.0, "close": 110.0},
        ],
    }
    record = {"reports": {"quant_strategy_report": "진입 99-101 KRW, 익절 110 KRW, 손절 95 KRW"}}

    backtest = build_strategy_backtest(record, chart)

    assert backtest["periods"]["전체"]["trades"][0]["return_percent"] == -5.94
    assert backtest["periods"]["전체"]["trades"][0]["exit_reason"] == "stop_loss"
    assert backtest["periods"]["전체"]["trades"][0]["exit_date"] == "2026-01-01"


@pytest.mark.unit
def test_backtest_rejects_prose_levels_inside_entry_zone():
    from tradingagents.dashboard.backtest import extract_price_timing_levels

    chart = {"currency": "KRW"}
    assert extract_price_timing_levels({"reports": {"quant_strategy_report": "진입 100-110 KRW, 익절 105 KRW, 손절 90 KRW"}}, chart) is None
    assert extract_price_timing_levels({"reports": {"quant_strategy_report": "진입 100-110 KRW, 익절 120 KRW, 손절 105 KRW"}}, chart) is None


@pytest.mark.unit
def test_backtest_uses_distinct_windows_for_each_period():
    from datetime import date, timedelta

    from tradingagents.dashboard.backtest import build_strategy_backtest

    start = date(2026, 1, 1)
    points = []
    for index in range(200):
        price = 80.0 + index * 0.1
        if index == 10:
            low, high, close = 99.0, 101.0, 100.0
        elif index == 20:
            low, high, close = 100.0, 111.0, 110.0
        elif index == 120:
            low, high, close = 99.0, 101.0, 100.0
        elif index == 130:
            low, high, close = 94.0, 101.0, 95.0
        elif index == 190:
            low, high, close = 99.0, 101.0, 100.0
        elif index == 195:
            low, high, close = 100.0, 112.0, 110.0
        else:
            low, high, close = price - 0.5, price + 0.5, price
        points.append({"date": (start + timedelta(days=index)).isoformat(), "open": price, "high": high, "low": low, "close": close})
    chart = {"available": True, "currency": "KRW", "points": points}
    record = {"reports": {"quant_strategy_report": "진입 99-101 KRW, 익절 110 KRW, 손절 95 KRW"}}

    backtest = build_strategy_backtest(record, chart)

    assert backtest["periods"]["1개월"]["start_date"] != backtest["periods"]["전체"]["start_date"]
    returns = {label: period["strategy_return_percent"] for label, period in backtest["periods"].items()}
    assert len(set(returns.values())) > 1


@pytest.mark.unit
def test_backtest_hides_periods_without_enough_history():
    from tradingagents.dashboard.backtest import build_strategy_backtest

    chart = {
        "available": True,
        "currency": "KRW",
        "points": [
            {"date": "2026-01-01", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"date": "2026-01-02", "open": 100.0, "high": 104.0, "low": 99.0, "close": 103.0},
            {"date": "2026-01-03", "open": 103.0, "high": 111.0, "low": 102.0, "close": 110.0},
            {"date": "2026-01-04", "open": 110.0, "high": 112.0, "low": 106.0, "close": 108.0},
        ],
    }
    record = {"reports": {"quant_strategy_report": "진입 99-101 KRW, 익절 110 KRW, 손절 95 KRW"}}

    backtest = build_strategy_backtest(record, chart)

    assert list(backtest["periods"].keys()) == ["전체"]


@pytest.mark.unit
def test_strategy_execution_replay_stops_after_stop_loss_and_shows_100m_pnl():
    from tradingagents.dashboard.backtest import build_strategy_execution_replay

    chart = {
        "available": True,
        "currency": "KRW",
        "points": [
            {"date": "2026-03-03", "open": 200000.0, "high": 201000.0, "low": 196000.0, "close": 197000.0},
            {"date": "2026-03-04", "open": 190000.0, "high": 191000.0, "low": 188000.0, "close": 189000.0},
            {"date": "2026-03-05", "open": 196000.0, "high": 197000.0, "low": 196000.0, "close": 196500.0},
            {"date": "2026-03-06", "open": 188000.0, "high": 189000.0, "low": 188000.0, "close": 188500.0},
        ],
    }
    record = {
        "strategy_spec": {
            "strategy_id": "005930.KS-2026-03-01-price-timing",
            "ticker": "005930.KS",
            "trade_date": "2026-03-01",
            "entry": {"type": "price_zone", "low": 196000.0, "high": 196000.0},
            "take_profit": {"type": "fixed_price", "price": 222500.0},
            "stop_loss": {"type": "fixed_price", "price": 188000.0},
            "currency": "KRW",
            "reanalysis_triggers": [{"type": "price_below", "level": 188000.0, "reason": "손절가 이탈"}],
        }
    }

    replay = build_strategy_execution_replay(record, chart, initial_capital=100_000_000)

    assert replay["available"] is True
    assert replay["initial_capital"] == 100_000_000
    assert replay["final_equity"] == 95_920_000
    assert replay["pnl"] == -4_080_000
    assert replay["return_percent"] == -4.08
    assert replay["reanalysis_required"] is True
    assert replay["stopped_after_reanalysis"] is True
    assert [event["type"] for event in replay["events"]] == ["buy", "sell", "reanalysis_required"]
    assert replay["events"][0]["date"] == "2026-03-03"
    assert replay["events"][1]["date"] == "2026-03-04"
    assert replay["events"][1]["reason"] == "stop_loss"
    assert replay["events"][2]["label"] == "재분석 필요"


@pytest.mark.unit
def test_backtest_marks_unavailable_without_valid_price_timing_levels():
    from tradingagents.dashboard.backtest import build_strategy_backtest

    backtest = build_strategy_backtest({"reports": {"quant_strategy_report": "관망"}}, {"available": True, "points": [{"date": "2026-01-01", "close": 100.0}]})

    assert backtest["available"] is False
    assert backtest["reason"] == "price_timing_levels_unavailable"


@pytest.mark.unit
def test_dashboard_detail_renders_backtest_panel_and_api(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    def fake_get_price_chart(ticker, trade_date, lookback_days=180):
        return {
            "available": True,
            "ticker": ticker,
            "currency": "KRW",
            "points": [
                {"date": "2026-01-01", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
                {"date": "2026-01-02", "open": 101.0, "high": 103.0, "low": 98.0, "close": 102.0},
                {"date": "2026-01-03", "open": 102.0, "high": 112.0, "low": 101.0, "close": 110.0},
            ],
            "latest_close": 110.0,
            "first_close": 100.0,
            "change": 10.0,
            "change_percent": 10.0,
            "min_close": 100.0,
            "max_close": 110.0,
            "start_date": "2026-01-01",
            "end_date": "2026-01-03",
            "path": "M 0 0 L 720 20",
            "area_path": "M 0 0 L 720 20 L 720 220 L 0 220 Z",
        }

    monkeypatch.setattr(dashboard_app, "get_price_chart", fake_get_price_chart)
    record = dict(sample_record)
    reports = dict(record["reports"])
    reports["quant_strategy_report"] = "진입 99-101 KRW, 익절 110 KRW, 손절 94 KRW"
    record["reports"] = reports
    record.pop("strategy_spec", None)
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "백테스트 시뮬레이션" in response.text
    assert "전략 수익률" in response.text
    assert "벤치마크" in response.text
    assert "8.91%" in response.text
    assert "익절 도달" in response.text
    assert "사전 검증/out-of-sample 성과가 아니" in response.text

    api_response = client.get(f"/api/runs/{stored['run_id']}/backtest")
    assert api_response.status_code == 200
    assert api_response.json()["available"] is True
    assert api_response.json()["periods"]["전체"]["strategy_return_percent"] == 8.91


@pytest.mark.unit
def test_dashboard_detail_renders_pre_live_validation_and_api(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    def fake_get_price_chart(ticker, trade_date, lookback_days=180):
        return {
            "available": True,
            "ticker": ticker,
            "currency": "KRW",
            "points": [
                {"date": "2026-01-01", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
                {"date": "2026-01-02", "open": 101.0, "high": 103.0, "low": 98.0, "close": 102.0},
                {"date": "2026-01-03", "open": 102.0, "high": 112.0, "low": 101.0, "close": 110.0},
            ],
            "latest_close": 110.0,
            "first_close": 100.0,
            "change": 10.0,
            "change_percent": 10.0,
            "min_close": 100.0,
            "max_close": 110.0,
            "start_date": "2026-01-01",
            "end_date": "2026-01-03",
            "path": "M 0 0 L 720 20",
            "area_path": "M 0 0 L 720 20 L 720 220 L 0 220 Z",
        }

    monkeypatch.setattr(dashboard_app, "get_price_chart", fake_get_price_chart)
    record = dict(sample_record)
    reports = dict(record["reports"])
    reports["quant_strategy_report"] = "진입 99-101 KRW, 익절 110 KRW, 손절 94 KRW"
    record["reports"] = reports
    record.pop("strategy_spec", None)
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "실전 투입 전 검증" in response.text
    assert "검증 실패" in response.text
    assert "실전 투입 보류" in response.text
    assert "표본 부족" in response.text
    assert "미래 수익을 보장하지 않습니다" in response.text

    api_response = client.get(f"/api/runs/{stored['run_id']}/pre-live-validation")
    assert api_response.status_code == 200
    payload = api_response.json()
    assert payload["available"] is True
    assert payload["status_label"] == "검증 실패"
    assert payload["live_capital_allowed"] is False
    assert "표본 부족" in payload["failure_reasons"]


@pytest.mark.unit
def test_dashboard_batch_pre_live_validation_api_summarizes_latest_runs(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    def fake_get_price_chart(ticker, trade_date, lookback_days=180):
        points = [
            {"date": "2026-01-01", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"date": "2026-01-02", "open": 101.0, "high": 103.0, "low": 98.0, "close": 102.0},
            {"date": "2026-01-03", "open": 102.0, "high": 112.0, "low": 101.0, "close": 110.0},
        ]
        return {"available": True, "ticker": ticker, "currency": "KRW", "points": points}

    monkeypatch.setattr(dashboard_app, "get_price_chart", fake_get_price_chart)
    repository = AnalysisRepository(tmp_path)
    for ticker in ("AAA.KS", "BBB.KS"):
        record = dict(sample_record)
        record["run_id"] = f"{ticker.lower()}-2026-01-01"
        record["ticker"] = ticker
        record["trade_date"] = "2026-01-01"
        record["generated_at"] = f"2026-01-01T00:00:0{1 if ticker == 'AAA.KS' else 2}+00:00"
        reports = dict(record["reports"])
        reports["quant_strategy_report"] = "진입 99-101 KRW, 익절 110 KRW, 손절 94 KRW"
        record["reports"] = reports
        record.pop("strategy_spec", None)
        repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get("/api/pre-live-validation?market=KR&latest_only=true&limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["live_capital_allowed_count"] == 0
    assert payload["failed_count"] == 2
    assert payload["paper_trading_candidate_count"] == 0
    assert {item["ticker"] for item in payload["failures"]} == {"AAA.KS", "BBB.KS"}
    assert all("표본 부족" in item["failure_reasons"] for item in payload["failures"])


@pytest.mark.unit
def test_dashboard_detail_renders_strategy_execution_replay_and_api(tmp_path, sample_record, monkeypatch):
    from tradingagents.dashboard import app as dashboard_app

    def fake_get_price_chart(ticker, trade_date, lookback_days=180):
        return {
            "available": True,
            "ticker": ticker,
            "currency": "KRW",
            "points": [
                {"date": "2026-03-03", "open": 200000.0, "high": 201000.0, "low": 196000.0, "close": 197000.0},
                {"date": "2026-03-04", "open": 190000.0, "high": 191000.0, "low": 188000.0, "close": 189000.0},
                {"date": "2026-03-05", "open": 196000.0, "high": 197000.0, "low": 196000.0, "close": 196500.0},
            ],
            "latest_close": 196500.0,
            "first_close": 197000.0,
            "change": -500.0,
            "change_percent": -0.25,
            "min_close": 189000.0,
            "max_close": 197000.0,
            "start_date": "2026-03-03",
            "end_date": "2026-03-05",
            "path": "M 0 0 L 720 20",
            "area_path": "M 0 0 L 720 20 L 720 220 L 0 220 Z",
        }

    monkeypatch.setattr(dashboard_app, "get_price_chart", fake_get_price_chart)
    record = dict(sample_record)
    record["ticker"] = "005930.KS"
    record["trade_date"] = "2026-03-01"
    record["strategy_spec"] = {
        "strategy_id": "005930.KS-2026-03-01-price-timing",
        "ticker": "005930.KS",
        "trade_date": "2026-03-01",
        "entry": {"type": "price_zone", "low": 196000.0, "high": 196000.0},
        "take_profit": {"type": "fixed_price", "price": 222500.0},
        "stop_loss": {"type": "fixed_price", "price": 188000.0},
        "currency": "KRW",
        "reanalysis_triggers": [{"type": "price_below", "level": 188000.0, "reason": "손절가 이탈"}],
    }
    repository = AnalysisRepository(tmp_path)
    stored = repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get(f"/runs/{stored['run_id']}")

    assert response.status_code == 200
    assert "전략 실행 리플레이" in response.text
    assert "1억원 기준 손익" in response.text
    assert "-4,080,000" in response.text
    assert "2026-03-03" in response.text
    assert "매수" in response.text
    assert "2026-03-04" in response.text
    assert "손절" in response.text
    assert "재분석 전 자동 재진입 차단" in response.text

    api_response = client.get(f"/api/runs/{stored['run_id']}/execution-replay")
    assert api_response.status_code == 200
    assert api_response.json()["pnl"] == -4_080_000
    assert api_response.json()["events"][2]["type"] == "reanalysis_required"
