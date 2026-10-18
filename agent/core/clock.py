"""Clocks. Strategies never read the time themselves; they get it from a Clock."""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from agent.core.errors import LookAheadError


def utc(ts: pd.Timestamp | str) -> pd.Timestamp:
    """Return `ts` as a UTC timestamp. Naive timestamps are rejected rather than guessed."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError(f"timestamp {t} has no timezone; pass an explicit UTC timestamp")
    return t.tz_convert("UTC")


class Clock(Protocol):
    def now(self) -> pd.Timestamp: ...


class WallClock:
    def now(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")


class ReplayClock:
    """Backtest clock. It only moves forward."""

    def __init__(self, start: pd.Timestamp) -> None:
        self._now = utc(start)

    def now(self) -> pd.Timestamp:
        return self._now

    def advance_to(self, t: pd.Timestamp) -> None:
        t = utc(t)
        if t < self._now:
            raise LookAheadError(f"clock cannot move backwards from {self._now} to {t}")
        self._now = t
