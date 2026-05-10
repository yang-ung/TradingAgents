from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from tradingagents.strategies.etf_allocation import (
    build_adaptive_core_v2_spec,
    build_static_weight_spec,
    run_etf_allocation_backtest,
)


def _load_backtest_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_etf_allocation_backtest.py"
    spec = importlib.util.spec_from_file_location("run_etf_allocation_backtest", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _prices() -> pd.DataFrame:
    dates = pd.to_datetime(
        [
            "2025-08-01",
            "2025-09-01",
            "2025-10-01",
            "2025-11-03",
            "2025-12-01",
            "2026-01-02",
            "2026-02-02",
            "2026-03-02",
            "2026-03-16",
            "2026-04-01",
            "2026-05-01",
        ]
    )
    return pd.DataFrame(
        {
            "SPY": [100.0, 105.0, 110.0, 115.0, 120.0, 118.0, 116.0, 100.0, 92.0, 90.0, 110.0],
            "QQQ": [100.0, 108.0, 116.0, 124.0, 132.0, 125.0, 118.0, 92.0, 84.0, 85.0, 120.0],
            "BIL": [100.0, 100.2, 100.4, 100.6, 100.8, 101.0, 101.2, 101.4, 101.5, 101.6, 101.8],
        },
        index=dates,
    )


def test_static_etf_allocation_backtest_uses_monthly_contributions_and_weights():
    spec = build_static_weight_spec(
        strategy_id="dca_70_30",
        weights={"SPY": 0.7, "QQQ": 0.3, "BIL": 0.0},
    )

    result = run_etf_allocation_backtest(spec, _prices(), monthly_contribution=1_000_000)

    assert result["strategy_id"] == "dca_70_30"
    assert result["invested"] == 10_000_000
    assert result["final_value"] > result["invested"]
    assert result["contribution_count"] == 10
    assert result["events"][0]["event"] == "rebalance"
    assert result["events"][0]["target_weights"] == {"SPY": 0.7, "QQQ": 0.3, "BIL": 0.0}
    assert result["max_drawdown_percent"] < 0


def test_adaptive_core_v2_reduces_risk_after_two_month_risk_off_confirmation():
    spec = build_adaptive_core_v2_spec(
        strategy_id="adaptive_core_v2",
        risk_on_weights={"SPY": 0.7, "QQQ": 0.3, "BIL": 0.0},
        risk_off_weights={"SPY": 0.45, "QQQ": 0.15, "BIL": 0.40},
        confirmation_months=2,
        drawdown_trigger=-0.10,
    )

    result = run_etf_allocation_backtest(spec, _prices(), monthly_contribution=1_000_000)

    regimes = [event["regime"] for event in result["events"]]
    assert "risk_off_confirmed" in regimes
    risk_off_event = next(event for event in result["events"] if event["regime"] == "risk_off_confirmed")
    assert risk_off_event["target_weights"]["BIL"] == 0.4
    assert risk_off_event["target_weights"]["SPY"] > 0
    assert result["live_capital_allowed"] is False
    assert result["system_trading_engine"] == "etf_allocation_rule_engine"


def test_etf_strategy_candidates_include_leveraged_qld_and_soxl_variants():
    module = _load_backtest_script()
    specs = module.build_specs()
    ids = {spec.strategy_id for spec in specs}
    assets = {asset for spec in specs for asset in spec.assets}

    assert "simple_dca_70_qld_30_spy" in ids
    assert "satellite_soxl_momentum" in ids
    assert {"QLD", "SOXL"}.issubset(assets)
    assert module.ASSET_LABELS["QLD"] == "나스닥100 2배"
    assert module.ASSET_LABELS["SOXL"] == "반도체 3배"
    assert module.DISPLAY_NAMES["simple_dca_70_qld_30_spy"] == "QLD 70 / SPY 30"
    assert module.DISPLAY_NAMES["satellite_soxl_momentum"] == "SOXL 위성 공격형"


def test_etf_live_readiness_blocks_leveraged_single_sample_winner():
    from tradingagents.strategies.etf_allocation import assess_etf_live_readiness

    report = assess_etf_live_readiness(
        selected_result={
            "display_name": "SOXL 위성 공격형",
            "total_return_percent": 42.86,
            "max_drawdown_percent": -11.51,
            "last_weights": {"SPY": 0.45, "QQQ": 0.25, "SOXL": 0.15, "GLD": 0.05, "BIL": 0.10},
        },
        benchmark_result={"display_name": "단순 DCA 70/30", "total_return_percent": 12.77},
        evaluation_windows=[
            {"label": "최근 1년", "result": {"total_return_percent": 42.86, "max_drawdown_percent": -11.51}},
        ],
    )

    assert report["live_capital_allowed"] is False
    assert report["status"] == "not_ready"
    assert "장기 검증 부족" in report["failure_reasons"]
    assert "레버리지 ETF 포함" in report["failure_reasons"]
    assert "한국 계좌 비용/세금/환율 미반영" in report["failure_reasons"]
    assert "paper trading 검증 부족" in report["failure_reasons"]
    assert report["leveraged_weight_percent"] == 15.0
    assert report["benchmark_excess_return_percent"] == 30.09
    assert report["readiness_checks"]["paper_trading"]["passed"] is False


def test_etf_live_readiness_requires_cost_and_paper_evidence_before_limited_live_candidate():
    from tradingagents.strategies.etf_allocation import assess_etf_live_readiness

    windows = [
        {"label": "장기 10년+", "result": {"total_return_percent": 180.0, "max_drawdown_percent": -18.0}, "benchmark": {"total_return_percent": 120.0}},
        {"label": "2018 금리인상 조정", "result": {"total_return_percent": -4.0, "max_drawdown_percent": -8.0}, "benchmark": {"total_return_percent": -7.0}},
        {"label": "2020 코로나 급락/회복", "result": {"total_return_percent": 18.0, "max_drawdown_percent": -19.0}, "benchmark": {"total_return_percent": 12.0}},
        {"label": "2022 금리인상 하락장", "result": {"total_return_percent": -6.0, "max_drawdown_percent": -12.0}, "benchmark": {"total_return_percent": -10.0}},
        {"label": "최근 1년", "result": {"total_return_percent": 14.0, "max_drawdown_percent": -7.0}, "benchmark": {"total_return_percent": 10.0}},
    ]

    report = assess_etf_live_readiness(
        selected_result={
            "display_name": "Adaptive Core v2 균형형",
            "total_return_percent": 14.0,
            "max_drawdown_percent": -7.0,
            "last_weights": {"SPY": 0.50, "QQQ": 0.30, "SCHD": 0.10, "GLD": 0.05, "BIL": 0.05},
        },
        benchmark_result={"display_name": "단순 DCA 70/30", "total_return_percent": 10.0},
        evaluation_windows=windows,
        cost_model={
            "modeled": True,
            "market": "KR",
            "fx_modeled": True,
            "tax_modeled": True,
            "trading_cost_modeled": True,
            "tracking_error_modeled": True,
        },
        paper_trading={
            "observed_days": 120,
            "closed_signals": 8,
            "total_return_percent": 4.5,
            "benchmark_excess_return_percent": 1.2,
            "max_drawdown_percent": -4.0,
        },
    )

    assert report["status"] == "limited_live_candidate"
    assert report["status_label"] == "제한적 실거래 검토 후보"
    assert report["failure_reasons"] == []
    assert report["readiness_checks"]["cost_model"]["passed"] is True
    assert report["readiness_checks"]["paper_trading"]["passed"] is True
    assert report["live_capital_allowed"] is False
    assert "최종 사람 승인" in report["recommended_action"]
