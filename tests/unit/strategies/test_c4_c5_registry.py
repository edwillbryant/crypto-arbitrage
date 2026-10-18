from __future__ import annotations

import pandas as pd
import pytest

from agent.strategies.c4_xarb import analyze
from agent.strategies.c5_seasonality import Seasonality, SeasonParams
from agent.strategies.registry import REGISTRY, variants
from agent.strategies.base import Snapshot

MONDAY_03 = pd.Timestamp("2024-03-04 03:00", tz="UTC")
SATURDAY_03 = pd.Timestamp("2024-03-09 03:00", tz="UTC")


def _w(t: pd.Timestamp, **kw: object) -> float:
    return Seasonality(SeasonParams(**kw), symbol="BTCUSDT").decide(Snapshot(t, {}))[0].weight  # type: ignore[arg-type]


def test_c5_window_wraps_midnight_and_filters_days() -> None:
    assert _w(MONDAY_03, start_hour=22, length=8) == 1.0
    assert _w(MONDAY_03, start_hour=4, length=8) == 0.0
    assert _w(MONDAY_03, start_hour=0, length=8, days="weekends") == 0.0
    assert _w(SATURDAY_03, start_hour=0, length=8, days="weekends") == 1.0
    assert _w(SATURDAY_03, start_hour=0, length=8, days="weekdays") == 0.0


def test_c4_counts_only_gaps_that_beat_all_costs() -> None:
    t = pd.date_range("2024-01-01", periods=4, freq="1min", tz="UTC")
    a = pd.Series([100.0, 100.0, 101.0, 100.0], index=t)
    b = pd.Series([100.0, 100.05, 100.0, 99.0], index=t)
    r = analyze(a, b, fee_bps=10.0, slippage_bps=3.0, transfer_bps=20.0)  # hurdle 2*(10+3)+20 = 46 bps
    assert r["observations"] == 4
    assert r["opportunities"] == 2  # +100 bps and +101 bps; the 5 bps gap doesn't clear the hurdle
    assert r["mean_net_edge_bps"] == pytest.approx(((100.0 - 46) + (101.0101 - 46)) / 2, abs=0.05)
    assert "upper bound" in r["caveat"]


def test_registry_trial_counts_are_fixed_in_advance() -> None:
    """These counts feed the deflated Sharpe's N. Changing a grid changes N and must be deliberate."""
    assert {k: len(variants(k)) for k in REGISTRY} == {
        "c1_carry": 33,
        "c2_trend": 16,
        "c3_cascade": 54,
        "c5_seasonality": 144,
    }


def test_every_variant_constructs() -> None:
    for name, spec in REGISTRY.items():
        for p in variants(name):
            s = spec.build(p)
            assert s.name == name and s.instruments()


def test_controls_and_research_only_code_can_never_go_live() -> None:
    assert not REGISTRY["c5_seasonality"].live_eligible
    assert "c4_xarb" not in REGISTRY  # an analyzer, not a Strategy: nothing to wire to an exchange
    assert {k for k, v in REGISTRY.items() if v.live_eligible} == {"c1_carry", "c2_trend", "c3_cascade"}
