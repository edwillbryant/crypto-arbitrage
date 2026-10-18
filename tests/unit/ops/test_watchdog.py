from __future__ import annotations

import pandas as pd

from agent.execution.types import OrderType
from agent.ops.watchdog import Watchdog
from agent.execution.types import Order
from tests.unit.execution.fakes import BTC_PERP, BTC_SPOT, T, paper


def test_silence_triggers_one_cancel_and_reduce_only_flatten() -> None:
    b = paper()
    b.place(Order("p", BTC_PERP, 0.01, OrderType.LIMIT_IOC, 60_000.0, False))
    b.place(Order("s", BTC_SPOT, 0.01, OrderType.LIMIT_IOC, 60_000.0, False))
    cancels: list[bool] = []
    b.cancel_all = lambda: cancels.append(True)  # type: ignore[method-assign]
    wd = Watchdog(b, silence=pd.Timedelta(seconds=60))
    wd.heartbeat(T)
    assert wd.check(T + pd.Timedelta(seconds=59)) is None
    action = wd.check(T + pd.Timedelta(seconds=61))
    assert action is not None and cancels == [True]
    # Everything goes flat. Flattening only the perp would turn a hedged carry pair into a naked spot long.
    assert b.positions() == {}
    assert {o.reduce_only for o in action.orders if o.instrument.market == "perp"} == {True}
    assert wd.check(T + pd.Timedelta(seconds=120)) is None  # latched: acts once per outage
    wd.heartbeat(T + pd.Timedelta(seconds=130))
    assert wd.check(T + pd.Timedelta(seconds=150)) is None
