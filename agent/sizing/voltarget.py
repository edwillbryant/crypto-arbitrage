"""Position sizing by volatility targeting. Never Kelly-optimal sizing: the edge estimate is too noisy."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from agent.core.types import Target

YEAR = pd.Timedelta(days=365)  # crypto trades every day


def realized_vol(closes: pd.Series, bar: pd.Timedelta) -> float:
    """Annualised standard deviation of log returns."""
    r = np.diff(np.log(closes.to_numpy(dtype="float64")))
    if len(r) < 2:
        return math.nan
    return float(np.std(r, ddof=1) * math.sqrt(YEAR / bar))


class Sizer(Protocol):
    def size(self, targets: list[Target], closes: Mapping[str, pd.Series]) -> list[Target]: ...


class PassThroughSizer:
    """Uses strategy weights as-is. For tests and for strategies that size themselves."""

    def size(self, targets: list[Target], closes: Mapping[str, pd.Series]) -> list[Target]:
        return list(targets)


@dataclass(frozen=True)
class VolTargetSizer:
    """Scales directional targets so the portfolio aims at `target_vol` annualised.

    The budget is split evenly across active directional positions, which is conservative unless the
    positions are perfectly correlated. Hedged groups (e.g. carry legs) pass through unchanged and are
    bounded by the risk gate instead. No history means no position.
    """

    target_vol: float = 0.10
    max_weight: float = 0.25
    bar: pd.Timedelta = pd.Timedelta("1h")
    lookback_bars: int = 30 * 24

    def size(self, targets: list[Target], closes: Mapping[str, pd.Series]) -> list[Target]:
        directional = [t for t in targets if t.group is None and t.weight != 0]
        budget = self.target_vol / max(len(directional), 1)
        out = []
        for t in targets:
            if t.group is not None or t.weight == 0:
                out.append(t)
                continue
            c = closes.get(t.instrument.key)
            vol = realized_vol(c.iloc[-(self.lookback_bars + 1) :], self.bar) if c is not None else math.nan
            if c is None or len(c) < self.lookback_bars + 1 or not vol > 0:
                out.append(t.with_weight(0.0))
                continue
            w = t.weight * budget / vol
            out.append(t.with_weight(max(-self.max_weight, min(self.max_weight, w))))
        return out
