"""C3: buy after liquidation cascades (hourly).

Edge: forced liquidations are price-insensitive and overshoot; liquidity providers get paid to absorb them.
Other side: the liquidation engine selling levered longs.
Cascade condition at bar i: over `window_h` hours, open interest fell >= oi_drop AND the log return is
<= -z sigma (sigma from the week before the move) AND the last funding rate is above min_funding (not a
market already paying heavily to be short).
Entry (long only), with confirmation: the condition held on the previous bar and this bar closes ABOVE it,
i.e. the forced selling has paused. Entering on the first cascade bar gets stopped out as it continues.
Exit: price recovers `revert_frac` of the drop, falls a further `stop_mult` x the drop, or `max_hold_h`.
The stop is sized to the move itself, not to pre-crash volatility, which is far too small a scale.
Bear case: catching knives; the sample is small and clustered in a few violent episodes; fills during a
cascade are worse than any bar-based model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from agent.core.types import Instrument, Target
from agent.data.datasets import FUNDING, METRICS, Market, klines
from agent.strategies.base import DataRequest, Snapshot

H1_PERP = klines("perp", "1h")
GRID: dict[str, list[Any]] = {
    "oi_drop": [0.03, 0.05, 0.08],
    "z": [2.0, 3.0, 4.0],
    "window_h": [1, 4],
    "max_hold_h": [12, 24, 48],
}


@dataclass(frozen=True)
class CascadeParams:
    oi_drop: float = 0.05
    z: float = 3.0
    window_h: int = 4
    max_hold_h: int = 24
    revert_frac: float = 0.5
    stop_mult: float = 0.5
    min_funding: float = -0.0005
    vol_lookback_h: int = 168

    def __post_init__(self) -> None:
        if not (0 < self.oi_drop < 1 and self.z > 0 and self.window_h >= 1 and self.max_hold_h >= 1):
            raise ValueError("invalid cascade parameters")


def _ns(col: pd.Series) -> np.ndarray:
    return col.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]").view("int64")


def _asof(times: pd.Series, source: pd.DataFrame, column: str) -> np.ndarray:
    """Latest `column` value with key_time <= each time (NaN if none). Source must be sorted by key_time."""
    if source.empty:
        return np.full(len(times), np.nan)
    idx = np.searchsorted(_ns(source["key_time"]), _ns(times), side="right") - 1
    values = source[column].to_numpy("float64")
    return np.where(idx >= 0, values[np.clip(idx, 0, None)], np.nan)


def cascade_positions(
    bars: pd.DataFrame, oi: pd.DataFrame, funding: pd.DataFrame, p: CascadeParams
) -> np.ndarray:
    close_time = bars["available_at"].reset_index(drop=True)
    c = bars["close"].to_numpy("float64")
    w = p.window_h
    oi_v = _asof(close_time, oi, "sum_open_interest")
    fr = _asof(close_time, funding, "rate")
    r1 = pd.Series(np.log(c)).diff()
    sigma = (r1.rolling(p.vol_lookback_h).std().shift(w) * math.sqrt(w)).to_numpy()
    ret_w = np.r_[np.full(w, np.nan), np.log(c[w:] / c[:-w])]
    oi_chg = np.r_[np.full(w, np.nan), oi_v[w:] / oi_v[:-w] - 1]
    with np.errstate(invalid="ignore"):  # NaNs compare False: no data, no trade
        cascade = (oi_chg <= -p.oi_drop) & (ret_w <= -p.z * sigma) & (fr > p.min_funding)

    pos = np.zeros(len(c))
    state = None  # (entry_index, target, stop)
    for i in range(w + 1, len(c)):
        if state is not None:
            entry_i, target, stop = state
            if c[i] >= target or c[i] <= stop or i - entry_i >= p.max_hold_h:
                state = None
                continue  # never re-enter on the exit bar
            pos[i] = 1.0
        elif cascade[i - 1] and c[i] > c[i - 1]:
            drop = c[i - 1 - w] - c[i]  # from before the move to the entry price
            if drop > 0:
                state = (i, c[i] + p.revert_frac * drop, c[i] - p.stop_mult * drop)
                pos[i] = 1.0
    return pos


class Cascade:
    name = "c3_cascade"
    decision_interval = pd.Timedelta(hours=1)

    def __init__(
        self, params: CascadeParams | None = None, symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    ) -> None:
        self.params, self.symbols = params or CascadeParams(), symbols

    def requests(self) -> list[DataRequest]:
        lb = pd.Timedelta(days=10)  # > vol lookback + window + max hold, so the replay converges
        return [DataRequest(d, s, lb) for s in self.symbols for d in (H1_PERP, METRICS, FUNDING)]

    def instruments(self) -> list[Instrument]:
        return [Instrument(s, Market.PERP) for s in self.symbols]

    def decide(self, snap: Snapshot) -> list[Target]:
        out = []
        for s in self.symbols:
            b, oi, f = snap.get(H1_PERP, s), snap.get(METRICS, s), snap.get(FUNDING, s)
            ok = len(b) > self.params.vol_lookback_h + self.params.window_h and not oi.empty
            ok = ok and b["available_at"].iloc[-1] >= snap.as_of - pd.Timedelta(hours=1)
            w = float(cascade_positions(b, oi, f, self.params)[-1]) if ok else 0.0
            out.append(Target(Instrument(s, Market.PERP), w, None, "cascade fade"))
        return out
