from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class PriceTimingPolicy:
    """Base prompt policy for converting analysis into price-timing levels.

    Subclass this and register it with :func:`register_price_timing_policy` to
    change how Quant Strategy Analyst derives entry, target, and stop guidance
    without rewriting the agent prompt or graph wiring.
    """

    key: ClassVar[str] = "base"
    label: ClassVar[str] = "Base Price Timing Policy"

    def prompt_instructions(self) -> str:
        raise NotImplementedError

    def render_prompt_block(self) -> str:
        return (
            "\n\nPrice Timing Policy\n"
            f"- selected_policy: {self.key}\n"
            f"- label: {self.label}\n"
            f"- instructions: {self.prompt_instructions()}\n"
        )


class BalancedPriceTimingPolicy(PriceTimingPolicy):
    key = "balanced"
    label = "Balanced Support/Resistance + ATR Policy"

    def prompt_instructions(self) -> str:
        return (
            "Start with nearby support/resistance and the 20/50-day moving averages. "
            "Use ATR to validate whether the entry, stop-loss, and take-profit distances are realistic. "
            "Prefer an entry zone near support or after confirmed resistance reclaim; set the first take-profit near recent resistance or roughly 1.5-2.0x risk; "
            "place stop-loss below the invalidation pivot or approximately 1.0-1.5x ATR below entry when price data supports it."
        )


class ConservativePullbackPolicy(PriceTimingPolicy):
    key = "conservative_pullback"
    label = "Conservative Pullback Policy"

    def prompt_instructions(self) -> str:
        return (
            "Avoid chasing breakouts. Prefer pullback entries near the 20-day or 50-day moving average, recent support, or lower Bollinger Band reclaim. "
            "Require a favorable reward/risk ratio before suggesting a buy. Place stops below the support/reclaim level and take profit into prior resistance."
        )


class BreakoutMomentumPolicy(PriceTimingPolicy):
    key = "breakout_momentum"
    label = "Breakout Momentum Policy"

    def prompt_instructions(self) -> str:
        return (
            "Prefer entries only after confirmed breakout above recent resistance or upper range with volume/volatility confirmation. "
            "Use the breakout pivot as invalidation; avoid entries if price is extended far beyond ATR-normalized risk. "
            "Use partial take-profit at the first measured-move or prior resistance extension."
        )


_REGISTRY: dict[str, type[PriceTimingPolicy]] = {}


def register_price_timing_policy(policy_cls: type[PriceTimingPolicy]) -> type[PriceTimingPolicy]:
    key = str(getattr(policy_cls, "key", "")).strip().lower()
    if not key:
        raise ValueError("price timing policy key must be non-empty")
    _REGISTRY[key] = policy_cls
    return policy_cls


def available_price_timing_policies() -> dict[str, type[PriceTimingPolicy]]:
    return dict(_REGISTRY)


def build_price_timing_policy(name: str | None = None) -> PriceTimingPolicy:
    selected = (name or os.getenv("TRADINGAGENTS_PRICE_TIMING_STRATEGY") or "balanced").strip().lower()
    policy_cls = _REGISTRY.get(selected)
    if policy_cls is None:
        policy_cls = _REGISTRY["balanced"]
    return policy_cls()


for _policy_cls in (BalancedPriceTimingPolicy, ConservativePullbackPolicy, BreakoutMomentumPolicy):
    register_price_timing_policy(_policy_cls)
