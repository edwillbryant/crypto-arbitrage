from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
import pytest

from agent.core.types import Instrument, Target
from agent.data.datasets import FUNDING, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.strategies.base import DataRequest, Snapshot
from tests.fixtures.factories import funding_csv, kline_csv_from_prices

BTC_PERP = Instrument("BTCUSDT", "perp")
H1 = klines("perp", "1h")
T0 = pd.Timestamp("2024-01-01", tz="UTC")


def put_prices(store: PITStore, opens: list[float], closes: list[float], symbol: str = "BTCUSDT") -> None:
    store.write(H1, symbol, parse_csv(H1, kline_csv_from_prices("2024-01-01", "1h", opens, closes)))


def put_funding(store: PITStore, n: int, rate: float) -> None:
    store.write(FUNDING, "BTCUSDT", parse_csv(FUNDING, funding_csv("2024-01-01", n, rate=rate)))


@dataclass
class ScriptedStrategy:
    """Emits a fixed weight schedule {decision_time: weight} for BTC perp; records every snapshot it sees."""

    schedule: Callable[[pd.Timestamp], float]
    name: str = "scripted"
    decision_interval: pd.Timedelta = pd.Timedelta("1h")
    seen: list[Snapshot] = field(default_factory=list)

    def requests(self) -> list[DataRequest]:
        return [DataRequest(H1, "BTCUSDT", pd.Timedelta(days=1))]

    def instruments(self) -> list[Instrument]:
        return [BTC_PERP]

    def decide(self, snap: Snapshot) -> list[Target]:
        self.seen.append(snap)
        return [Target(BTC_PERP, self.schedule(snap.as_of))]


@pytest.fixture
def scripted() -> type[ScriptedStrategy]:
    return ScriptedStrategy
