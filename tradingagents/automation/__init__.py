"""Automation signal helpers for TradingAgents."""

from .signal_engine import build_price_table, calculate_market_score, detect_fair_value_gaps

__all__ = ["build_price_table", "calculate_market_score", "detect_fair_value_gaps"]
