from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents.automation.crypto_signal_engine import DEFAULT_CRYPTO_SYMBOLS, build_crypto_snapshot


def _load_llm_market_view(path: str | None) -> dict[str, Any]:
    if not path:
        return {
            "bias": "neutral",
            "summary": "LLM 시장뷰 미입력: hourly 차트 기반 모의전략만 적용",
        }
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("LLM market view JSON must be an object")
    return payload


def _download_hourly_candles(symbol: str, period: str = "30d") -> list[dict[str, Any]]:
    import yfinance as yf

    history = yf.Ticker(symbol).history(period=period, interval="1h", auto_adjust=True)
    candles: list[dict[str, Any]] = []
    if history is None or history.empty:
        return candles
    for idx, row in history.reset_index().iterrows():
        time_value = row.get("Datetime") or row.get("Date") or idx
        candles.append(
            {
                "time": str(time_value),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row.get("Volume", 0) or 0),
            }
        )
    return candles


def _format_percent(value: object) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if amount >= 0 else ""
    return f"{sign}{amount:.2f}%"


def build_hourly_dashboard_report(
    stock_snapshot: dict[str, Any],
    crypto_snapshot: dict[str, Any],
    *,
    report_stage: str = "장중",
    report_purpose: str = "전략 가격 레벨 추적과 모의거래 이벤트 점검",
    api_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    stock_rows = stock_snapshot.get("rows") if isinstance(stock_snapshot.get("rows"), list) else []
    crypto_rows = crypto_snapshot.get("rows") if isinstance(crypto_snapshot.get("rows"), list) else []
    stock_signals: list[dict[str, Any]] = []
    for row in stock_rows[:5]:
        if not isinstance(row, dict):
            continue
        stock_signals.append(
            {
                "ticker": row.get("ticker"),
                "action": row.get("action"),
                "status_label": row.get("status_label"),
                "entry_zone_label": row.get("entry_zone_label"),
                "risk_reward_label": f"목표 {row.get('take_profit_1', '-')} / 손절 {row.get('stop_loss', '-')}",
            }
        )
    crypto_return = (crypto_snapshot.get("paper_portfolio") or {}).get("return_percent") if isinstance(crypto_snapshot.get("paper_portfolio"), dict) else None
    crypto_signals: list[dict[str, Any]] = []
    for row in crypto_rows[:5]:
        if not isinstance(row, dict):
            continue
        crypto_signals.append(
            {
                "ticker": row.get("ticker"),
                "action": row.get("action"),
                "signal_score": row.get("signal_score"),
                "entry_zone_label": row.get("entry_zone_label"),
                "paper_return_label": _format_percent(crypto_return),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "title": "TradingAgents 장 운영 전략 리포트",
        "report_stage": report_stage,
        "report_purpose": report_purpose,
        "report_cadence_label": "장 이벤트 기준 리포트 · 시스템 트레이딩 상시 추적",
        "market_summary": {
            "market_score": stock_snapshot.get("market_score"),
            "previous_market_score": stock_snapshot.get("previous_market_score"),
            "score_delta": stock_snapshot.get("score_delta"),
            "risk_level_label": stock_snapshot.get("risk_level_label") or stock_snapshot.get("risk_level"),
            "change_summary": stock_snapshot.get("change_summary"),
        },
        "stock_signals": stock_signals,
        "crypto_signals": crypto_signals,
        "factor_breakdown": stock_snapshot.get("factor_breakdown") if isinstance(stock_snapshot.get("factor_breakdown"), list) else [],
        "dashboard_updates": ["ETF/주식 가격표 갱신", "암호화폐 모의투자 갱신", "장 운영 리포트 보드 갱신"],
        "safety": {"live_capital_allowed": False, "paper_order_only": True, "label": "실거래 차단 · 모의주문 전용"},
        "api_checks": api_checks or [],
        "llm_market_view": crypto_snapshot.get("llm_market_view") if isinstance(crypto_snapshot.get("llm_market_view"), dict) else {},
        "paper_portfolio": crypto_snapshot.get("paper_portfolio") if isinstance(crypto_snapshot.get("paper_portfolio"), dict) else {},
        "strategy_revision": crypto_snapshot.get("strategy_revision") if isinstance(crypto_snapshot.get("strategy_revision"), dict) else {},
    }


def update_crypto_snapshot_files(
    out_dir: Path,
    symbols: list[str],
    llm_market_view: dict[str, Any],
    *,
    starting_cash: float = 1_000_000.0,
    report_stage: str = "장중",
    report_purpose: str = "전략 가격 레벨 추적과 모의거래 이벤트 점검",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "crypto_signal_snapshot.json"
    previous_portfolio: dict[str, Any] | None = None
    if out_path.exists():
        try:
            previous_snapshot = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous_snapshot = {}
        if isinstance(previous_snapshot, dict) and isinstance(previous_snapshot.get("paper_portfolio"), dict):
            previous_portfolio = previous_snapshot["paper_portfolio"]
    symbol_candles = {symbol: _download_hourly_candles(symbol) for symbol in symbols}
    snapshot = build_crypto_snapshot(symbol_candles, llm_market_view=llm_market_view, starting_cash=starting_cash, previous_portfolio=previous_portfolio)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    stock_path = out_dir / "signal_snapshot.json"
    if stock_path.exists():
        try:
            stock_snapshot = json.loads(stock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stock_snapshot = {}
        if isinstance(stock_snapshot, dict):
            report = build_hourly_dashboard_report(
                stock_snapshot,
                snapshot,
                report_stage=report_stage,
                report_purpose=report_purpose,
                api_checks=[
                    {"name": "ETF/주식 API", "status": "pending_public_sync", "url": "/api/signals/price-table"},
                    {"name": "Crypto API", "status": "pending_public_sync", "url": "/api/signals/crypto"},
                ],
            )
            (out_dir / "hourly_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    history_path = out_dir / "crypto_signal_snapshot_history.jsonl"
    compact = {
        "timestamp": snapshot.get("timestamp") or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "asset_class": "crypto",
        "timeframe": snapshot.get("timeframe"),
        "rows": len(snapshot.get("rows", [])),
        "paper_portfolio": snapshot.get("paper_portfolio"),
        "strategy_revision": snapshot.get("strategy_revision"),
        "live_capital_allowed": False,
        "paper_order_only": True,
    }
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(compact, ensure_ascii=False) + "\n")
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Update TradingAgents crypto hourly signal snapshot")
    parser.add_argument("--dashboard-dir", default="artifacts/dashboard-pilot-20260430")
    parser.add_argument("--symbols", default=",".join(DEFAULT_CRYPTO_SYMBOLS))
    parser.add_argument("--llm-view-json", default=None)
    parser.add_argument("--starting-cash", type=float, default=1_000_000.0)
    parser.add_argument("--report-stage", default="장중")
    parser.add_argument("--report-purpose", default="전략 가격 레벨 추적과 모의거래 이벤트 점검")
    args = parser.parse_args()

    symbols = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    llm_market_view = _load_llm_market_view(args.llm_view_json)
    out_dir = Path(args.dashboard_dir)
    snapshot = update_crypto_snapshot_files(
        out_dir,
        symbols,
        llm_market_view,
        starting_cash=args.starting_cash,
        report_stage=args.report_stage,
        report_purpose=args.report_purpose,
    )
    out_path = out_dir / "crypto_signal_snapshot.json"
    print(out_path)
    print(
        json.dumps(
            {
                "asset_class": snapshot.get("asset_class"),
                "timeframe": snapshot.get("timeframe"),
                "rows": len(snapshot.get("rows", [])),
                "paper_return_percent": snapshot.get("paper_portfolio", {}).get("return_percent"),
                "live_capital_allowed": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
