from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from tradingagents.automation.signal_engine import build_price_table, calculate_market_score
from tradingagents.strategies import build_adaptive_core_v2_spec, build_static_weight_spec, run_etf_allocation_backtest

SHORT_TERM_SYMBOLS = ["QQQ", "QLD", "SOXL", "GLD", "SCHD", "SPY"]
ETF_SYMBOLS = ["SPY", "QQQ", "SCHD", "IEF", "GLD", "BIL", "QLD", "SOXL"]
INITIAL_CAPITAL_KRW = 100_000_000
MONTHLY_CONTRIBUTION_KRW = 1_000_000


def _fmt_pct(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}%"


def _fmt_krw(value: float | int | None) -> str:
    if value is None:
        return "-"
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{int(round(float(value))):,}원"


def _download_ohlcv(symbol: str, period: str = "9mo") -> list[dict[str, Any]]:
    hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    candles: list[dict[str, Any]] = []
    for idx, row in hist.reset_index().iterrows():
        date_value = row.get("Date") or row.get("Datetime") or idx
        values = {
            "date": str(date_value)[:10],
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row.get("Volume", 0) or 0),
        }
        if all(math.isfinite(float(values[k])) for k in ("open", "high", "low", "close")):
            candles.append(values)
    return candles


def _max_drawdown(values: list[float]) -> float:
    peak = -1e18
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            mdd = min(mdd, (value / peak - 1.0) * 100.0)
    return round(mdd, 2)


def _short_term_backtest(symbol: str, candles: list[dict[str, Any]], market_state: dict[str, Any]) -> dict[str, Any]:
    eval_start = max(1, len(candles) - 100)
    cash = float(INITIAL_CAPITAL_KRW)
    shares = 0.0
    position = False
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    bh_start = candles[eval_start]["close"]
    bh_end = candles[-1]["close"]
    buy_hold_return = (bh_end / bh_start - 1.0) * 100.0 if bh_start else 0.0

    for i in range(eval_start, len(candles)):
        today = candles[i]
        if position:
            current_trade = trades[-1]
            stop = float(current_trade["stop_loss"])
            target = float(current_trade["take_profit_1"])
            exit_reason = None
            exit_price = None
            if today["low"] <= stop:
                exit_reason = "손절"
                exit_price = stop
            elif today["high"] >= target:
                exit_reason = "익절"
                exit_price = target
            if exit_reason and exit_price is not None:
                cash = shares * exit_price
                ret = (exit_price / entry_price - 1.0) * 100.0
                current_trade.update(
                    {
                        "exit_date": today["date"],
                        "exit_price": round(exit_price, 4),
                        "exit_reason": exit_reason,
                        "return_percent": round(ret, 2),
                        "pnl_krw": round(INITIAL_CAPITAL_KRW * ret / 100.0),
                    }
                )
                events.append({"date": today["date"], "type": exit_reason, "price": round(exit_price, 4), "reason": exit_reason})
                position = False
                shares = 0.0
                entry_price = 0.0

        if not position:
            table = build_price_table(market_state, {symbol: candles[: i + 1]})
            row = table["rows"][0]
            if row.get("action") == "매수 후보":
                entry_price = float(row["entry_high"])
                shares = cash / entry_price
                cash = 0.0
                position = True
                trade = {
                    "entry_date": today["date"],
                    "entry_price": round(entry_price, 4),
                    "entry_low": row["entry_low"],
                    "entry_high": row["entry_high"],
                    "stop_loss": row["stop_loss"],
                    "take_profit_1": row["take_profit_1"],
                    "pattern_score": row["pattern_score"],
                    "strategy": "단타 FVG retest",
                }
                trades.append(trade)
                events.append({"date": today["date"], "type": "매수", "price": round(entry_price, 4), "reason": "FVG 진입구간 터치 + 시장점수 통과"})
                stop = float(row["stop_loss"])
                target = float(row["take_profit_1"])
                if today["low"] <= stop:
                    exit_price = stop
                    ret = (exit_price / entry_price - 1.0) * 100.0
                    trade.update({"exit_date": today["date"], "exit_price": round(exit_price, 4), "exit_reason": "손절", "return_percent": round(ret, 2), "pnl_krw": round(INITIAL_CAPITAL_KRW * ret / 100.0)})
                    events.append({"date": today["date"], "type": "손절", "price": round(exit_price, 4), "reason": "진입일 손절가 터치: 일봉 순서 불명확하여 보수적 stop-first"})
                    cash = shares * exit_price
                    position = False
                    shares = 0.0
                    entry_price = 0.0
                elif today["high"] >= target:
                    exit_price = target
                    ret = (exit_price / entry_price - 1.0) * 100.0
                    trade.update({"exit_date": today["date"], "exit_price": round(exit_price, 4), "exit_reason": "익절", "return_percent": round(ret, 2), "pnl_krw": round(INITIAL_CAPITAL_KRW * ret / 100.0)})
                    events.append({"date": today["date"], "type": "익절", "price": round(exit_price, 4), "reason": "진입일 목표가 터치"})
                    cash = shares * exit_price
                    position = False
                    shares = 0.0
                    entry_price = 0.0

        value = cash + (shares * today["close"] if position else 0.0)
        equity_curve.append({"date": today["date"], "value": round(value, 2)})

    final_value = equity_curve[-1]["value"] if equity_curve else INITIAL_CAPITAL_KRW
    closed = [t for t in trades if "exit_date" in t]
    wins = [t for t in closed if float(t.get("return_percent", 0)) > 0]
    return {
        "ticker": symbol,
        "strategy_family": "단타/스윙 패턴",
        "strategy_name": "FVG retest v1",
        "period_start": candles[eval_start]["date"],
        "period_end": candles[-1]["date"],
        "strategy_return_percent": round((final_value / INITIAL_CAPITAL_KRW - 1.0) * 100.0, 2),
        "buy_hold_return_percent": round(buy_hold_return, 2),
        "excess_return_percent": round((final_value / INITIAL_CAPITAL_KRW - 1.0) * 100.0 - buy_hold_return, 2),
        "final_equity_krw": round(final_value),
        "pnl_krw": round(final_value - INITIAL_CAPITAL_KRW),
        "max_drawdown_percent": _max_drawdown([point["value"] for point in equity_curve]),
        "trade_count": len(trades),
        "closed_trade_count": len(closed),
        "win_rate_percent": round(len(wins) / len(closed) * 100.0, 2) if closed else 0.0,
        "events": events,
        "trades": trades,
        "candles": candles[eval_start:],
        "equity_curve": equity_curve,
        "live_capital_allowed": False,
    }


