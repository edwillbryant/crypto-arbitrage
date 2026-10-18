"""Fakes for execution tests."""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from agent.core.errors import AgentError
from agent.core.types import Instrument, TopOfBook
from agent.execution.paper import PaperBroker
from agent.execution.types import Ack, Order, SymbolRules

T = pd.Timestamp("2024-03-01 10:00", tz="UTC")
BTC_PERP = Instrument("BTCUSDT", "perp")
BTC_SPOT = Instrument("BTCUSDT", "spot")
ETH_PERP = Instrument("ETHUSDT", "perp")
RULES = {BTC_PERP.key: SymbolRules(step=0.001, tick=0.1, min_notional=100.0),
         BTC_SPOT.key: SymbolRules(step=0.00001, tick=0.01, min_notional=5.0),
         ETH_PERP.key: SymbolRules(step=0.001, tick=0.01, min_notional=20.0)}  # fmt: skip


class Books:
    """Scripted top-of-book per instrument key."""

    MIDS: ClassVar[dict[str, float]] = {BTC_PERP.key: 50_000.0, BTC_SPOT.key: 50_000.0, ETH_PERP.key: 2_000.0}

    def __init__(self, spread: float = 1.0, qty: float = 100.0) -> None:
        self.quotes: dict[str, TopOfBook] = {}
        for k in RULES:
            self.set(k, self.MIDS[k], spread, qty)

    def set(self, key: str, mid: float, spread: float = 1.0, qty: float = 100.0) -> None:
        self.quotes[key] = TopOfBook(T, mid - spread / 2, qty, mid + spread / 2, qty)

    def __call__(self, inst: Instrument) -> TopOfBook | None:
        return self.quotes.get(inst.key)


def paper(cash: float = 100_000.0, books: Books | None = None) -> PaperBroker:
    return PaperBroker(
        cash=cash, rules=RULES, book=books or Books(), taker_fee_bps={"perp": 5.0, "spot": 10.0}
    )


class FailingBroker:
    """Delegates to a paper broker, but chosen instruments raise or reject."""

    def __init__(
        self, inner: PaperBroker, *, raise_on: set[str] = frozenset(), reject_on: set[str] = frozenset()
    ) -> None:  # type: ignore[assignment]
        self.inner, self.raise_on, self.reject_on = inner, raise_on, reject_on
        self.placed: list[Order] = []

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self.inner, name)

    def place(self, order: Order) -> Ack:
        self.placed.append(order)
        if order.instrument.key in self.raise_on:
            raise AgentError("simulated API timeout")
        if order.instrument.key in self.reject_on:
            return Ack(order.client_id, "REJECTED", 0.0, 0.0, 0.0, "simulated reject")
        return self.inner.place(order)
