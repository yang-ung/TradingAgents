from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

DEFAULT_CRYPTO_SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD"]
CRYPTO_BINANCE_SYMBOLS = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT",
    "XRP-USD": "XRPUSDT",
}


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _valid_candle(candle: Mapping[str, object]) -> bool:
    return all(key in candle for key in ("high", "low", "close"))


def _normalize_candles(candles: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, candle in enumerate(candles):
        if not isinstance(candle, Mapping) or not _valid_candle(candle):
            continue
        close = _to_float(candle.get("close"))
        open_price = _to_float(candle.get("open"), close)
        high = max(_to_float(candle.get("high"), close), open_price, close)
        low = min(_to_float(candle.get("low"), close), open_price, close)
        if close <= 0:
            continue
        normalized.append(
            {
                "time": candle.get("time") or candle.get("date") or str(idx),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": max(0.0, _to_float(candle.get("volume"))),
            }
        )
    return normalized


def _sma(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(values[-period - 1 : -1], values[-period:]):
        change = cur - prev
        if change >= 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _volatility_percent(values: Sequence[float], window: int = 24) -> float:
    if len(values) < 2:
        return 0.0
    sample = values[-window:] if len(values) >= window else values
    returns = [(cur - prev) / prev for prev, cur in zip(sample[:-1], sample[1:]) if prev]
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    return (variance**0.5) * 100.0


def _format_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _format_signed_percent(value: float) -> str:
    return f"{value:+.2f}%"


def _format_won(value: float) -> str:
    rounded = int(round(value))
    sign = "+" if rounded > 0 else ""
    return f"{sign}{rounded:,}원"


def _format_integer_label(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{int(float(value)):,}"


def _format_quote_price_label(value: float | None, quote_to_krw: float) -> str:
    if value is None:
        return "-"
    converted = float(value) * float(quote_to_krw)
    label = _format_integer_label(converted)
    return f"{label}원" if quote_to_krw != 1.0 and label != "-" else label


def _format_krw_value_label(value: float | None, quote_to_krw: float) -> str:
    if quote_to_krw == 1.0:
        return _format_integer_label(value)
    return f"{_format_integer_label(value)}원"


def _profit_status_label(return_percent: float) -> str:
    if return_percent > 0.05:
        return "수익 중"
    if return_percent < -0.05:
        return "손실 중"
    return "본전권"


def _paper_event_label(action: object) -> str:
    text = str(action or "")
    if "매수" in text:
        return "모의매수"
    if "관찰" in text:
        return "모의관찰"
    return "모의보유"


def build_crypto_strategy_row(ticker: str, candles: Sequence[Mapping[str, object]], *, llm_bias: str = "neutral") -> dict[str, Any]:
    """Build one hourly crypto strategy row from deterministic chart indicators plus an LLM market-view bias."""
    valid = _normalize_candles(candles)
    if not valid:
        return {
            "ticker": ticker,
            "asset_class": "crypto",
            "timeframe": "1h",
            "strategy_name": "crypto_hourly_trend_pullback",
            "current_price": 0.0,
            "signal_score": 0,
            "action": "데이터 부족",
            "entry_zone_label": "-",
            "stop_loss": None,
            "take_profit_1": None,
            "paper_order_only": True,
            "live_capital_allowed": False,
            "strategy_commentary": "차트 데이터가 부족하여 모의매매 신호를 만들지 않았습니다.",
        }
    closes = [float(candle["close"]) for candle in valid]
    current = closes[-1]
    ma20 = _sma(closes, 20) or current
    ma50 = _sma(closes, 50) or ma20
    rsi14 = _rsi(closes, 14)
    vol24 = _volatility_percent(closes, 24)
    recent_high = max(closes[-24:]) if len(closes) >= 24 else max(closes)
    recent_low = min(closes[-24:]) if len(closes) >= 24 else min(closes)
    trend_up = current >= ma20 >= ma50
    pullback = current <= recent_high * 0.985 and current >= ma20 * 0.97
    breakout = current >= recent_high * 0.995

    score = 45
    if trend_up:
        score += 20
    if pullback:
        score += 12
    if breakout:
        score += 10
    if 40 <= rsi14 <= 68:
        score += 8
    elif rsi14 > 78:
        score -= 12
    if vol24 > 4.5:
        score -= 8
    bias = str(llm_bias or "neutral").lower()
    if bias == "bullish":
        score += 7
    elif bias == "bearish":
        score -= 10
    score = max(0, min(100, int(round(score))))

    entry_low = min(current, ma20) * 0.995
    entry_high = current if pullback or breakout else min(current, ma20) * 1.005
    stop_loss = min(recent_low, entry_low * 0.965)
    risk = max(entry_high - stop_loss, current * 0.015)
    take_profit_1 = current + risk * 1.8

    if score >= 70:
        action = "모의매수 후보"
    elif score >= 58:
        action = "모의관찰"
    elif score >= 45:
        action = "모의보유"
    else:
        action = "모의회피"

    commentary = (
        f"차트: MA20 {ma20:.2f}, MA50 {ma50:.2f}, RSI14 {rsi14:.1f}, 24시간 변동성 {vol24:.2f}%. "
        f"LLM 시장뷰는 {bias}로 반영했으며, 실거래가 아닌 paper 계좌 모의투자로만 실행합니다."
    )
    return {
        "ticker": ticker,
        "asset_class": "crypto",
        "timeframe": "1h",
        "strategy_name": "crypto_hourly_trend_pullback",
        "current_price": _format_price(current),
        "signal_score": score,
        "action": action,
        "entry_low": _format_price(entry_low),
        "entry_high": _format_price(entry_high),
        "entry_zone_label": f"{entry_low:.2f} ~ {entry_high:.2f}",
        "stop_loss": _format_price(stop_loss),
        "take_profit_1": _format_price(take_profit_1),
        "indicators": {
            "ma20": _format_price(ma20),
            "ma50": _format_price(ma50),
            "rsi14": round(rsi14, 2),
            "volatility_24h_percent": round(vol24, 2),
            "recent_24h_high": _format_price(recent_high),
            "recent_24h_low": _format_price(recent_low),
        },
        "strategy_commentary": commentary,
        "paper_order_only": True,
        "live_capital_allowed": False,
    }


def run_crypto_paper_simulation(
    rows: Sequence[Mapping[str, object]],
    *,
    starting_cash: float = 1_000_000.0,
    previous_portfolio: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Execute a paper-only crypto portfolio state transition from current rows."""
    row_by_ticker = {str(row.get("ticker")): row for row in rows if isinstance(row, Mapping) and row.get("ticker")}
    candidates = [row for row in rows if isinstance(row, Mapping) and str(row.get("action")) in {"모의매수 후보", "모의관찰", "모의보유"}]
    allocation = starting_cash * 0.2
    positions: list[dict[str, Any]] = []
    trade_history: list[dict[str, Any]] = []
    closed_trades: list[dict[str, Any]] = []
    previous = previous_portfolio if isinstance(previous_portfolio, Mapping) else {}
    previous_positions = previous.get("positions") if isinstance(previous.get("positions"), list) else []
    previous_cash = _to_float(previous.get("cash"), starting_cash - sum(_to_float(pos.get("entry_price")) * _to_float(pos.get("quantity")) for pos in previous_positions if isinstance(pos, Mapping)))
    cash = max(0.0, previous_cash) if previous_positions else starting_cash
    realized_pnl_amount = _to_float(previous.get("realized_pnl_amount"))
    opened_or_held = {str(pos.get("ticker")) for pos in previous_positions if isinstance(pos, Mapping) and pos.get("ticker")}

    for previous_position in previous_positions:
        if not isinstance(previous_position, Mapping):
            continue
        ticker = str(previous_position.get("ticker") or "")
        row = row_by_ticker.get(ticker)
        if not row:
            continue
        price = _to_float(row.get("current_price"))
        qty = _to_float(previous_position.get("quantity"))
        entry_price = _to_float(previous_position.get("entry_price"), price)
        if price <= 0 or qty <= 0:
            continue
        take_profit_1 = _to_float(previous_position.get("take_profit_1"), _to_float(row.get("take_profit_1")))
        stop_loss = _to_float(previous_position.get("stop_loss"), _to_float(row.get("stop_loss")))
        mark_value = qty * price
        pnl = round(mark_value - (qty * entry_price), 2)
        if take_profit_1 and price >= take_profit_1:
            cash += mark_value
            realized_pnl_amount += pnl
            event = "모의매도(익절)"
            closed_trades.append({"ticker": ticker, "exit_price": round(price, 4), "quantity": round(qty, 8), "pnl_amount": pnl, "pnl_label": _format_won(pnl), "reason": "목표가 도달로 paper 계좌에서 익절 매도"})
            trade_history.append({"time": row.get("time") or "현재 스냅샷", "ticker": ticker, "event": event, "price": round(price, 4), "quantity": round(qty, 8), "notional": round(mark_value, 2), "pnl_amount": pnl, "pnl_label": _format_won(pnl), "reason": "목표가 도달로 paper 계좌에서 익절 매도"})
            continue
        if stop_loss and price <= stop_loss:
            cash += mark_value
            realized_pnl_amount += pnl
            event = "모의매도(손절)"
            closed_trades.append({"ticker": ticker, "exit_price": round(price, 4), "quantity": round(qty, 8), "pnl_amount": pnl, "pnl_label": _format_won(pnl), "reason": "손절가 도달로 paper 계좌에서 손절 매도"})
            trade_history.append({"time": row.get("time") or "현재 스냅샷", "ticker": ticker, "event": event, "price": round(price, 4), "quantity": round(qty, 8), "notional": round(mark_value, 2), "pnl_amount": pnl, "pnl_label": _format_won(pnl), "reason": "손절가 도달로 paper 계좌에서 손절 매도"})
            continue
        unrealized_return = round(((price / entry_price) - 1.0) * 100.0, 2) if entry_price else 0.0
        positions.append({"ticker": ticker, "quantity": round(qty, 8), "entry_price": round(entry_price, 4), "current_price": round(price, 4), "market_value": round(mark_value, 2), "unrealized_return_percent": unrealized_return, "unrealized_return_label": _format_signed_percent(unrealized_return), "unrealized_pnl": pnl, "unrealized_pnl_label": _format_won(pnl), "stop_loss": round(stop_loss, 4) if stop_loss else None, "take_profit_1": round(take_profit_1, 4) if take_profit_1 else None, "status": "paper_open", "action": "모의보유"})
        trade_history.append({"time": row.get("time") or "현재 스냅샷", "ticker": ticker, "event": "모의보유", "price": round(price, 4), "quantity": round(qty, 8), "notional": round(mark_value, 2), "pnl_amount": pnl, "pnl_label": _format_won(pnl), "reason": "보유 포지션을 현재가로 평가"})

    for row in candidates[:5]:
        ticker = str(row.get("ticker") or "")
        if not ticker or ticker in opened_or_held or ticker in {position["ticker"] for position in positions}:
            continue
        if str(row.get("action")) != "모의매수 후보":
            continue
        price = _to_float(row.get("current_price"))
        if price <= 0:
            continue
        notional = min(allocation, cash)
        if notional <= 0:
            continue
        qty = notional / price
        cash -= notional
        stop_loss = _to_float(row.get("stop_loss"))
        take_profit_1 = _to_float(row.get("take_profit_1"))
        positions.append({"ticker": ticker, "quantity": round(qty, 8), "entry_price": round(price, 4), "current_price": round(price, 4), "market_value": round(notional, 2), "unrealized_return_percent": 0.0, "unrealized_return_label": _format_signed_percent(0.0), "unrealized_pnl": 0.0, "unrealized_pnl_label": _format_won(0.0), "stop_loss": round(stop_loss, 4) if stop_loss else None, "take_profit_1": round(take_profit_1, 4) if take_profit_1 else None, "status": "paper_open", "action": "모의매수"})
        trade_history.append({"time": row.get("time") or row.get("generated_at") or "현재 스냅샷", "ticker": ticker, "event": "모의매수", "price": round(price, 4), "quantity": round(qty, 8), "notional": round(notional, 2), "pnl_amount": 0.0, "pnl_label": _format_won(0.0), "reason": "시스템 트레이딩이 조건을 만족해 paper 계좌에 모의 매수"})

    portfolio_value = round(cash + sum(float(position["market_value"]) for position in positions), 2)
    return_percent = round(((portfolio_value / starting_cash) - 1.0) * 100.0, 2) if starting_cash else 0.0
    pnl_amount = round(portfolio_value - round(starting_cash, 2), 2)
    revision_required = return_percent <= -3.0 or not positions
    reason = "모의 포지션 없음: 전략 조건을 완화하거나 대상/타임프레임 재검토 필요" if not positions else "paper 계좌 수익률 기준 전략 유지, 다음 hourly 결과로 재평가"
    if return_percent <= -3.0:
        reason = "모의 수익률 -3% 이하: 손절/진입 조건 재조정 필요"
    return {
        "execution_mode": "paper_trade_execution",
        "cadence": "hourly",
        "cadence_label": "암호화폐 1시간마다 갱신",
        "starting_cash": round(starting_cash, 2),
        "cash": round(cash, 2),
        "portfolio_value": portfolio_value,
        "return_percent": return_percent,
        "return_label": _format_signed_percent(return_percent),
        "pnl_amount": pnl_amount,
        "pnl_label": _format_won(pnl_amount),
        "total_pnl": pnl_amount,
        "total_pnl_label": _format_won(pnl_amount),
        "realized_pnl_amount": round(realized_pnl_amount, 2),
        "realized_pnl_label": _format_won(realized_pnl_amount),
        "profit_status_label": _profit_status_label(return_percent),
        "positions": positions,
        "closed_trades": closed_trades,
        "trade_history": trade_history,
        "strategy_revision_required": revision_required,
        "strategy_revision_reason": reason,
        "paper_order_only": True,
        "live_capital_allowed": False,
    }


def _binance_symbol(ticker: object) -> str:
    text = str(ticker or "").upper().strip()
    return CRYPTO_BINANCE_SYMBOLS.get(text, text.replace("-USD", "USDT"))


def _lookup_live_price(ticker: object, live_prices: Mapping[str, object]) -> float:
    symbol = _binance_symbol(ticker)
    return _to_float(live_prices.get(symbol), _to_float(live_prices.get(str(ticker))))


def run_crypto_live_paper_execution(
    snapshot: Mapping[str, object],
    live_prices: Mapping[str, object],
    *,
    commission_bps: float = 10.0,
    quote_to_krw: float = 1.0,
    now: str | None = None,
) -> dict[str, Any]:
    """Run one realtime paper-only execution cycle from Binance ticker prices.

    The hourly snapshot supplies strategy levels; Binance realtime prices only decide fills,
    exits, and marked-to-market PnL. This function never enables live capital.
    """
    timestamp = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
    row_by_ticker = {str(row.get("ticker")): row for row in rows if isinstance(row, Mapping) and row.get("ticker")}
    quote_to_krw = max(1e-9, _to_float(quote_to_krw, 1.0))
    previous = snapshot.get("paper_portfolio") if isinstance(snapshot.get("paper_portfolio"), Mapping) else {}
    starting_cash = _to_float(previous.get("starting_cash"), 1_000_000.0)
    previous_positions = previous.get("positions") if isinstance(previous.get("positions"), list) else []
    previous_cash = _to_float(previous.get("cash"), starting_cash - sum(_to_float(pos.get("entry_price")) * _to_float(pos.get("quantity")) * quote_to_krw for pos in previous_positions if isinstance(pos, Mapping)))
    cash = max(0.0, previous_cash)
    trade_history: list[dict[str, Any]] = []
    old_history = previous.get("trade_history") if isinstance(previous.get("trade_history"), list) else []
    closed_trades: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    total_fees = _to_float(previous.get("total_fees_amount"))
    realized_pnl = _to_float(previous.get("realized_pnl_amount"))
    commission_rate = max(0.0, _to_float(commission_bps)) / 10_000.0
    open_tickers: set[str] = set()

    for previous_position in previous_positions:
        if not isinstance(previous_position, Mapping):
            continue
        ticker = str(previous_position.get("ticker") or "")
        row = row_by_ticker.get(ticker, {})
        price = _lookup_live_price(ticker, live_prices)
        qty = _to_float(previous_position.get("quantity"))
        entry_price = _to_float(previous_position.get("entry_price"), price)
        if price <= 0 or qty <= 0:
            continue
        stop_loss = _to_float(previous_position.get("stop_loss"), _to_float(row.get("stop_loss")))
        take_profit_1 = _to_float(previous_position.get("take_profit_1"), _to_float(row.get("take_profit_1")))
        gross_value = qty * price * quote_to_krw
        sell_fee = round(gross_value * commission_rate, 2)
        entry_fee = _to_float(previous_position.get("entry_fee_amount"))
        cost_basis = qty * entry_price * quote_to_krw + entry_fee
        gross_pnl = gross_value - (qty * entry_price * quote_to_krw)
        net_pnl = round((gross_value - sell_fee) - cost_basis, 2)
        exit_event = ""
        reason = ""
        if take_profit_1 and price >= take_profit_1:
            exit_event = "실시간 모의매도(익절)"
            reason = "익절 체결"
        elif stop_loss and price <= stop_loss:
            exit_event = "실시간 모의매도(손절)"
            reason = "손절 체결"
        if exit_event:
            cash += gross_value - sell_fee
            total_fees += sell_fee
            realized_pnl += net_pnl
            event = {"time": timestamp, "ticker": ticker, "event": exit_event, "price": round(price, 4), "price_label": _format_quote_price_label(price, quote_to_krw), "quantity": round(qty, 8), "notional": round(gross_value, 2), "notional_label": _format_krw_value_label(gross_value, quote_to_krw), "fee_amount": sell_fee, "fee_label": _format_won(-sell_fee), "pnl_amount": net_pnl, "pnl_label": _format_won(net_pnl), "reason": reason}
            trade_history.append(event)
            closed_trades.append(dict(event))
            continue
        unrealized_pnl = round(gross_value - cost_basis, 2)
        unrealized_return = round((unrealized_pnl / cost_basis) * 100.0, 2) if cost_basis else 0.0
        positions.append({"ticker": ticker, "quantity": round(qty, 8), "entry_price": round(entry_price, 4), "entry_price_label": _format_quote_price_label(entry_price, quote_to_krw), "current_price": round(price, 4), "current_price_label": _format_quote_price_label(price, quote_to_krw), "market_value": round(gross_value, 2), "market_value_label": _format_krw_value_label(gross_value, quote_to_krw), "entry_fee_amount": round(entry_fee, 2), "unrealized_return_percent": unrealized_return, "unrealized_return_label": _format_signed_percent(unrealized_return), "unrealized_pnl": unrealized_pnl, "unrealized_pnl_label": _format_won(unrealized_pnl), "stop_loss": round(stop_loss, 4) if stop_loss else None, "stop_loss_label": _format_quote_price_label(stop_loss, quote_to_krw) if stop_loss else "-", "take_profit_1": round(take_profit_1, 4) if take_profit_1 else None, "take_profit_1_label": _format_quote_price_label(take_profit_1, quote_to_krw) if take_profit_1 else "-", "status": "paper_open", "action": "실시간 모의보유"})
        open_tickers.add(ticker)
        trade_history.append({"time": timestamp, "ticker": ticker, "event": "실시간 모의보유", "price": round(price, 4), "price_label": _format_quote_price_label(price, quote_to_krw), "quantity": round(qty, 8), "notional": round(gross_value, 2), "notional_label": _format_krw_value_label(gross_value, quote_to_krw), "fee_amount": 0.0, "fee_label": _format_won(0.0), "pnl_amount": unrealized_pnl, "pnl_label": _format_won(unrealized_pnl), "reason": "보유"})

    allocation = starting_cash * 0.2
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or "")
        if not ticker or ticker in open_tickers:
            continue
        price = _lookup_live_price(ticker, live_prices)
        entry_low = _to_float(row.get("entry_low"))
        entry_high = _to_float(row.get("entry_high"))
        score = int(_to_float(row.get("signal_score")))
        if price <= 0 or entry_low <= 0 or entry_high <= 0 or score < 70 or not (entry_low <= price <= entry_high):
            continue
        gross_notional = min(allocation, cash / (1.0 + commission_rate) if commission_rate else cash)
        if gross_notional <= 0:
            continue
        fee = round(gross_notional * commission_rate, 2)
        qty = gross_notional / (price * quote_to_krw)
        cash -= gross_notional + fee
        total_fees += fee
        stop_loss = _to_float(row.get("stop_loss"))
        take_profit_1 = _to_float(row.get("take_profit_1"))
        positions.append({"ticker": ticker, "quantity": round(qty, 8), "entry_price": round(price, 4), "entry_price_label": _format_quote_price_label(price, quote_to_krw), "current_price": round(price, 4), "current_price_label": _format_quote_price_label(price, quote_to_krw), "market_value": round(gross_notional, 2), "market_value_label": _format_krw_value_label(gross_notional, quote_to_krw), "entry_fee_amount": fee, "unrealized_return_percent": round((-fee / (gross_notional + fee)) * 100.0, 2) if gross_notional else 0.0, "unrealized_return_label": _format_signed_percent(round((-fee / (gross_notional + fee)) * 100.0, 2) if gross_notional else 0.0), "unrealized_pnl": -fee, "unrealized_pnl_label": _format_won(-fee), "stop_loss": round(stop_loss, 4) if stop_loss else None, "stop_loss_label": _format_quote_price_label(stop_loss, quote_to_krw) if stop_loss else "-", "take_profit_1": round(take_profit_1, 4) if take_profit_1 else None, "take_profit_1_label": _format_quote_price_label(take_profit_1, quote_to_krw) if take_profit_1 else "-", "status": "paper_open", "action": "실시간 모의매수"})
        open_tickers.add(ticker)
        trade_history.append({"time": timestamp, "ticker": ticker, "event": "실시간 모의매수", "price": round(price, 4), "price_label": _format_quote_price_label(price, quote_to_krw), "quantity": round(qty, 8), "notional": round(gross_notional, 2), "notional_label": _format_krw_value_label(gross_notional, quote_to_krw), "fee_amount": fee, "fee_label": _format_won(-fee), "pnl_amount": -fee, "pnl_label": _format_won(-fee), "reason": "진입 체결"})

    portfolio_value = round(cash + sum(_to_float(position.get("market_value")) for position in positions), 2)
    total_pnl = round(portfolio_value - starting_cash, 2)
    return_percent = round((total_pnl / starting_cash) * 100.0, 2) if starting_cash else 0.0
    return {
        "execution_mode": "realtime_paper_execution",
        "price_source": "binance_public_ticker",
        "generated_at": timestamp,
        "commission_bps": round(_to_float(commission_bps), 4),
        "quote_currency": "USDT",
        "display_currency": "KRW",
        "asset_value_currency": "KRW",
        "quote_to_krw": round(quote_to_krw, 6),
        "state_version": 2,
        "starting_cash": round(starting_cash, 2),
        "starting_cash_label": _format_integer_label(starting_cash),
        "cash": round(cash, 2),
        "cash_label": _format_integer_label(cash),
        "portfolio_value": portfolio_value,
        "portfolio_value_label": _format_integer_label(portfolio_value),
        "return_percent": return_percent,
        "return_label": _format_signed_percent(return_percent),
        "pnl_amount": total_pnl,
        "pnl_label": _format_won(total_pnl),
        "total_pnl": total_pnl,
        "total_pnl_label": _format_won(total_pnl),
        "realized_pnl_amount": round(realized_pnl, 2),
        "realized_pnl_label": _format_won(realized_pnl),
        "total_fees_amount": round(total_fees, 2),
        "total_fees_label": _format_won(-total_fees) if total_fees else _format_won(0.0),
        "profit_status_label": _profit_status_label(return_percent),
        "positions": positions,
        "closed_trades": closed_trades,
        "trade_history": (trade_history + old_history)[:50],
        "paper_order_only": True,
        "live_capital_allowed": False,
    }


def _normalize_kline(item: object) -> dict[str, Any] | None:
    if isinstance(item, Mapping):
        time_value = item.get("time") or item.get("date") or item.get("open_time")
        open_price = _to_float(item.get("open"))
        high = _to_float(item.get("high"))
        low = _to_float(item.get("low"))
        close = _to_float(item.get("close"))
        volume = _to_float(item.get("volume"))
    elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) >= 6:
        time_value, open_price, high, low, close, volume = item[:6]
        open_price = _to_float(open_price)
        high = _to_float(high)
        low = _to_float(low)
        close = _to_float(close)
        volume = _to_float(volume)
    else:
        return None
    if close <= 0:
        return None
    return {"time": str(time_value), "open": open_price, "high": max(high, open_price, close), "low": min(low, open_price, close), "close": close, "volume": max(0.0, volume)}


def build_live_crypto_chart_payload(
    ticker: str,
    klines: Sequence[object],
    *,
    snapshot: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    candles = [candle for candle in (_normalize_kline(item) for item in klines) if candle]
    rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
    row = next((item for item in rows if isinstance(item, Mapping) and str(item.get("ticker")) == ticker), {})
    portfolio = snapshot.get("paper_portfolio") if isinstance(snapshot.get("paper_portfolio"), Mapping) else {}
    positions = portfolio.get("positions") if isinstance(portfolio.get("positions"), list) else []
    position = next((item for item in positions if isinstance(item, Mapping) and str(item.get("ticker")) == ticker), {})
    return {
        "available": bool(candles),
        "ticker": ticker,
        "binance_symbol": _binance_symbol(ticker),
        "price_source": "binance_public_klines" if candles else "snapshot_fallback",
        "candles": candles,
        "levels": {
            "entry_low": _format_price(_to_float(row.get("entry_low"))) if row else None,
            "entry_high": _format_price(_to_float(row.get("entry_high"))) if row else None,
            "stop_loss": _format_price(_to_float(row.get("stop_loss"))) if row else None,
            "take_profit_1": _format_price(_to_float(row.get("take_profit_1"))) if row else None,
        },
        "position": dict(position) if isinstance(position, Mapping) else {},
        "paper_order_only": True,
        "live_capital_allowed": False,
    }


def build_crypto_snapshot(
    symbol_candles: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    llm_market_view: Mapping[str, object] | None = None,
    starting_cash: float = 1_000_000.0,
    previous_portfolio: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    view = dict(llm_market_view or {})
    bias = str(view.get("bias") or "neutral")
    rows = [build_crypto_strategy_row(symbol, candles, llm_bias=bias) for symbol, candles in symbol_candles.items()]
    rows.sort(key=lambda row: (-int(row.get("signal_score") or 0), str(row.get("ticker") or "")))
    paper = run_crypto_paper_simulation(rows, starting_cash=starting_cash, previous_portfolio=previous_portfolio)
    latest_execution_by_ticker = {
        str(event.get("ticker")): event
        for event in paper.get("trade_history", [])
        if isinstance(event, Mapping) and event.get("ticker")
    }
    position_by_ticker = {
        str(position.get("ticker")): position
        for position in paper.get("positions", [])
        if isinstance(position, Mapping) and position.get("ticker")
    }
    enriched_rows: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "")
        enriched = dict(row)
        event = latest_execution_by_ticker.get(ticker)
        position = position_by_ticker.get(ticker)
        if event:
            label = str(event.get("event") or "관망")
            detail = str(event.get("reason") or "paper 계좌 상태 업데이트")
        elif position:
            label = str(position.get("action") or "모의보유")
            detail = "paper 계좌 보유 포지션"
        else:
            label = "관망"
            detail = "paper 계좌 미보유: 신규 매수 조건 미충족"
        enriched["paper_execution_label"] = label
        enriched["paper_execution_detail"] = detail
        enriched_rows.append(enriched)
    return {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "asset_class": "crypto",
        "timeframe": "1h",
        "crypto_update_cadence": "hourly",
        "crypto_update_cadence_label": "암호화폐 1시간마다 갱신",
        "strategy_name": "crypto_hourly_trend_pullback",
        "source": "yfinance_hourly_ohlc_plus_llm_market_view",
        "llm_market_view": {
            "bias": bias,
            "summary": view.get("summary") or "LLM 시장뷰 미입력: 차트 기반 모의전략만 적용",
        },
        "rows": enriched_rows,
        "paper_portfolio": paper,
        "strategy_revision": {
            "required": bool(paper.get("strategy_revision_required")),
            "reason": paper.get("strategy_revision_reason"),
            "check_after": "hourly_paper_return_review",
        },
        "paper_order_only": True,
        "live_capital_allowed": False,
    }
