"""Every strategy the research pipeline may test, with its parameter grid FIXED IN ADVANCE.

The number of variants is the N in the deflated Sharpe ratio. Grids are set before seeing any data;
growing one after looking at results is p-hacking and must show up in review as a changed test count.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.research.sweep import grid
from agent.strategies import c1_carry, c2_trend, c3_cascade, c5_seasonality
from agent.strategies.base import Strategy


@dataclass(frozen=True)
class StrategySpec:
    name: str
    build: Callable[..., Strategy]  # build(params, symbols=None)
    grid: Mapping[str, Sequence[Any]]
    valid: Callable[[dict[str, Any]], bool]
    live_eligible: bool


def _always(p: dict[str, Any]) -> bool:
    return True


def _sym(symbols: tuple[str, ...] | None) -> dict[str, Any]:
    return {} if symbols is None else {"symbols": symbols}


REGISTRY: dict[str, StrategySpec] = {
    "c1_carry": StrategySpec(
        "c1_carry",
        lambda p, symbols=None: c1_carry.FundingCarry(c1_carry.CarryParams(**p), **_sym(symbols)),
        c1_carry.GRID,
        c1_carry.valid,
        live_eligible=True,
    ),
    "c2_trend": StrategySpec(
        "c2_trend",
        lambda p, symbols=None: c2_trend.Trend(c2_trend.TrendParams(**p), **_sym(symbols)),
        c2_trend.GRID,
        _always,
        live_eligible=True,
    ),
    "c3_cascade": StrategySpec(
        "c3_cascade",
        lambda p, symbols=None: c3_cascade.Cascade(c3_cascade.CascadeParams(**p), **_sym(symbols)),
        c3_cascade.GRID,
        _always,
        live_eligible=True,
    ),
    # A control that must fail validation. Never tradeable, whatever its backtest says.
    "c5_seasonality": StrategySpec(
        "c5_seasonality",
        lambda p: c5_seasonality.Seasonality(c5_seasonality.SeasonParams(**p)),
        c5_seasonality.GRID,
        _always,
        live_eligible=False,
    ),
}
# C4 (cross-exchange arbitrage) is an analyzer in agent/strategies/c4_xarb.py, deliberately not a Strategy.


def variants(name: str) -> list[dict[str, Any]]:
    spec = REGISTRY[name]
    return [p for p in grid(spec.grid) if spec.valid(p)]
