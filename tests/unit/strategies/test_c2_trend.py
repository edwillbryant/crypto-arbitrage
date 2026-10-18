from __future__ import annotations

import numpy as np
import pandas as pd

from agent.strategies.c2_trend import Trend, TrendParams, trend_positions
from tests.unit.strategies.helpers import D1_PERP, bars, snapshot

T = pd.Timestamp("2024-06-01", tz="UTC")


def _decide(closes: list[float], **kw: object) -> float:
    s = snapshot(T, {(D1_PERP, "BTCUSDT"): bars(D1_PERP, T, closes)})
    return Trend(TrendParams(**kw), symbols=("BTCUSDT",)).decide(s)[0].weight  # type: ignore[arg-type]


FLAT = [100.0 + (i % 2) for i in range(60)]


def test_breakout_up_goes_long_and_down_goes_short() -> None:
    assert _decide([*FLAT, 110.0], n=20, k_atr=3.0) == 1.0
    assert _decide([*FLAT, 90.0], n=20, k_atr=3.0) == -1.0
    assert _decide([*FLAT, 100.5], n=20, k_atr=3.0) == 0.0


def test_long_only_ignores_downside_breakouts() -> None:
    assert _decide([*FLAT, 90.0], n=20, k_atr=3.0, allow_short=False) == 0.0


def test_trailing_stop_exits() -> None:
    up = [*FLAT, 110.0, 115.0, 120.0]
    assert _decide(up, n=20, k_atr=3.0) == 1.0
    # ATR here is ~1-5; dropping 20 from the 120 peak is well beyond 3 ATR but still above the 20-day low
    assert _decide([*up, 100.5], n=20, k_atr=3.0) == 0.0


def test_not_enough_history_is_flat() -> None:
    assert _decide(FLAT[:15], n=20, k_atr=3.0) == 0.0


def test_replay_converges_regardless_of_window_start() -> None:
    """State is rebuilt from history; once a window contains a flat period, the start doesn't matter."""
    rng = np.random.default_rng(7)
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 900))))
    h, lo = c * 1.01, c * 0.99
    full = trend_positions(c, h, lo, TrendParams(n=20, k_atr=3.0))
    late = trend_positions(c[300:].reset_index(drop=True), h[300:].reset_index(drop=True),
                           lo[300:].reset_index(drop=True), TrendParams(n=20, k_atr=3.0))  # fmt: skip
    agree = full[300:].to_numpy() == late.to_numpy()
    assert agree[200:].all()  # identical after a warm-up
