"""Core value types shared by strategies, sizing, risk and execution."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from agent.data.datasets import Market


@dataclass(frozen=True)
class Instrument:
    symbol: str
    market: Market

    def __post_init__(self) -> None:
        object.__setattr__(self, "market", Market(self.market))

    @property
    def key(self) -> str:
        return f"{self.market}:{self.symbol}"

    @classmethod
    def from_key(cls, key: str) -> Instrument:
        market, symbol = key.split(":", 1)
        return cls(symbol, Market(market))


@dataclass(frozen=True)
class Target:
    """Desired signed notional as a fraction of equity. Grouped targets (e.g. carry legs) move together."""

    instrument: Instrument
    weight: float
    group: str | None = None
    reason: str = ""

    def with_weight(self, weight: float) -> Target:
        return Target(self.instrument, weight, self.group, self.reason)


@dataclass(frozen=True)
class TopOfBook:
    ts: pd.Timestamp
    bid: float
    bid_qty: float
    ask: float
    ask_qty: float
