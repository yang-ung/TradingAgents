from __future__ import annotations

import pytest


@pytest.mark.unit
def test_strategy_spec_validates_long_price_timing_and_generates_signal():
    from tradingagents.strategies import StrategySpec, evaluate_signal

    spec = StrategySpec.from_price_timing_levels(
        ticker="005930.KS",
        trade_date="2026-04-30",
        levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
        source="quant_strategy_report",
    )

    assert spec.strategy_type == "price_timing_long"
    assert spec.execution_mode == "programmatic_rule_engine"
    assert spec.entry.low == 99
    assert spec.entry.high == 101

    assert evaluate_signal(spec, {"date": "2026-05-01", "close": 100})["action"] == "BUY"
    assert evaluate_signal(spec, {"date": "2026-05-02", "close": 111}, position_open=True)["action"] == "SELL_TAKE_PROFIT"
    assert evaluate_signal(spec, {"date": "2026-05-03", "close": 94}, position_open=True)["action"] == "SELL_STOP_LOSS"
    assert evaluate_signal(spec, {"date": "2026-05-04", "close": 104})["action"] == "WAIT"


@pytest.mark.unit
def test_strategy_spec_rejects_invalid_long_level_direction():
    from tradingagents.strategies import StrategySpec

    with pytest.raises(ValueError):
        StrategySpec.from_price_timing_levels(
            ticker="005930.KS",
            trade_date="2026-04-30",
            levels={"entry_low": 100, "entry_high": 101, "take_profit": 98, "stop_loss": 95, "currency": "KRW"},
        )

    with pytest.raises(ValueError):
        StrategySpec.from_price_timing_levels(
            ticker="005930.KS",
            trade_date="2026-04-30",
            levels={"entry_low": 100, "entry_high": 101, "take_profit": 110, "stop_loss": 102, "currency": "KRW"},
        )


