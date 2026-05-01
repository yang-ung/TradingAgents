from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_indicators,
    get_language_instruction,
    get_stock_data,
)
from tradingagents.agents.strategies.price_timing import build_price_timing_policy


QUANT_STRATEGY_SYSTEM_MESSAGE = """You are a Quant Strategy Analyst focused on practical price timing for a single stock.
Your job is not to repeat the market analyst. Your job is to decide which trading strategy, if any, is usable now and to translate the setup into a disciplined trading plan.

Evaluate these strategy families explicitly:
- trend-following: moving-average alignment, pullback-to-support, breakout continuation, MACD confirmation.
- mean-reversion: RSI/Bollinger Band stretch, range support/resistance, failed breakdown/reclaim.
- volatility breakout: ATR expansion, range compression, prior high/low breakout, volume confirmation.
- momentum: short-term relative strength, gap/follow-through, volume-backed continuation.
- no-trade / wait: when signals conflict, liquidity is poor, volatility is too high, or price is between levels.

You must produce price timing guidance:
- preferred entry price or entry price zone;
- add-on / scale-in trigger if applicable;
- take-profit or first target zone;
- stop-loss / invalidation price;
- position sizing guidance;
- expected holding period;
- what would make the strategy invalid.

Important constraints:
- Do not invent exact prices. Use exact entry, take-profit, and stop-loss levels only when supported by retrieved price/indicator data, recent highs/lows, moving averages, ATR, or clearly stated source evidence.
- If exact prices are not supported, give conditional zones and formulas, e.g. "near the 20-day moving average", "above recent resistance", or "1.5x ATR below entry".
- Separate executable signals from watchlist conditions. If the best action is to wait, say exactly what price/indicator trigger would change that.
- Keep risk first: every buy/sell idea needs an invalidation level.
- The output must be useful to a trader who asks: "얼마에 사고 얼마에 팔아야 하나?"
- Also include a machine-readable StrategySpec JSON block when exact executable levels are supported. This lets the programmatic_rule_engine run signals/backtests without asking the agent again.
- The StrategySpec JSON must include: strategy_id, ticker, trade_date, strategy_type="price_timing_long", execution_mode="programmatic_rule_engine", entry {type="price_zone", low, high}, take_profit {type="fixed_price", price}, stop_loss {type="fixed_price", price}, currency, basis, avoid_conditions, confidence, valid_until, and reanalysis_triggers.
- valid_until should express the strategy review horizon. reanalysis_triggers should tell the program when to ask an agent to reassess, e.g. price_below/price_above levels, volume_spike multiplier/lookback_days, moving_average_cross ma/direction, and time_expired date with Korean reason strings.
- If supported executable levels are not available, omit the StrategySpec JSON and state which data is missing.

Call get_stock_data first. Then call get_indicators for a compact but sufficient set: close_10_ema, close_50_sma, close_200_sma, macd, macds, macdh, rsi, boll, boll_ub, boll_lb, atr, and vwma when available. Avoid redundant commentary; focus on strategy selection and price timing.

Return a report with these sections:
1. Strategy Verdict
2. Entry Timing
3. Exit / Take-Profit Plan
4. Stop-Loss / Invalidation
5. Position Sizing
6. Strategy Fit Table
7. Conditions to Reassess
"""


def create_quant_strategy_analyst(llm):
    def quant_strategy_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])
        tools = [get_stock_data, get_indicators]
        price_timing_policy = build_price_timing_policy()

        system_message = (
            QUANT_STRATEGY_SYSTEM_MESSAGE
            + price_timing_policy.render_prompt_block()
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to produce a quantitative strategy and price-timing report."
                    " If you cannot support exact prices from data, provide conditional zones or indicator formulas instead."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])
        report = ""
        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "quant_strategy_report": report,
        }

    return quant_strategy_analyst_node