def _build_etf_specs():
    return [
        build_static_weight_spec(strategy_id="simple_dca_70_spy_30_qqq", weights={"SPY": 0.70, "QQQ": 0.30, "BIL": 0.0}),
        build_static_weight_spec(strategy_id="static_balanced_dca", weights={"SPY": 0.45, "QQQ": 0.25, "SCHD": 0.10, "IEF": 0.10, "GLD": 0.05, "BIL": 0.05}),
        build_adaptive_core_v2_spec(strategy_id="adaptive_core_v2_balanced_confirmed"),
        build_static_weight_spec(strategy_id="qld_satellite_20", weights={"SPY": 0.50, "QQQ": 0.20, "QLD": 0.20, "GLD": 0.05, "BIL": 0.05}),
    ]


ETF_NAMES = {
    "simple_dca_70_spy_30_qqq": "단순 DCA 70/30",
    "static_balanced_dca": "상시분산 DCA",
    "adaptive_core_v2_balanced_confirmed": "Adaptive Core v2 균형형",
    "qld_satellite_20": "QLD 20% 위성형",
}


def _download_close_frame(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    data = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False, threads=False)
    close = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]]
    if not isinstance(close, pd.DataFrame):
        close = close.to_frame(symbols[0])
    close = close.ffill().dropna(how="all")
    return close[symbols].dropna()


def _etf_backtests() -> dict[str, Any]:
    specs = _build_etf_specs()
    symbols = sorted({asset for spec in specs for asset in spec.assets})
    prices = _download_close_frame(symbols, "2012-01-01", "2026-05-05")
    windows = {
        "최근 100거래일": prices.tail(100),
        "최근 1년": prices.loc["2025-05-01":"2026-05-04"],
        "장기 10년+": prices.loc["2012-01-01":"2026-05-04"],
        "2022 금리인상 하락장": prices.loc["2022-01-01":"2022-12-31"],
    }
    output: dict[str, Any] = {}
    for label, frame in windows.items():
        rows = []
        for spec in specs:
            result = run_etf_allocation_backtest(spec, frame[list(spec.assets)], monthly_contribution=MONTHLY_CONTRIBUTION_KRW)
            result["display_name"] = ETF_NAMES[spec.strategy_id]
            result["last_weights"] = result["events"][-1]["target_weights"] if result.get("events") else {}
            result["rebalance_count"] = len(result.get("events", []))
            result["data_range"] = [frame.index[0].date().isoformat(), frame.index[-1].date().isoformat()]
            rows.append(result)
        rows.sort(key=lambda r: r["final_value"], reverse=True)
        benchmark = next(r for r in rows if r["strategy_id"] == "simple_dca_70_spy_30_qqq")
        for row in rows:
            row["benchmark_excess_return_percent"] = round(row["total_return_percent"] - benchmark["total_return_percent"], 4)
            row["benchmark_excess_profit"] = round(row["profit"] - benchmark["profit"], 2)
        output[label] = {"data_range": [frame.index[0].date().isoformat(), frame.index[-1].date().isoformat()], "rows": rows}
    return output


