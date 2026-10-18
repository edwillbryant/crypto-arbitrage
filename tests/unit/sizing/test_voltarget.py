from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from agent.core.types import Instrument, Target
from agent.sizing.voltarget import VolTargetSizer, realized_vol

BTC = Instrument("BTCUSDT", "perp")
ETH = Instrument("ETHUSDT", "perp")
SPOT = Instrument("BTCUSDT", "spot")


def _closes(daily_sigma: float, n: int = 400, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, daily_sigma, n))))


def test_realized_vol_annualises_by_bar_length() -> None:
    c = _closes(0.02, n=5000)
    assert realized_vol(c, pd.Timedelta("1d")) == pytest.approx(0.02 * math.sqrt(365), rel=0.05)


def test_directional_weight_scaled_to_target_and_capped() -> None:
    sizer = VolTargetSizer(target_vol=0.10, max_weight=0.25, bar=pd.Timedelta("1d"), lookback_bars=365)
    vol = 0.02 * math.sqrt(365)
    out = sizer.size([Target(BTC, 1.0)], {BTC.key: _closes(0.02, n=5000)})
    assert out[0].weight == pytest.approx(min(0.10 / vol, 0.25), rel=0.06)
    quiet = sizer.size([Target(BTC, 1.0)], {BTC.key: _closes(0.001, n=400)})
    assert quiet[0].weight == pytest.approx(0.25)


def test_portfolio_target_is_split_across_directional_positions() -> None:
    sizer = VolTargetSizer(target_vol=0.10, max_weight=1.0, bar=pd.Timedelta("1d"), lookback_bars=365)
    closes = {BTC.key: _closes(0.03, seed=1), ETH.key: _closes(0.03, seed=2)}
    one = sizer.size([Target(BTC, 1.0)], closes)[0].weight
    two = sizer.size([Target(BTC, 1.0), Target(ETH, -1.0)], closes)
    assert two[0].weight == pytest.approx(one / 2, rel=0.2)
    assert two[1].weight < 0


def test_hedged_groups_pass_through_and_flat_stays_flat() -> None:
    sizer = VolTargetSizer()
    out = sizer.size(
        [Target(SPOT, 0.2, group="carry"), Target(BTC, -0.2, group="carry"), Target(ETH, 0.0)], {}
    )
    assert [t.weight for t in out] == [0.2, -0.2, 0.0]


def test_missing_or_short_history_means_no_position() -> None:
    sizer = VolTargetSizer(lookback_bars=100)
    assert sizer.size([Target(BTC, 1.0)], {})[0].weight == 0.0
    assert sizer.size([Target(BTC, 1.0)], {BTC.key: _closes(0.02, n=20)})[0].weight == 0.0
