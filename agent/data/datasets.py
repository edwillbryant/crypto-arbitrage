"""Dataset definitions: what a series is, how often it ticks, and when each row becomes knowable."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd


class Market(StrEnum):
    SPOT = "spot"
    PERP = "perp"  # Binance USDⓈ-M perpetual futures


class Kind(StrEnum):
    KLINES = "klines"
    FUNDING = "fundingRate"
    PREMIUM = "premiumIndexKlines"
    METRICS = "metrics"  # open interest and long/short ratios, 5-minute snapshots


BAR_KINDS = frozenset({Kind.KLINES, Kind.PREMIUM})
INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")

BAR_COLUMNS = ("open", "high", "low", "close", "volume", "quote_volume", "trades",
               "taker_buy_volume", "taker_buy_quote_volume", "close_time")  # fmt: skip
FUNDING_COLUMNS = ("interval_hours", "rate")
METRICS_COLUMNS = ("sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio",
                   "sum_toptrader_long_short_ratio", "count_long_short_ratio",
                   "sum_taker_long_short_vol_ratio")  # fmt: skip

# [ASSUMPTION] Binance does not document when a metrics snapshot is published relative to its
# create_time. We assume it is usable 5 minutes later; revisit once live collection measures it.
METRICS_LAG = pd.Timedelta(minutes=5)


@dataclass(frozen=True)
class Dataset:
    market: Market
    kind: Kind
    interval: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "market", Market(self.market))
        object.__setattr__(self, "kind", Kind(self.kind))
        if self.kind in BAR_KINDS:
            if self.interval not in INTERVALS:
                raise ValueError(f"{self.kind} needs an interval in {INTERVALS}, got {self.interval!r}")
        elif self.interval is not None:
            raise ValueError(f"{self.kind} takes no interval")
        if self.kind is not Kind.KLINES and self.market is not Market.PERP:
            raise ValueError(f"{self.kind} exists only for perpetual futures")

    @property
    def is_bars(self) -> bool:
        return self.kind in BAR_KINDS

    @property
    def name(self) -> str:
        return "/".join(p for p in (self.market.value, self.kind.value, self.interval) if p)

    @property
    def bar_length(self) -> pd.Timedelta:
        if not self.is_bars or self.interval is None:
            raise ValueError(f"{self.name} is not a bar dataset")
        return pd.Timedelta(self.interval)

    @property
    def cadence(self) -> pd.Timedelta | None:
        """Expected spacing between rows, or None when it varies (funding intervals differ by symbol/time)."""
        if self.is_bars:
            return self.bar_length
        if self.kind is Kind.METRICS:
            return pd.Timedelta(minutes=5)
        return None

    @property
    def publication_lag(self) -> pd.Timedelta:
        return METRICS_LAG if self.kind is Kind.METRICS else pd.Timedelta(0)

    @property
    def columns(self) -> tuple[str, ...]:
        if self.is_bars:
            return BAR_COLUMNS
        return FUNDING_COLUMNS if self.kind is Kind.FUNDING else METRICS_COLUMNS

    def availability(self, key_time: pd.Series) -> pd.Series:
        """Earliest time each row may be used. Bars: at their close. Others: at key_time + lag."""
        end = key_time + self.bar_length if self.is_bars else key_time
        return end + self.publication_lag


def klines(market: Market | str, interval: str) -> Dataset:
    return Dataset(Market(market), Kind.KLINES, interval)


FUNDING = Dataset(Market.PERP, Kind.FUNDING)
METRICS = Dataset(Market.PERP, Kind.METRICS)
PREMIUM_1H = Dataset(Market.PERP, Kind.PREMIUM, "1h")
