from __future__ import annotations

import math
from datetime import date
from typing import Any

from tradingagents.strategies.schema import StrategySpec, parse_strategy_spec

from .backtest import _valid_points


def build_walk_forward_backtest(
    records: list[dict[str, Any]],
    chart: dict[str, Any],
    *,
    start_date: str,
    end_date: str | None = None,
    initial_capital: int = 100_000_000,
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> dict[str, Any]:
    """Run an event-driven walk-forward replay across saved StrategySpec versions.

    This is deterministic and does not call agents/LLMs. When a strategy is
    invalidated by stop-loss/reanalysis, the runner switches to the next saved
    strategy whose trade_date is available at or after that event date.
    """
    if not chart.get("available"):
        return {"available": False, "reason": "chart_unavailable", "events": [], "trades": []}
    if initial_capital <= 0:
        return {"available": False, "reason": "invalid_initial_capital", "events": [], "trades": []}
    try:
        start = date.fromisoformat(str(start_date)[:10])
    except ValueError:
        return {"available": False, "reason": "invalid_start_date", "events": [], "trades": []}
    end = _parse_date(end_date) if end_date else None
    if end is not None and end < start:
        return {"available": False, "reason": "invalid_date_range", "events": [], "trades": []}
    try:
        cost_rate = (float(commission_bps) + float(slippage_bps)) / 10_000.0
    except (TypeError, ValueError):
        return {"available": False, "reason": "invalid_cost_model", "events": [], "trades": []}
    if not math.isfinite(cost_rate) or cost_rate < 0:
        return {"available": False, "reason": "invalid_cost_model", "events": [], "trades": []}

    points = [_with_date(point) for point in _valid_points(chart.get("points") or [])]
    points = [point for point in points if point.get("_date") is not None and point["_date"] >= start and (end is None or point["_date"] <= end)]
    if len(points) < 2:
        return {"available": False, "reason": "insufficient_price_history", "events": [], "trades": []}

    strategies = _strategy_versions(records)
    strategies = [item for item in strategies if item["trade_date_obj"] <= (end or points[-1]["_date"])]
    if not strategies:
        return {"available": False, "reason": "strategy_spec_unavailable", "events": [], "trades": []}

    ticker = str(strategies[0]["spec"].ticker or (records[0].get("ticker") if records else ""))
    currency = str(chart.get("currency") or strategies[0]["spec"].currency or "KRW").upper()
    first_close = points[0]["close"]
    cash = float(initial_capital)
    shares = 0.0
    entry_price: float | None = None
    entry_capital: float | None = None
    entry_date: str | None = None
    current: dict[str, Any] | None = None
    current_index = -1
    active_blocked_until_next_strategy = False
    events: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    used_strategy_ids: list[str] = []
    reanalysis_count = 0

    for point in points:
        point_date = point["_date"]
        if current is None or active_blocked_until_next_strategy:
            next_index = _next_strategy_index(strategies, current_index, point_date, allow_initial=current is None and not active_blocked_until_next_strategy)
            if next_index is not None:
                current_index = next_index
                current = strategies[current_index]
                active_blocked_until_next_strategy = False
                spec = current["spec"]
                used_strategy_ids.append(spec.strategy_id)
                events.append(
                    {
                        "type": "strategy_selected",
                        "label": "전략 선택",
                        "date": point["date"],
                        "run_id": current["run_id"],
                        "strategy_id": spec.strategy_id,
                        "strategy_trade_date": spec.trade_date,
                        "reason": "시작 전략" if len(used_strategy_ids) == 1 else "재분석 후 전략 교체",
                    }
                )
            elif current is None:
                equity_curve.append(_equity_point(point, cash, shares))
                continue

        spec = current["spec"] if current else None
        if spec is None:
            equity_curve.append(_equity_point(point, cash, shares))
            continue
        levels = spec.to_price_timing_levels()
        entry_low = float(levels["entry_low"])
        entry_high = float(levels["entry_high"])
        take_profit = float(levels["take_profit"])
        stop_loss = float(levels["stop_loss"])

        if shares == 0.0 and not active_blocked_until_next_strategy:
            if point["low"] <= entry_high and point["high"] >= entry_low:
                fill = min(max(entry_high, point["low"]), point["high"])
                entry_capital = cash
                tradable_cash = cash * (1 - cost_rate)
                shares = tradable_cash / fill
                cash = 0.0
                entry_price = fill
                entry_date = point["date"]
                events.append(
                    {
                        "type": "buy",
                        "label": "매수",
                        "date": point["date"],
                        "price": round(fill, 4),
                        "capital": int(round(entry_capital)),
                        "run_id": current["run_id"],
                        "strategy_id": spec.strategy_id,
                        "reason": "진입 구간 도달",
                    }
                )

        exit_price = None
        exit_reason = None
        exit_label = None
        if shares > 0.0:
            # Conservative daily OHLC assumption: stop first if both stop and target are touched.
            if point["low"] <= stop_loss:
                exit_price = stop_loss
                exit_reason = "stop_loss"
                exit_label = "손절"
            elif point["high"] >= take_profit:
                exit_price = take_profit
                exit_reason = "take_profit"
                exit_label = "익절"

        if exit_price is not None and entry_price is not None and entry_capital is not None:
            gross_cash = shares * exit_price
            cash = gross_cash * (1 - cost_rate)
            shares = 0.0
            pnl = cash - entry_capital
            trade_return = pnl / entry_capital * 100 if entry_capital else 0.0
            trade = {
                "entry_date": entry_date,
                "entry_price": round(entry_price, 4),
                "exit_date": point["date"],
                "exit_price": round(exit_price, 4),
                "exit_reason": exit_reason,
                "return_percent": round(trade_return, 2),
                "pnl": int(round(pnl)),
                "run_id": current["run_id"],
                "strategy_id": spec.strategy_id,
            }
            trades.append(trade)
            events.append(
                {
                    "type": "sell",
                    "label": exit_label,
                    "date": point["date"],
                    "price": round(exit_price, 4),
                    "reason": exit_reason,
                    "return_percent": round(trade_return, 2),
                    "pnl": int(round(pnl)),
                    "equity": int(round(cash)),
                    "run_id": current["run_id"],
                    "strategy_id": spec.strategy_id,
                }
            )
            if exit_reason == "stop_loss":
                reanalysis_count += 1
                next_version = _peek_next_strategy(strategies, current_index)
                events.append(
                    {
                        "type": "reanalysis_required",
                        "label": "재분석 필요",
                        "date": point["date"],
                        "price": round(exit_price, 4),
                        "reason": "손절가 이탈",
                        "next_strategy_run_id": next_version.get("run_id") if next_version else None,
                        "next_strategy_date": next_version.get("trade_date") if next_version else None,
                        "auto_reentry_blocked": True,
                    }
                )
                active_blocked_until_next_strategy = True
                current = None
            entry_price = None
            entry_capital = None
            entry_date = None
        equity_curve.append(_equity_point(point, cash, shares))

    final_equity = cash if shares == 0.0 else shares * points[-1]["close"]
    if shares and entry_price is not None and entry_capital is not None:
        pnl = final_equity - entry_capital
        trades.append(
            {
                "entry_date": entry_date,
                "entry_price": round(entry_price, 4),
                "exit_date": points[-1]["date"],
                "exit_price": round(points[-1]["close"], 4),
                "exit_reason": "period_end",
                "return_percent": round(pnl / entry_capital * 100, 2) if entry_capital else 0.0,
                "pnl": int(round(pnl)),
                "run_id": current.get("run_id") if current else None,
                "strategy_id": current["spec"].strategy_id if current else None,
            }
        )
    pnl_total = final_equity - float(initial_capital)
    benchmark_return = (points[-1]["close"] - first_close) / first_close * 100 if first_close else 0.0
    wins = sum(1 for trade in trades if float(trade.get("return_percent") or 0.0) > 0)
    max_drawdown = _max_drawdown_percent(equity_curve, initial_capital)
    return {
        "available": True,
        "mode": "stored_strategy_walk_forward",
        "ticker": ticker,
        "currency": currency,
        "start_date": points[0]["date"],
        "end_date": points[-1]["date"],
        "initial_capital": int(round(initial_capital)),
        "final_equity": int(round(final_equity)),
        "pnl": int(round(pnl_total)),
        "return_percent": round(pnl_total / float(initial_capital) * 100, 2),
        "benchmark_return_percent": round(benchmark_return, 2),
        "excess_return_percent": round(pnl_total / float(initial_capital) * 100 - benchmark_return, 2),
        "trade_count": len(trades),
        "win_rate_percent": round(wins / len(trades) * 100, 2) if trades else 0.0,
        "max_drawdown_percent": max_drawdown,
        "strategy_version_count": len(set(used_strategy_ids)),
        "reanalysis_count": reanalysis_count,
        "costs": {
            "commission_bps": float(commission_bps),
            "slippage_bps": float(slippage_bps),
            "one_way_cost_percent": round(cost_rate * 100, 4),
            "round_trip_cost_percent": round(cost_rate * 200, 4),
        },
        "events": events,
        "trades": trades,
        "equity_curve": [{k: v for k, v in point.items() if k != "_date"} for point in equity_curve],
        "strategy_versions": [
            {
                "run_id": item["run_id"],
                "strategy_id": item["spec"].strategy_id,
                "trade_date": item["trade_date"],
            }
            for item in strategies
        ],
        "summary_label": "전략 재수립 포함 워크포워드",
        "disclaimer": "저장된 StrategySpec만 사용한 결정론적 walk-forward 시뮬레이션이며 미래 수익을 보장하지 않습니다.",
    }


def _strategy_versions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    for record in records:
        spec = parse_strategy_spec(record.get("strategy_spec") if isinstance(record, dict) else None)
        trade_date = _parse_date(record.get("trade_date") if isinstance(record, dict) else None) or _parse_date(spec.trade_date if spec else None)
        if spec is None or trade_date is None:
            continue
        versions.append(
            {
                "run_id": record.get("run_id"),
                "ticker": record.get("ticker") or spec.ticker,
                "trade_date": trade_date.isoformat(),
                "trade_date_obj": trade_date,
                "spec": spec,
            }
        )
    versions.sort(key=lambda item: (item["trade_date_obj"], str(item.get("run_id") or "")))
    return versions


def _parse_date(value: Any) -> date | None:
    try:
        text = str(value or "")
        if not text:
            return None
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _with_date(point: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(point)
    enriched["_date"] = _parse_date(point.get("date"))
    return enriched


def _next_strategy_index(strategies: list[dict[str, Any]], current_index: int, point_date: date, *, allow_initial: bool) -> int | None:
    if allow_initial:
        eligible = [index for index, item in enumerate(strategies) if item["trade_date_obj"] <= point_date]
        if eligible:
            return eligible[-1]
        for index, item in enumerate(strategies):
            if item["trade_date_obj"] >= point_date:
                return index if item["trade_date_obj"] <= point_date else None
        return None
    for index in range(current_index + 1, len(strategies)):
        if strategies[index]["trade_date_obj"] <= point_date:
            return index
        return None
    return None


def _peek_next_strategy(strategies: list[dict[str, Any]], current_index: int) -> dict[str, Any] | None:
    next_index = current_index + 1
    if 0 <= next_index < len(strategies):
        return strategies[next_index]
    return None


def _equity_point(point: dict[str, Any], cash: float, shares: float) -> dict[str, Any]:
    equity = cash if shares == 0.0 else shares * point["close"]
    return {"date": point["date"], "equity": int(round(equity)), "close": point["close"], "_date": point.get("_date")}


def _max_drawdown_percent(equity_curve: list[dict[str, Any]], initial_capital: int) -> float:
    peak = float(initial_capital)
    max_drawdown = 0.0
    for point in equity_curve:
        equity = float(point.get("equity") or 0.0)
        if equity > peak:
            peak = equity
        if peak > 0:
            max_drawdown = min(max_drawdown, (equity - peak) / peak * 100)
    return round(abs(max_drawdown), 2)
