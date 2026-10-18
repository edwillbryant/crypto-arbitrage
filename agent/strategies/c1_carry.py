"""C1: delta-neutral funding carry (long spot, short perp).

Edge: leveraged longs pay a persistent premium (positive funding) to hold perp exposure.
Other side: traders who want leverage and prefer perps to spot.
Entry: trailing mean of the last `n_periods` settled funding rates, annualised, >= entry_apr.
Exit: annualised mean < exit_apr (hysteresis avoids churn), or |perp/spot - 1| > max_basis.
Holding: days to weeks. Never reversed: reverse carry needs a spot short, which v1 does not do.
Bear case: basis-trade vaults have compressed the premium; funding has been near zero in late 2026.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from agent.core.types import Instrument, Target
from agent.data.datasets import FUNDING, Market, klines
from agent.strategies.base import DataRequest, Snapshot
from agent.strategies.common import fresh_last_close

H1_SPOT, H1_PERP = klines("spot", "1h"), klines("perp", "1h")
GRID: dict[str, list[Any]] = {
    "entry_apr": [0.10, 0.15, 0.20, 0.30],
    "exit_apr": [0.0, 0.05, 0.10],
    "n_periods": [3, 9, 21],
}


@dataclass(frozen=True)
class CarryParams:
    entry_apr: float = 0.15
    exit_apr: float = 0.05
    n_periods: int = 3
    max_basis: float = 0.01
    leg_weight: float = 0.25

    def __post_init__(self) -> None:
        if not 0 <= self.exit_apr < self.entry_apr:
            raise ValueError("need 0 <= exit_apr < entry_apr")
        if self.n_periods < 1 or self.max_basis <= 0 or self.leg_weight <= 0:
            raise ValueError("n_periods, max_basis and leg_weight must be positive")


def valid(p: dict[str, float]) -> bool:
    return 0 <= p["exit_apr"] < p["entry_apr"]


def annualized(rates: pd.Series, interval_hours: pd.Series) -> pd.Series:
    return rates * (24 / interval_hours) * 365


def hysteresis(signal: pd.Series, *, entry: float, exit: float) -> list[bool]:
    """On at >= entry, off below exit; NaN switches off (fail closed)."""
    on, out = False, []
    for x in signal:
        if math.isnan(x):
            on = False
        elif not on and x >= entry:
            on = True
        elif on and x < exit:
            on = False
        out.append(on)
    return out


class FundingCarry:
    name = "c1_carry"
    decision_interval = pd.Timedelta(hours=1)

    def __init__(
        self, params: CarryParams | None = None, symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    ) -> None:
        self.params, self.symbols = params or CarryParams(), symbols

    def requests(self) -> list[DataRequest]:
        reqs = []
        for s in self.symbols:
            reqs += [DataRequest(FUNDING, s, pd.Timedelta(days=30)),  # replay window for the hysteresis
                     DataRequest(H1_SPOT, s, pd.Timedelta(hours=3)),
                     DataRequest(H1_PERP, s, pd.Timedelta(hours=3))]  # fmt: skip
        return reqs

    def instruments(self) -> list[Instrument]:
        return [Instrument(s, m) for s in self.symbols for m in (Market.SPOT, Market.PERP)]

    def _on(self, snap: Snapshot, symbol: str) -> bool:
        p = self.params
        f = snap.get(FUNDING, symbol)
        if len(f) < p.n_periods:
            return False
        apr = annualized(f["rate"], f["interval_hours"]).rolling(p.n_periods).mean()
        if not hysteresis(apr, entry=p.entry_apr, exit=p.exit_apr)[-1]:
            return False
        spot = fresh_last_close(snap.get(H1_SPOT, symbol), H1_SPOT, snap.as_of)
        perp = fresh_last_close(snap.get(H1_PERP, symbol), H1_PERP, snap.as_of)
        return spot is not None and perp is not None and abs(perp / spot - 1) <= p.max_basis

    def decide(self, snap: Snapshot) -> list[Target]:
        out = []
        for s in self.symbols:
            w = self.params.leg_weight if self._on(snap, s) else 0.0
            group = f"carry:{s}"
            out += [Target(Instrument(s, Market.SPOT), w, group, "funding carry"),
                    Target(Instrument(s, Market.PERP), -w, group, "funding carry")]  # fmt: skip
        return out
