"""C5: time-of-day / day-of-week seasonality: a CONTROL, never eligible to trade.

There is no economic story here. With 144 variants, one of them will look good in-sample by chance. The
point of C5 is that the validation pipeline (neighbourhood check, hold-out, deflated Sharpe) must REJECT
it. If C5 passes, the filters are too loose and nothing else that passed can be trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from agent.core.types import Instrument, Target
from agent.data.datasets import Market
from agent.strategies.base import DataRequest, Snapshot

GRID: dict[str, list[Any]] = {
    "start_hour": list(range(24)),
    "length": [4, 8],
    "days": ["all", "weekdays", "weekends"],
}


@dataclass(frozen=True)
class SeasonParams:
    start_hour: int = 0
    length: int = 8
    days: Literal["all", "weekdays", "weekends"] = "all"

    def __post_init__(self) -> None:
        if not (
            0 <= self.start_hour < 24
            and 1 <= self.length < 24
            and self.days in ("all", "weekdays", "weekends")
        ):
            raise ValueError("invalid seasonality parameters")


class Seasonality:
    name = "c5_seasonality"
    decision_interval = pd.Timedelta(hours=1)

    def __init__(self, params: SeasonParams | None = None, symbol: str = "BTCUSDT") -> None:
        self.params, self.symbol = params or SeasonParams(), symbol

    def requests(self) -> list[DataRequest]:
        return []  # uses only the clock

    def instruments(self) -> list[Instrument]:
        return [Instrument(self.symbol, Market.PERP)]

    def decide(self, snap: Snapshot) -> list[Target]:
        p, t = self.params, snap.as_of
        weekend = t.weekday() >= 5
        day_ok = p.days == "all" or (p.days == "weekends") == weekend
        on = day_ok and (t.hour - p.start_hour) % 24 < p.length
        return [Target(Instrument(self.symbol, Market.PERP), 1.0 if on else 0.0, None, "seasonality control")]
