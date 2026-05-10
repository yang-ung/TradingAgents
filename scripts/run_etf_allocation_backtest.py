from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yfinance as yf

from tradingagents.strategies import (
    assess_etf_live_readiness,
    build_adaptive_core_v2_spec,
    build_static_weight_spec,
    run_etf_allocation_backtest,
)

ASSET_LABELS = {
    "SPY": "S&P500",
    "QQQ": "나스닥100",
    "QLD": "나스닥100 2배",
    "SOXL": "반도체 3배",
    "SCHD": "배당/퀄리티",
    "IEF": "미국중기채",
    "GLD": "금",
    "BIL": "현금성/초단기채",
}

DISPLAY_NAMES = {
    "simple_dca_70_spy_30_qqq": "단순 DCA 70/30",
    "simple_dca_70_qld_30_spy": "QLD 70 / SPY 30",
    "satellite_soxl_momentum": "SOXL 위성 공격형",
    "static_balanced_dca": "상시분산 DCA",
    "adaptive_core_v2_balanced_confirmed": "Adaptive Core v2 균형형",
    "adaptive_core_v2_aggressive_confirmed": "Adaptive Core v2 공격형",
    "adaptive_core_v2_slow_confirmed": "Adaptive Core v2 느린확인형",
}

EVALUATION_WINDOWS = [
    ("장기 10년+", "2012-01-01", "2026-05-01"),
    ("2018 금리인상 조정", "2018-01-01", "2018-12-31"),
    ("2020 코로나 급락/회복", "2020-02-01", "2020-12-31"),
    ("2022 금리인상 하락장", "2022-01-01", "2022-12-31"),
    ("최근 1년", "2025-05-01", "2026-05-01"),
]


def build_specs():
    return [
        build_static_weight_spec(strategy_id="simple_dca_70_spy_30_qqq", weights={"SPY": 0.70, "QQQ": 0.30, "BIL": 0.0}),
        build_static_weight_spec(strategy_id="simple_dca_70_qld_30_spy", weights={"QLD": 0.70, "SPY": 0.30, "BIL": 0.0}),
        build_static_weight_spec(strategy_id="satellite_soxl_momentum", weights={"SPY": 0.45, "QQQ": 0.25, "SOXL": 0.15, "GLD": 0.05, "BIL": 0.10}),
        build_static_weight_spec(
            strategy_id="static_balanced_dca",
            weights={"SPY": 0.45, "QQQ": 0.25, "SCHD": 0.10, "IEF": 0.10, "GLD": 0.05, "BIL": 0.05},
        ),
        build_adaptive_core_v2_spec(
            strategy_id="adaptive_core_v2_balanced_confirmed",
            risk_on_weights={"SPY": 0.50, "QQQ": 0.30, "SCHD": 0.10, "IEF": 0.00, "GLD": 0.05, "BIL": 0.05},
            risk_off_weights={"SPY": 0.35, "QQQ": 0.10, "SCHD": 0.10, "IEF": 0.15, "GLD": 0.10, "BIL": 0.20},
            confirmation_months=2,
            drawdown_trigger=-0.10,
        ),
        build_adaptive_core_v2_spec(
            strategy_id="adaptive_core_v2_aggressive_confirmed",
            risk_on_weights={"SPY": 0.55, "QQQ": 0.35, "SCHD": 0.00, "IEF": 0.00, "GLD": 0.05, "BIL": 0.05},
            risk_off_weights={"SPY": 0.45, "QQQ": 0.15, "SCHD": 0.05, "IEF": 0.10, "GLD": 0.10, "BIL": 0.15},
            confirmation_months=2,
            drawdown_trigger=-0.10,
        ),
        build_adaptive_core_v2_spec(
            strategy_id="adaptive_core_v2_slow_confirmed",
            risk_on_weights={"SPY": 0.50, "QQQ": 0.30, "SCHD": 0.10, "IEF": 0.00, "GLD": 0.05, "BIL": 0.05},
            risk_off_weights={"SPY": 0.40, "QQQ": 0.10, "SCHD": 0.10, "IEF": 0.15, "GLD": 0.10, "BIL": 0.15},
            confirmation_months=3,
            drawdown_trigger=-0.10,
        ),
    ]


def _run_specs(prices: pd.DataFrame, specs: list) -> list[dict]:
    results = []
    for spec in specs:
        result = run_etf_allocation_backtest(spec, prices[list(spec.assets)], monthly_contribution=1_000_000)
        result["display_name"] = DISPLAY_NAMES[spec.strategy_id]
        result["last_weights"] = result["events"][-1]["target_weights"]
        result["risk_off_count"] = sum(1 for event in result["events"] if event.get("regime") == "risk_off_confirmed")
        result["rebalance_count"] = len(result["events"])
        result["equity_curve"] = result["equity_curve"][-90:]
        results.append(result)
    return sorted(results, key=lambda item: item["final_value"], reverse=True)


def _asset_returns(prices: pd.DataFrame, symbols: list[str]) -> dict[str, float]:
    return {symbol: round((prices[symbol].iloc[-1] / prices[symbol].iloc[0] - 1) * 100, 4) for symbol in symbols}


def _evaluation_windows(prices: pd.DataFrame, specs: list, selected_strategy_id: str) -> list[dict]:
    windows = []
    for label, start, end in EVALUATION_WINDOWS:
        window_prices = prices.loc[start:end]
        if window_prices.empty:
            continue
        results = _run_specs(window_prices, specs)
        selected = next((item for item in results if item.get("strategy_id") == selected_strategy_id), None)
        benchmark = next((item for item in results if item.get("strategy_id") == "simple_dca_70_spy_30_qqq"), None)
        windows.append(
            {
                "label": label,
                "data_range": [window_prices.index[0].date().isoformat(), window_prices.index[-1].date().isoformat()],
                "result": selected,
                "benchmark": benchmark,
                "top_strategy": results[0] if results else None,
            }
        )
    return windows


def main():
    specs = build_specs()
    symbols = sorted({asset for spec in specs for asset in spec.assets})
    prices = yf.download(symbols, start="2012-01-01", end="2026-05-02", auto_adjust=True, progress=False, threads=False)["Close"]
    prices = prices[symbols].ffill().dropna()
    period = prices.loc["2025-05-01":"2026-05-01"]
    results = _run_specs(period, specs)
    benchmark = next((item for item in results if item.get("strategy_id") == "simple_dca_70_spy_30_qqq"), {})
    selected = results[0] if results else {}
    evaluation_windows = _evaluation_windows(prices, specs, str(selected.get("strategy_id") or ""))
    output = {
        "data_range": [period.index[0].date().isoformat(), period.index[-1].date().isoformat()],
        "long_term_data_range": [prices.index[0].date().isoformat(), prices.index[-1].date().isoformat()],
        "asset_returns": _asset_returns(period, symbols),
        "long_term_asset_returns": _asset_returns(prices, symbols),
        "results": results,
        "evaluation_windows": evaluation_windows,
        "live_readiness": assess_etf_live_readiness(
            selected_result=selected,
            benchmark_result=benchmark,
            evaluation_windows=evaluation_windows,
            max_allowed_leveraged_weight=0.0,
        ),
    }
    out_dir = Path("artifacts/etf-strategy-reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "adaptive-core-v2-1y.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