@pytest.mark.unit
def test_strategy_spec_rejects_non_finite_price_levels():
    from tradingagents.strategies import StrategySpec

    for levels in [
        {"entry_low": float("nan"), "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
        {"entry_low": 99, "entry_high": float("inf"), "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
        {"entry_low": 99, "entry_high": 101, "take_profit": float("inf"), "stop_loss": 95, "currency": "KRW"},
        {"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": float("nan"), "currency": "KRW"},
    ]:
        with pytest.raises(ValueError):
            StrategySpec.from_price_timing_levels(ticker="005930.KS", trade_date="2026-04-30", levels=levels)


@pytest.mark.unit
def test_backtest_prefers_structured_strategy_spec_over_regex_report():
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
        "strategy_spec": {
            "strategy_id": "005930.KS-2026-04-30-price-timing",
            "ticker": "005930.KS",
            "trade_date": "2026-04-30",
            "strategy_type": "price_timing_long",
            "execution_mode": "programmatic_rule_engine",
            "entry": {"type": "price_zone", "low": 99, "high": 101},
            "take_profit": {"type": "fixed_price", "price": 110},
            "stop_loss": {"type": "fixed_price", "price": 95},
            "currency": "KRW",
            "source": "test",
        },
        "reports": {"quant_strategy_report": "진입 1-2 KRW, 익절 3 KRW, 손절 0.5 KRW"},
    }

    backtest = build_strategy_backtest(record, chart)

    assert backtest["available"] is True
    assert backtest["strategy"] == "price_timing_long"
    assert backtest["levels"]["entry_low"] == 99
    assert backtest["levels"]["take_profit"] == 110
    assert backtest["periods"]["전체"]["strategy_return_percent"] == 8.91


@pytest.mark.unit
def test_extracts_valid_strategyspec_json_from_quant_report():
    from tradingagents.strategies import extract_strategy_spec_from_text

    report = '''전략 요약
StrategySpec:
{
  "strategy_id": "005930.KS-2026-04-30-price-timing",
  "ticker": "005930.KS",
  "trade_date": "2026-04-30",
  "strategy_type": "price_timing_long",
  "execution_mode": "programmatic_rule_engine",
  "entry": {"type": "price_zone", "low": 99, "high": 101},
  "take_profit": {"type": "fixed_price", "price": 110},
  "stop_loss": {"type": "fixed_price", "price": 95},
  "currency": "KRW",
  "source": "quant_strategy_report"
}
'''

    spec = extract_strategy_spec_from_text(report)

    assert spec is not None
    assert spec.ticker == "005930.KS"
    assert spec.entry.low == 99


@pytest.mark.unit
def test_extracts_strategyspec_json_with_string_basis_and_irrelevant_trigger_direction():
    from tradingagents.strategies import extract_strategy_spec_from_text

    report = '''### StrategySpec JSON
```json
{
  "strategy_id": "005930.KS_trend_pullback_20260430",
  "ticker": "005930.KS",
  "trade_date": "2026-04-30",
  "strategy_type": "price_timing_long",
  "execution_mode": "programmatic_rule_engine",
  "entry": {"type": "price_zone", "low": 217000, "high": 222000},
  "take_profit": {"type": "fixed_price", "price": 234000},
  "stop_loss": {"type": "fixed_price", "price": 214000},
  "currency": "KRW",
  "basis": "close_10_ema_219266, vwma_217469",
  "avoid_conditions": "가격이 214000원 아래로 마감",
  "confidence": 0.74,
  "valid_until": "2026-05-07",
  "reanalysis_triggers": [
    {"type": "price_below", "level": 214000, "direction": "below_close", "reason": "손절가 이탈"}
  ]
}
```
'''

    spec = extract_strategy_spec_from_text(report)

    assert spec is not None
    assert spec.entry.low == 217000
    assert spec.take_profit.price == 234000
    assert spec.basis == ["close_10_ema_219266, vwma_217469"]
    assert spec.avoid_conditions == ["가격이 214000원 아래로 마감"]
    assert spec.reanalysis_triggers[0].type == "price_below"
    assert spec.reanalysis_triggers[0].direction is None


@pytest.mark.unit
def test_build_analysis_record_materializes_strategy_spec_from_quant_report():
    from tradingagents.dashboard.extract import build_analysis_record

    state = {
        "company_of_interest": "005930.KS",
        "trade_date": "2026-04-30",
        "market_report": "시장 분석",
        "quant_strategy_report": "진입 99-101 KRW, 익절 110 KRW, 손절 95 KRW",
        "sentiment_report": "심리",
        "news_report": "뉴스",
        "fundamentals_report": "펀더멘털",
        "investment_debate_state": {"bull_history": "", "bear_history": "", "judge_decision": ""},
        "trader_investment_decision": "**Action**: Buy",
        "risk_debate_state": {"aggressive_history": "", "conservative_history": "", "neutral_history": "", "judge_decision": "**Rating**: Buy"},
        "investment_plan": "**Recommendation**: Buy",
        "final_trade_decision": "**Rating**: Buy\n\n**Executive Summary**: 요약",
    }

    record = build_analysis_record(state, generated_at="2026-05-01T00:00:00+00:00")

    assert record["strategy_spec"]["execution_mode"] == "programmatic_rule_engine"
    assert record["strategy_spec"]["entry"] == {"type": "price_zone", "low": 99.0, "high": 101.0}
    assert record["strategy_spec"]["take_profit"]["price"] == 110.0


@pytest.mark.unit
def test_reanalysis_trigger_engine_detects_price_volume_and_expiry():
    from tradingagents.strategies import StrategySpec, evaluate_reanalysis_triggers

    spec = StrategySpec.from_price_timing_levels(
        ticker="005930.KS",
        trade_date="2026-04-30",
        levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
        source="quant_strategy_report",
        valid_until="2026-05-03",
        reanalysis_triggers=[
            {"type": "price_below", "level": 95, "reason": "손절선 이탈"},
            {"type": "volume_spike", "multiplier": 2.0, "lookback_days": 3, "reason": "비정상 거래량 급증"},
            {"type": "time_expired", "date": "2026-05-03", "reason": "전략 유효기간 만료"},
        ],
    )
    points = [
        {"date": "2026-05-01", "close": 100, "volume": 1000},
        {"date": "2026-05-02", "close": 99, "volume": 1100},
        {"date": "2026-05-03", "close": 94, "volume": 3500},
    ]

    result = evaluate_reanalysis_triggers(spec, points)

    assert result["reanalysis_required"] is True
    assert {item["type"] for item in result["triggers"]} == {"price_below", "volume_spike", "time_expired"}
    assert result["triggers"][0]["reason"]


@pytest.mark.unit
def test_reanalysis_trigger_engine_uses_valid_until_even_without_explicit_time_trigger():
    from tradingagents.strategies import StrategySpec, evaluate_reanalysis_triggers

    spec = StrategySpec.from_price_timing_levels(
        ticker="005930.KS",
        trade_date="2026-04-30",
        levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
        valid_until="2026-05-03",
        reanalysis_triggers=[],
    )

    result = evaluate_reanalysis_triggers(spec, [{"date": "2026-05-04", "close": 100, "volume": 1000}])

    assert result["reanalysis_required"] is True
    assert result["triggers"] == [{"type": "time_expired", "reason": "전략 유효기간 만료"}]


@pytest.mark.unit
def test_strategy_spec_rejects_invalid_trigger_dates():
    from tradingagents.strategies import StrategySpec

    with pytest.raises(ValueError):
        StrategySpec.from_price_timing_levels(
            ticker="005930.KS",
            trade_date="2026-04-30",
            levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
            valid_until="not-a-date",
        )

    with pytest.raises(ValueError):
        StrategySpec.from_price_timing_levels(
            ticker="005930.KS",
            trade_date="2026-04-30",
            levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
            valid_until="2026-05-03junk",
        )

    with pytest.raises(ValueError):
        StrategySpec.from_price_timing_levels(
            ticker="005930.KS",
            trade_date="2026-04-30",
            levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
            reanalysis_triggers=[{"type": "time_expired", "date": "not-a-date"}],
        )

    with pytest.raises(ValueError):
        StrategySpec.from_price_timing_levels(
            ticker="005930.KS",
            trade_date="2026-04-30",
            levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
            reanalysis_triggers=[{"type": "time_expired", "date": "2026-05-03Tbad"}],
        )


@pytest.mark.unit
def test_reanalysis_trigger_rejects_non_finite_price_levels():
    from tradingagents.strategies import StrategySpec

    for level in [float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            StrategySpec.from_price_timing_levels(
                ticker="005930.KS",
                trade_date="2026-04-30",
                levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
                reanalysis_triggers=[{"type": "price_below", "level": level}],
            )


@pytest.mark.unit
def test_reanalysis_trigger_rejects_non_finite_volume_multiplier():
    from tradingagents.strategies import StrategySpec

    for multiplier in [float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            StrategySpec.from_price_timing_levels(
                ticker="005930.KS",
                trade_date="2026-04-30",
                levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
                reanalysis_triggers=[{"type": "volume_spike", "multiplier": multiplier, "lookback_days": 20}],
            )


@pytest.mark.unit
def test_reanalysis_trigger_engine_detects_moving_average_cross():
    from tradingagents.strategies import StrategySpec, evaluate_reanalysis_triggers

    spec = StrategySpec.from_price_timing_levels(
        ticker="005930.KS",
        trade_date="2026-04-30",
        levels={"entry_low": 99, "entry_high": 101, "take_profit": 110, "stop_loss": 95, "currency": "KRW"},
        reanalysis_triggers=[{"type": "moving_average_cross", "ma": "sma20", "direction": "down", "reason": "20일선 하향 이탈"}],
    )
    points = [
        {"date": "2026-05-01", "close": 102, "sma20": 100},
        {"date": "2026-05-02", "close": 98, "sma20": 99},
    ]

    result = evaluate_reanalysis_triggers(spec, points)

    assert result["reanalysis_required"] is True
    assert result["triggers"] == [{"type": "moving_average_cross", "reason": "20일선 하향 이탈"}]


@pytest.mark.unit
def test_dashboard_reanalysis_check_api_runs_without_agent_call(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tradingagents.dashboard import app as dashboard_app
    from tradingagents.dashboard.app import create_dashboard_app
    from tradingagents.dashboard.storage import AnalysisRepository

    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, lookback_days=180: {
            "available": True,
            "currency": "KRW",
            "points": [
                {"date": "2026-05-01", "close": 100, "volume": 1000},
                {"date": "2026-05-02", "close": 94, "volume": 3000},
            ],
        },
    )
    repository = AnalysisRepository(tmp_path)
    record = {
        "run_id": "trigger-test",
        "ticker": "005930.KS",
        "trade_date": "2026-04-30",
        "generated_at": "2026-05-01T00:00:00+00:00",
        "rating": "Buy",
        "trader_action": "Buy",
        "research_recommendation": "Buy",
        "decision_summary": "요약",
        "investment_thesis": "",
        "price_target": None,
        "time_horizon": "",
        "snippets": {},
        "reports": {},
        "report_lengths": {},
        "raw_log_path": "",
        "structured_path": "",
        "metadata": {},
        "raw_state": {},
        "strategy_spec": {
            "strategy_id": "005930.KS-2026-04-30-price-timing",
            "ticker": "005930.KS",
            "trade_date": "2026-04-30",
            "strategy_type": "price_timing_long",
            "execution_mode": "programmatic_rule_engine",
            "entry": {"type": "price_zone", "low": 99, "high": 101},
            "take_profit": {"type": "fixed_price", "price": 110},
            "stop_loss": {"type": "fixed_price", "price": 95},
            "currency": "KRW",
            "source": "test",
            "reanalysis_triggers": [{"type": "price_below", "level": 95, "reason": "손절선 이탈"}],
        },
    }
    repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get("/api/runs/trigger-test/reanalysis-check")

    assert response.status_code == 200
    assert response.json()["reanalysis_required"] is True
    assert response.json()["triggers"] == [{"type": "price_below", "reason": "손절선 이탈"}]
    assert response.json()["execution_mode"] == "programmatic_rule_engine"


@pytest.mark.unit
def test_dashboard_reanalysis_check_api_merges_chart_moving_averages(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tradingagents.dashboard import app as dashboard_app
    from tradingagents.dashboard.app import create_dashboard_app
    from tradingagents.dashboard.storage import AnalysisRepository

    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, lookback_days=180: {
            "available": True,
            "currency": "KRW",
            "points": [
                {"date": "2026-05-01", "close": 102, "volume": 1000},
                {"date": "2026-05-02", "close": 98, "volume": 1000},
            ],
            "moving_averages": {
                "ma20": [
                    {"time": "2026-05-01", "value": 100},
                    {"time": "2026-05-02", "value": 99},
                ]
            },
        },
    )
    repository = AnalysisRepository(tmp_path)
    record = {
        "run_id": "trigger-ma-test",
        "ticker": "005930.KS",
        "trade_date": "2026-04-30",
        "generated_at": "2026-05-01T00:00:00+00:00",
        "rating": "Buy",
        "trader_action": "Buy",
        "research_recommendation": "Buy",
        "decision_summary": "요약",
        "investment_thesis": "",
        "price_target": None,
        "time_horizon": "",
        "snippets": {},
        "reports": {},
        "report_lengths": {},
        "raw_log_path": "",
        "structured_path": "",
        "metadata": {},
        "raw_state": {},
        "strategy_spec": {
            "strategy_id": "005930.KS-2026-04-30-price-timing",
            "ticker": "005930.KS",
            "trade_date": "2026-04-30",
            "strategy_type": "price_timing_long",
            "execution_mode": "programmatic_rule_engine",
            "entry": {"type": "price_zone", "low": 99, "high": 101},
            "take_profit": {"type": "fixed_price", "price": 110},
            "stop_loss": {"type": "fixed_price", "price": 95},
            "currency": "KRW",
            "source": "test",
            "reanalysis_triggers": [{"type": "moving_average_cross", "ma": "ma20", "direction": "down", "reason": "20일선 하향 이탈"}],
        },
    }
    repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get("/api/runs/trigger-ma-test/reanalysis-check")

    assert response.status_code == 200
    assert response.json()["triggers"] == [{"type": "moving_average_cross", "reason": "20일선 하향 이탈"}]


@pytest.mark.unit
def test_dashboard_signal_api_runs_strategy_spec_without_agent_call(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tradingagents.dashboard import app as dashboard_app
    from tradingagents.dashboard.app import create_dashboard_app
    from tradingagents.dashboard.storage import AnalysisRepository

    monkeypatch.setattr(
        dashboard_app,
        "get_price_chart",
        lambda ticker, trade_date, lookback_days=180: {
            "available": True,
            "currency": "KRW",
            "points": [{"date": "2026-05-01", "open": 99, "high": 102, "low": 98, "close": 100}],
        },
    )
    repository = AnalysisRepository(tmp_path)
    record = {
        "run_id": "signal-test",
        "ticker": "005930.KS",
        "trade_date": "2026-04-30",
        "generated_at": "2026-05-01T00:00:00+00:00",
        "rating": "Buy",
        "trader_action": "Buy",
        "research_recommendation": "Buy",
        "decision_summary": "요약",
        "investment_thesis": "",
        "price_target": None,
        "time_horizon": "",
        "snippets": {},
        "reports": {},
        "report_lengths": {},
        "raw_log_path": "",
        "structured_path": "",
        "metadata": {},
        "raw_state": {},
        "strategy_spec": {
            "strategy_id": "005930.KS-2026-04-30-price-timing",
            "ticker": "005930.KS",
            "trade_date": "2026-04-30",
            "strategy_type": "price_timing_long",
            "execution_mode": "programmatic_rule_engine",
            "entry": {"type": "price_zone", "low": 99, "high": 101},
            "take_profit": {"type": "fixed_price", "price": 110},
            "stop_loss": {"type": "fixed_price", "price": 95},
            "currency": "KRW",
            "source": "test",
        },
    }
    repository.save(record)
    client = TestClient(create_dashboard_app(tmp_path))

    response = client.get("/api/runs/signal-test/signal")

    assert response.status_code == 200
    assert response.json()["action"] == "BUY"
    assert response.json()["execution_mode"] == "programmatic_rule_engine"


@pytest.mark.unit
def test_market_data_cache_reuses_fetch_result(tmp_path):
    from tradingagents.data.cache import MarketDataCache

    calls = []

    def fetcher():
        calls.append("called")
        return {"points": [{"date": "2026-01-01", "close": 100}]}

    cache = MarketDataCache(tmp_path)

    first = cache.get_or_fetch("005930.KS", "2026-04-30", "price_1d", fetcher)
    second = cache.get_or_fetch("005930.KS", "2026-04-30", "price_1d", fetcher)

    assert first == second
    assert calls == ["called"]
    assert cache.path_for("005930.KS", "2026-04-30", "price_1d").exists()
