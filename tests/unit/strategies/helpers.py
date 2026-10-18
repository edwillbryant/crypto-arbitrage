"""Hand-built snapshots for strategy unit tests (synthetic; code-path checks only)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from agent.data.datasets import FUNDING, INTERVALS, METRICS, Dataset, klines
from agent.data.parse import TS_DTYPE
from agent.strategies.base import Snapshot


def bars(dataset: Dataset, end: pd.Timestamp, closes: list[float], *, highs: list[float] | None = None,
         lows: list[float] | None = None) -> pd.DataFrame:  # fmt: skip
    """Bars whose last one closes exactly at `end` (i.e. it is the newest bar available at `end`)."""
    step = INTERVALS[dataset.interval or "1h"]
    n = len(closes)
    key = pd.Series(pd.date_range(end=end - step, periods=n, freq=step)).astype(TS_DTYPE)
    c = np.asarray(closes, dtype="float64")
    o = np.r_[c[0], c[:-1]]
    h = np.asarray(highs, dtype="float64") if highs is not None else np.maximum(o, c)
    lo = np.asarray(lows, dtype="float64") if lows is not None else np.minimum(o, c)
    df = pd.DataFrame({"key_time": key, "available_at": (key + step).astype(TS_DTYPE), "open": o, "high": h,
                       "low": lo, "close": c, "volume": 1.0, "quote_volume": c, "trades": 1,
                       "taker_buy_volume": 0.5, "taker_buy_quote_volume": c / 2})  # fmt: skip
    df["close_time"] = (key + step - pd.Timedelta(milliseconds=1)).astype(TS_DTYPE)
    return df


def funding(end: pd.Timestamp, rates: list[float], interval_hours: int = 8) -> pd.DataFrame:
    """Funding prints, the last one settled exactly at `end`."""
    key = pd.Series(
        pd.date_range(end=end, periods=len(rates), freq=pd.Timedelta(hours=interval_hours))
    ).astype(TS_DTYPE)
    return pd.DataFrame(
        {"key_time": key, "available_at": key, "interval_hours": interval_hours, "rate": rates}
    )


def metrics(end: pd.Timestamp, oi: list[float]) -> pd.DataFrame:
    """Hourly OI snapshots (the store has 5-minute ones; hourly is enough for these tests)."""
    key = pd.Series(pd.date_range(end=end, periods=len(oi), freq="1h")).astype(TS_DTYPE)
    df = pd.DataFrame({"key_time": key, "available_at": key, "sum_open_interest": oi,
                       "sum_open_interest_value": oi})  # fmt: skip
    for c in METRICS.columns[2:]:
        df[c] = 1.0
    return df


def snapshot(as_of: pd.Timestamp, frames: dict[tuple[Dataset, str], pd.DataFrame]) -> Snapshot:
    for df in frames.values():
        assert (df["available_at"] <= as_of).all(), "test fixture leaks the future"
    return Snapshot(as_of, {(d.name, s): f for (d, s), f in frames.items()})


H1_PERP, H1_SPOT, D1_PERP = klines("perp", "1h"), klines("spot", "1h"), klines("perp", "1d")
__all__ = ["D1_PERP", "FUNDING", "H1_PERP", "H1_SPOT", "METRICS", "bars", "funding", "metrics", "snapshot"]
