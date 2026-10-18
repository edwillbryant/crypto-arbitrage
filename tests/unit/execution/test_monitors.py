from __future__ import annotations

import pandas as pd

from agent.execution.hedge import HedgeMonitor
from agent.execution.reconciler import Reconciler
from tests.unit.execution.fakes import BTC_PERP, BTC_SPOT, RULES, T


def test_reconciler_matches_and_mismatches() -> None:
    r = Reconciler(RULES)
    r.expect({BTC_PERP.key: 0.01})
    assert r.check({BTC_PERP.key: 0.01}, open_orders=[]).ok
    bad = r.check({BTC_PERP.key: 0.02}, open_orders=[])  # someone traded outside the agent
    assert not bad.ok and BTC_PERP.key in bad.detail
    assert not r.check(
        {BTC_PERP.key: 0.01}, open_orders=["unknown-order"]
    ).ok  # IOC/market leave nothing open
    assert r.check({BTC_PERP.key: 0.0100001}, open_orders=[]).ok  # within half a step


def test_reconciler_starts_from_the_exchange_state() -> None:
    r = Reconciler(RULES)
    assert r.check({BTC_PERP.key: 0.5}, open_orders=[]).ok  # first check adopts reality (after a restart)
    assert not r.check({BTC_PERP.key: 0.4}, open_orders=[]).ok


def test_hedge_monitor_needs_a_sustained_breach() -> None:
    h = HedgeMonitor(max_net_delta=0.02, grace=pd.Timedelta(seconds=30))
    groups = {"g": [BTC_SPOT.key, BTC_PERP.key]}
    marks = {BTC_SPOT.key: 100.0, BTC_PERP.key: 100.0}
    assert h.update(T, {BTC_SPOT.key: 1.0, BTC_PERP.key: -0.99}, marks, groups) == frozenset()  # 0.5% net
    off = {BTC_SPOT.key: 1.0, BTC_PERP.key: -0.9}  # ~5.3% net
    assert h.update(T, off, marks, groups) == frozenset()
    assert h.update(T + pd.Timedelta(seconds=29), off, marks, groups) == frozenset()
    assert h.update(T + pd.Timedelta(seconds=31), off, marks, groups) == frozenset({"g"})
    assert (
        h.update(T + pd.Timedelta(seconds=32), {BTC_SPOT.key: 1.0, BTC_PERP.key: -1.0}, marks, groups)
        == frozenset()
    )


def test_flat_groups_are_hedged() -> None:
    h = HedgeMonitor(max_net_delta=0.02, grace=pd.Timedelta(seconds=0))
    assert h.update(T, {}, {}, {"g": [BTC_SPOT.key, BTC_PERP.key]}) == frozenset()
