from __future__ import annotations

import pandas as pd

from agent.execution.types import Order, OrderType
from agent.ops.watchdog import Watchdog
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


class Flaky:
    """A broker that fails in the ways a broker fails during an outage."""

    def __init__(self, inner, *, cancel: bool = False, positions: bool = False, place_on: str = "") -> None:  # type: ignore[no-untyped-def]
        self.inner, self.fail_cancel, self.fail_positions, self.place_on = inner, cancel, positions, place_on

    def cancel_all(self) -> None:
        if self.fail_cancel:
            raise RuntimeError("cancel timed out")

    def positions(self) -> dict[str, float]:
        if self.fail_positions:
            raise RuntimeError("positions timed out")
        return self.inner.positions()  # type: ignore[no-any-return]

    def place(self, order):  # type: ignore[no-untyped-def]
        if order.instrument.key == self.place_on:
            raise RuntimeError("place timed out")
        return self.inner.place(order)


def _opened():  # type: ignore[no-untyped-def]
    b = paper()
    b.place(Order("p", BTC_PERP, 0.01, OrderType.LIMIT_IOC, 60_000.0, False))
    b.place(Order("s", BTC_SPOT, 0.01, OrderType.LIMIT_IOC, 60_000.0, False))
    return b


def test_a_failed_cancel_does_not_stop_the_flatten() -> None:
    b = _opened()
    wd = Watchdog(Flaky(b, cancel=True))
    wd.heartbeat(T)
    action = wd.check(T + pd.Timedelta(minutes=5))
    assert action is not None and "cancel_all failed" in action.errors[0]
    assert b.positions() == {}


def test_unreadable_positions_are_reported() -> None:
    wd = Watchdog(Flaky(_opened(), positions=True))
    wd.heartbeat(T)
    action = wd.check(T + pd.Timedelta(minutes=5))
    assert action is not None and action.orders == [] and "positions failed" in action.errors[-1]


def test_one_failed_close_does_not_stop_the_others() -> None:
    b = _opened()
    wd = Watchdog(Flaky(b, place_on=BTC_PERP.key))
    wd.heartbeat(T)
    action = wd.check(T + pd.Timedelta(minutes=5))
    assert action is not None and any(BTC_PERP.key in e for e in action.errors)
    assert BTC_SPOT.key not in b.positions() and BTC_PERP.key in b.positions()


def test_unarmed_watchdog_does_nothing() -> None:
    assert Watchdog(paper()).check(T) is None  # no heartbeat ever seen: nothing to compare against
