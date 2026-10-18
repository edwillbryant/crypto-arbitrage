"""Strategy interface. Strategies are pure: they see a point-in-time Snapshot and return targets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from agent.core.types import Instrument, Target
from agent.data.datasets import Dataset


@dataclass(frozen=True)
class DataRequest:
    dataset: Dataset
    symbol: str
    lookback: pd.Timedelta


@dataclass(frozen=True)
class Snapshot:
    """Everything a strategy may know at `as_of`. Every row satisfies available_at <= as_of."""

    as_of: pd.Timestamp
    data: Mapping[tuple[str, str], pd.DataFrame]

    def get(self, dataset: Dataset, symbol: str) -> pd.DataFrame:
        try:
            return self.data[(dataset.name, symbol)]
        except KeyError:
            raise KeyError(f"{dataset.name} {symbol} was not requested by the strategy") from None


class Strategy(Protocol):
    name: str
    decision_interval: pd.Timedelta

    def requests(self) -> list[DataRequest]: ...

    def instruments(self) -> list[Instrument]: ...

    def decide(self, snap: Snapshot) -> list[Target]: ...
