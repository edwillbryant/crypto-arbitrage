from __future__ import annotations

import pandas as pd

from agent.strategies.base import Snapshot
from agent.strategies.monitor import MonitorOnly
from agent.strategies.registry import REGISTRY


def test_monitor_only_watches_the_universe_and_never_trades() -> None:
    m = MonitorOnly()
    assert {i.key for i in m.instruments()} == {
        "spot:BTCUSDT",
        "perp:BTCUSDT",
        "spot:ETHUSDT",
        "perp:ETHUSDT",
    }
    assert m.decide(Snapshot(pd.Timestamp("2026-10-22", tz="UTC"), {})) == []
    assert "monitor_only" not in REGISTRY  # not a researchable or lockable strategy
