"""Monitor-only mode: watch the whole universe, never trade.

Used for paper trading with every strategy switched off (M4 found nothing deployable). The full loop
runs: feed, health, RiskGate, order manager (flat targets), reconciliation, kill switch and logs. So
the infrastructure is exercised for weeks before any strategy is allowed near it.
"""

from __future__ import annotations

import pandas as pd

from agent.core.types import Instrument, Target
from agent.data.datasets import Market
from agent.strategies.base import DataRequest, Snapshot


class MonitorOnly:
    name = "monitor_only"
    decision_interval = pd.Timedelta(minutes=1)

    def __init__(self, symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")) -> None:
        self.symbols = symbols

    def requests(self) -> list[DataRequest]:
        return []

    def instruments(self) -> list[Instrument]:
        return [Instrument(s, m) for s in self.symbols for m in (Market.SPOT, Market.PERP)]

    def decide(self, snap: Snapshot) -> list[Target]:
        return []  # the loop fills in flat targets for every instrument