def _x_for(idx: int, total: int, width: int = 760, left: int = 48) -> float:
    return left + idx * (width / max(total - 1, 1))


def _svg_price_chart(result: dict[str, Any]) -> str:
    candles = result["candles"]
    events = result["events"]
    width, height = 860, 260
    left, top, plot_w, plot_h = 48, 20, 760, 190
    prices = [c["close"] for c in candles] + [float(e["price"]) for e in events if e.get("price")]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.08 or 1
    lo -= pad
    hi += pad

    def y(price: float) -> float:
        return top + (hi - price) / (hi - lo) * plot_h

    path = " ".join(f"{'M' if i == 0 else 'L'} {_x_for(i, len(candles), plot_w, left):.1f} {y(c['close']):.1f}" for i, c in enumerate(candles))
    date_to_idx = {c["date"]: i for i, c in enumerate(candles)}
    markers = []
    for event in events:
        idx = date_to_idx.get(event["date"])
        if idx is None:
            continue
        x = _x_for(idx, len(candles), plot_w, left)
        yy = y(float(event["price"]))
        color = "#22c55e" if event["type"] == "매수" else ("#60a5fa" if event["type"] == "익절" else "#ef4444")
        markers.append(f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="5" fill="{color}"/><text x="{x+7:.1f}" y="{yy-7:.1f}" fill="{color}" font-size="11">{event["type"]}</text>')
    return f'<svg viewBox="0 0 {width} {height}" class="chart"><rect width="{width}" height="{height}" rx="16" fill="#08111f"/><path d="{path}" fill="none" stroke="#94a3b8" stroke-width="2"/><line x1="{left}" x2="{left+plot_w}" y1="{top+plot_h}" y2="{top+plot_h}" stroke="#334155"/><text x="{left}" y="238" fill="#94a3b8" font-size="11">{candles[0]["date"]}</text><text x="{left+plot_w-72}" y="238" fill="#94a3b8" font-size="11">{candles[-1]["date"]}</text>{"".join(markers)}</svg>'


def _render_html(report: dict[str, Any]) -> str:
    short_rows = report["short_term"]["rows"]
    etf_100 = report["etf_allocation"]["최근 100거래일"]["rows"]
    generated = report["generated_at"]
    short_table = "".join(
        f"<tr><td>{r['ticker']}</td><td>{r['strategy_name']}</td><td>{_fmt_pct(r['strategy_return_percent'])}</td><td>{_fmt_pct(r['buy_hold_return_percent'])}</td><td>{_fmt_pct(r['excess_return_percent'])}</td><td>{_fmt_pct(r['max_drawdown_percent'])}</td><td>{r['trade_count']}</td><td>{_fmt_krw(r['pnl_krw'])}</td></tr>"
        for r in short_rows
    )
    etf_table = "".join(
        f"<tr><td>{r['display_name']}</td><td>{_fmt_pct(r['total_return_percent'])}</td><td>{_fmt_pct(r['benchmark_excess_return_percent'])}</td><td>{_fmt_pct(r['max_drawdown_percent'])}</td><td>{r['rebalance_count']}</td><td>{_fmt_krw(r['profit'])}</td></tr>"
        for r in etf_100
    )
    chart_sections = "".join(
        f"<section class='panel'><h3>{r['ticker']} 실제 차트 + 매수/매도 지점</h3><p>거래 이벤트 {len(r['events'])}개 · 데이터 {r['period_start']}~{r['period_end']}</p>{_svg_price_chart(r)}<details><summary>거래 로그 원본 보기</summary><pre>{json.dumps(r['events'], ensure_ascii=False, indent=2)}</pre></details></section>"
        for r in short_rows[:4]
    )
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>전략 증거 리포트</title><style>
    body{{margin:0;background:#020617;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.55}}.page{{max-width:1180px;margin:0 auto;padding:32px}}.hero,.panel{{background:#0f172a;border:1px solid #1e293b;border-radius:22px;padding:24px;margin:18px 0;box-shadow:0 20px 60px #0006}}h1,h2,h3{{margin:0 0 10px}}.muted{{color:#94a3b8}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}.card{{background:#111827;border:1px solid #263244;border-radius:16px;padding:16px}}table{{width:100%;border-collapse:collapse;margin-top:12px}}th,td{{padding:10px;border-bottom:1px solid #263244;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{color:#93c5fd}}.pos{{color:#22c55e}}.neg{{color:#f87171}}pre{{white-space:pre-wrap;background:#020617;border:1px solid #334155;border-radius:12px;padding:12px;overflow:auto}}.chart{{width:100%;height:auto;margin-top:12px}}code{{color:#bfdbfe}}
    </style></head><body><main class='page'><section class='hero'><p class='muted'>TradingAgents deterministic evidence report · {generated}</p><h1>전략별 실제 데이터 기반 백테스트 증거</h1><p>FVG는 <b>단타/스윙 패턴 전략 중 하나</b>로만 분리했고, 장기 보유용 ETF는 별도 <code>ETFAllocationSpec</code> 배분 엔진으로 DCA 벤치마크와 비교했다. 모든 수치는 yfinance adjusted OHLC/Close에서 계산했고, LLM 판단으로 숫자를 만들지 않았다.</p><div class='grid'><div class='card'><b>단타</b><br>FVG retest v1 · 실제 OHLC 매수/익절/손절 이벤트</div><div class='card'><b>장기 ETF</b><br>DCA / 상시분산 / Adaptive Core / QLD 위성 비교</div><div class='card'><b>안전</b><br>live_capital_allowed=false · 주문 연동 없음</div></div></section><section class='panel'><h2>1. 단타 패턴 전략 100거래일 결과</h2><table><thead><tr><th>종목</th><th>전략</th><th>전략수익률</th><th>단순보유</th><th>초과</th><th>MDD</th><th>거래수</th><th>1억원 손익</th></tr></thead><tbody>{short_table}</tbody></table></section><section class='panel'><h2>2. 장기 보유용 ETF 전략 최근 100거래일 비교</h2><p class='muted'>월 100만원 적립식 기준. 단순 DCA 70/30을 고정 벤치마크로 사용.</p><table><thead><tr><th>전략</th><th>납입금 대비 수익률</th><th>DCA 대비 초과</th><th>계좌 MDD</th><th>리밸런싱</th><th>손익</th></tr></thead><tbody>{etf_table}</tbody></table></section>{chart_sections}<section class='panel'><h2>3. 데이터 기반 증명 방식</h2><ul><li>가격 데이터: <code>yfinance</code> adjusted daily OHLC/Close</li><li>단타 이벤트: 각 일봉의 high/low가 entry/target/stop을 실제로 터치했는지 검사</li><li>동일 일봉에서 목표/손절 동시 가능 시 보수적으로 손절 우선</li><li>장기 ETF: <code>run_etf_allocation_backtest()</code> 결정론적 배분 엔진 사용</li><li>원본 JSON: <code>artifacts/dashboard-pilot-20260430/strategy_evidence_report.json</code></li></ul></section></main></body></html>"""


def main() -> None:
    market_state = calculate_market_score({"equity_trend": 10, "volatility": 5, "rates": 0, "fx": 0, "news_sentiment": 0, "macro_risk": 0})
    candles = {symbol: _download_ohlcv(symbol) for symbol in SHORT_TERM_SYMBOLS}
    short_rows = [_short_term_backtest(symbol, rows, market_state) for symbol, rows in candles.items()]
    short_rows.sort(key=lambda r: (r["strategy_return_percent"], r["excess_return_percent"]), reverse=True)
    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "data_source": "yfinance adjusted daily OHLC/Close",
        "assumptions": {
            "short_term_market_state_for_pattern_test": market_state,
            "short_term_fill_rule": "entry_high fill; daily stop-first when stop and target both touched",
            "etf_monthly_contribution_krw": MONTHLY_CONTRIBUTION_KRW,
            "fees_tax_slippage": "not included in this evidence slice",
            "live_capital_allowed": False,
            "paper_order_only": True,
        },
        "short_term": {"strategy_family": "단타/스윙 패턴", "rows": short_rows},
        "etf_allocation": _etf_backtests(),
    }
    out_json = Path("artifacts/dashboard-pilot-20260430/strategy_evidence_report.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_html = Path("tradingagents/dashboard/static/strategy-evidence-100d.html")
    out_html.write_text(_render_html(report), encoding="utf-8")
    print(json.dumps({"json": str(out_json), "html": str(out_html), "short_top": [(r["ticker"], r["strategy_return_percent"], r["trade_count"]) for r in short_rows[:3]], "etf_100_top": [(r["display_name"], r["total_return_percent"]) for r in report["etf_allocation"]["최근 100거래일"]["rows"][:3]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
