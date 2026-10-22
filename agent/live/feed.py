"""Market feeds for the live loop.

`StoreFeed` serves point-in-time data from the PITStore with a synthetic top of book around the last close.
It's used for tests and for replaying history through the live stack. The Binance WebSocket feed (needs
network) implements the same interface.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from agent.core.types import Instrument, TopOfBook
from agent.data.datasets import FUNDING, klines
from agent.data.store import PITStore
from agent.strategies.base import DataRequest, Snapshot


class MarketFeed(Protocol):
    def advance(self, now: pd.Timestamp) -> None: ...

    def snapshot(self, requests: list[DataRequest], now: pd.Timestamp) -> Snapshot: ...

    def mark(self, instrument: Instrument) -> float | None: ...

    def book(self, instrument: Instrument) -> TopOfBook | None: ...

    def closes(self, instrument: Instrument, lookback: pd.Timedelta, now: pd.Timestamp) -> pd.Series: ...

    def funding_settlements(
        self, instrument: Instrument, after: pd.Timestamp, upto: pd.Timestamp
    ) -> list[tuple[pd.Timestamp, float]]: ...

    def book_age_s(self, now: pd.Timestamp) -> float: ...

    def bars_late(self, now: pd.Timestamp) -> int: ...

    def depeg(self) -> float: ...

    def clock_drift_ms(self) -> float: ...

    def drain_errors(self) -> list[str]: ...


class StoreFeed:
    def __init__(
        self,
        store: PITStore,
        *,
        half_spread_bps: float = 1.0,
        depth: float = 100.0,
        exec_interval: str = "1h",
    ) -> None:
        self.store, self.half_spread, self.depth, self.exec_interval = (
            store,
            half_spread_bps / 1e4,
            depth,
            exec_interval,
        )
        self.now: pd.Timestamp | None = None
        self.book_age_override: float | None = None
        self.bars_late_override: int | None = None

    def advance(self, now: pd.Timestamp) -> None:
        self.now = now

    def _bars(self, instrument: Instrument, lookback: pd.Timedelta, now: pd.Timestamp) -> pd.DataFrame:
        return self.store.read(
            klines(instrument.market, self.exec_interval), instrument.symbol, as_of=now, lookback=lookback
        )

    def snapshot(self, requests: list[DataRequest], now: pd.Timestamp) -> Snapshot:
        data = {}
        for r in requests:
            data[(r.dataset.name, r.symbol)] = self.store.read(
                r.dataset, r.symbol, as_of=now, lookback=r.lookback
            )
        return Snapshot(now, data)

    def mark(self, instrument: Instrument) -> float | None:
        if self.now is None:
            return None
        b = self._bars(instrument, pd.Timedelta(hours=3), self.now)
        return None if b.empty else float(b["close"].iloc[-1])

    def book(self, instrument: Instrument) -> TopOfBook | None:
        m = self.mark(instrument)
        if m is None or self.now is None:
            return None
        return TopOfBook(
            self.now, m * (1 - self.half_spread), self.depth, m * (1 + self.half_spread), self.depth
        )

    def closes(self, instrument: Instrument, lookback: pd.Timedelta, now: pd.Timestamp) -> pd.Series:
        return self._bars(instrument, lookback, now)["close"]

    def funding_settlements(
        self, instrument: Instrument, after: pd.Timestamp, upto: pd.Timestamp
    ) -> list[tuple[pd.Timestamp, float]]:
        f = self.store.read(FUNDING, instrument.symbol, as_of=upto, lookback=upto - after)
        f = f[f["key_time"] > after]
        return list(zip(f["key_time"], f["rate"].astype("float64"), strict=True))

    def book_age_s(self, now: pd.Timestamp) -> float:
        return self.book_age_override if self.book_age_override is not None else 0.0

    def bars_late(self, now: pd.Timestamp) -> int:
        return self.bars_late_override if self.bars_late_override is not None else 0

    def depeg(self) -> float:
        return 0.0

    def clock_drift_ms(self) -> float:
        return 0.0

    def drain_errors(self) -> list[str]:
        return []
