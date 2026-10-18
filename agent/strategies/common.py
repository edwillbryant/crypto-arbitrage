"""Helpers shared by strategies."""

from __future__ import annotations

import pandas as pd

from agent.data.datasets import INTERVALS, Dataset


def fresh_last_close(bars: pd.DataFrame, dataset: Dataset, as_of: pd.Timestamp) -> float | None:
    """Close of the newest bar if it closed within one bar of `as_of`; otherwise None (fail closed)."""
    if bars.empty:
        return None
    last = bars.iloc[-1]
    if last["available_at"] < as_of - INTERVALS[dataset.interval or "1h"]:
        return None
    return float(last["close"])
