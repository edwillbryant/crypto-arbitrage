"""C2: time-series trend following on daily perp bars (Donchian breakout + ATR trailing stop).

Edge: crypto trends persist over weeks (slow diffusion of information, herding).
Other side: mean-reverting discretionary traders and late sellers into strength.
Entry: close above the prior `n`-day high (long) / below the prior `n`-day low (short).
Exit: opposite breakout (flips), or a close beyond `k_atr` x ATR from the best close since entry.
Holding: weeks; 5-20 trades/yr/asset expected. Output is a direction; the sizer applies vol targeting.
Bear case: few independent trades -> wide confidence intervals; whipsaw regimes like late 2026.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from agent.core.types import Instrument, Target
from agent.data.datasets import Market, klines
from agent.strategies.base import DataRequest, Snapshot

D1_PERP = klines("perp", "1d")
GRID: dict[str, list[Any]] = {"n": [20, 50, 100, 200], "k_atr": [2.0, 3.0, 4.0, 5.0]}


@dataclass(frozen=True)
class TrendParams:
    n: int = 50
    k_atr: float = 3.0
    atr_n: int = 20
    allow_short: bool = True

    def __post_init__(self) -> None:
        if self.n < 2 or self.atr_n < 2 or self.k_atr <= 0:
            raise ValueError("n, atr_n >= 2 and k_atr > 0 required")


def trend_positions(close: pd.Series, high: pd.Series, low: pd.Series, p: TrendParams) -> pd.Series:
    """Replay the position state machine over the whole series. Position at i uses data up to bar i."""
    c = close.to_numpy("float64")
    hi_n = high.rolling(p.n).max().shift(1).to_numpy("float64")
    lo_n = low.rolling(p.n).min().shift(1).to_numpy("float64")
    prev_c = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev_c), np.abs(low - prev_c)]), axis=0)
    atr = (
        pd.Series(tr).rolling(p.atr_n).mean().shift(1).to_numpy("float64")
    )  # stop uses ATR known before the bar

    pos, best, out = 0, np.nan, np.zeros(len(c))
    for i in range(len(c)):
        up, down = c[i] > hi_n[i], c[i] < lo_n[i]  # False while the windows are NaN
        if pos == 1:
            best = max(best, c[i])
            if down or c[i] < best - p.k_atr * atr[i]:
                pos = -1 if down and p.allow_short else 0
                best = c[i]
        elif pos == -1:
            best = min(best, c[i])
            if up or c[i] > best + p.k_atr * atr[i]:
                pos = 1 if up else 0
                best = c[i]
        elif up:
            pos, best = 1, c[i]
        elif down and p.allow_short:
            pos, best = -1, c[i]
        out[i] = pos
    return pd.Series(out, index=close.index)


class Trend:
    name = "c2_trend"
    decision_interval = pd.Timedelta(days=1)

    def __init__(
        self, params: TrendParams | None = None, symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    ) -> None:
        self.params, self.symbols = params or TrendParams(), symbols

    def requests(self) -> list[DataRequest]:
        days = max(400, 4 * self.params.n)  # long replay so the state converges (see tests)
        return [DataRequest(D1_PERP, s, pd.Timedelta(days=days)) for s in self.symbols]

    def instruments(self) -> list[Instrument]:
        return [Instrument(s, Market.PERP) for s in self.symbols]

    def decide(self, snap: Snapshot) -> list[Target]:
        out = []
        for s in self.symbols:
            b = snap.get(D1_PERP, s)
            fresh = len(b) >= self.params.n + 2 and b["available_at"].iloc[-1] >= snap.as_of - pd.Timedelta(
                days=1
            )
            w = (
                float(trend_positions(b["close"], b["high"], b["low"], self.params).iloc[-1])
                if fresh
                else 0.0
            )
            out.append(Target(Instrument(s, Market.PERP), w, None, "trend"))
        return out
