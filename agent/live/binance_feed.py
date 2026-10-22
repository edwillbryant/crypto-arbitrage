"""Binance market data over public REST (polling), implementing the MarketFeed interface.

One `advance(now)` per cycle refreshes server time (clock drift), top of book, recent klines and funding.
Every failure is collected (drained by the loop into the API-error count) and never raised: the old data
is kept and its age shows up as staleness, so the RiskGate reacts and the loop doesn't crash.
Only closed bars are cached or served: a bar still forming when fetched is discarded, never stored as partial.
WebSocket streaming is a later optimisation; at one cycle a minute this uses ~15 requests/min.
"""

from __future__ import annotations

import json
import math
import urllib.request
from collections.abc import Callable
from typing import Any

import pandas as pd

from agent.core.types import Instrument, TopOfBook
from agent.data.datasets import FUNDING, INTERVALS, Dataset, Market, klines
from agent.data.parse import TS_DTYPE, parse_csv
from agent.strategies.base import DataRequest, Snapshot

FUTURES_MAINNET = "https://fapi.binance.com"
SPOT_MAINNET = "https://api.binance.com"
FUTURES_TESTNET = "https://testnet.binancefuture.com"
BOOTSTRAP_BARS, REFRESH_BARS = 1000, 5


def http_get_json(url: str, timeout: float = 10.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


class BinanceRestFeed:
    def __init__(
        self,
        *,
        instruments: list[Instrument],
        get: Callable[[str], Any] = http_get_json,
        local_now: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC"),
        futures_base: str = FUTURES_MAINNET,
        spot_base: str = SPOT_MAINNET,
        exec_interval: str = "1h",
        depeg_symbol: str = "USDCUSDT",
    ) -> None:
        self.instruments, self.get, self.local_now = instruments, get, local_now
        self.futures_base, self.spot_base = futures_base.rstrip("/"), spot_base.rstrip("/")
        self.exec_interval, self.depeg_symbol = exec_interval, depeg_symbol
        self.now: pd.Timestamp | None = None
        self._books: dict[str, TopOfBook] = {}
        self._bars: dict[
            tuple[str, str], pd.DataFrame
        ] = {}  # (dataset name, symbol) -> bars incl. forming bar
        self._funding: dict[str, pd.DataFrame] = {}
        self._errors: list[str] = []
        self._drift_ms = 0.0
        self._depeg = 0.0

    # -- polling ---------------------------------------------------------------------------------------
    def _fetch(self, url: str) -> Any:
        try:
            return self.get(url)
        except Exception as e:  # broad on purpose: every failure is an API error for the RiskGate
            path = url.split("://", 1)[-1].split("/", 1)[-1]
            self._errors.append(f"GET /{path}: {type(e).__name__}: {e}")
            return None

    def _base(self, market: Market) -> str:
        return self.futures_base if market is Market.PERP else self.spot_base

    def _api(self, market: Market) -> str:
        return "fapi/v1" if market is Market.PERP else "api/v3"

    def advance(self, now: pd.Timestamp) -> None:
        self.now = now
        t = self._fetch(f"{self.futures_base}/fapi/v1/time")
        if t is not None:
            self._drift_ms = (
                abs((self.local_now() - pd.Timestamp(t["serverTime"], unit="ms", tz="UTC")).total_seconds())
                * 1e3
            )
        for inst in self.instruments:
            self._poll_book(inst)
            self._poll_bars(inst)
            if inst.market is Market.PERP:
                self._poll_funding(inst)
        q = self._fetch(f"{self.futures_base}/fapi/v1/ticker/bookTicker?symbol={self.depeg_symbol}")
        if q is not None:
            self._depeg = abs(1 - (float(q["bidPrice"]) + float(q["askPrice"])) / 2)

    def _poll_book(self, inst: Instrument) -> None:
        q = self._fetch(
            f"{self._base(inst.market)}/{self._api(inst.market)}/ticker/bookTicker?symbol={inst.symbol}"
        )
        if q is None:
            return
        ts = (
            pd.Timestamp(q["time"], unit="ms", tz="UTC") if "time" in q else self.local_now()
        )  # spot has no time
        self._books[inst.key] = TopOfBook(ts, float(q["bidPrice"]), float(q["bidQty"]), float(q["askPrice"]),
                                          float(q["askQty"]))  # fmt: skip

    def _poll_bars(self, inst: Instrument) -> None:
        ds = klines(inst.market, self.exec_interval)
        key = (ds.name, inst.symbol)
        limit = REFRESH_BARS if key in self._bars else BOOTSTRAP_BARS
        query = f"symbol={inst.symbol}&interval={self.exec_interval}&limit={limit}"
        rows = self._fetch(f"{self._base(inst.market)}/{self._api(inst.market)}/klines?{query}")
        if not rows:
            return
        fresh = parse_csv(ds, ("\n".join(",".join(str(v) for v in r) for r in rows) + "\n").encode())
        # Never cache the bar still forming: once its nominal close passes it would look "closed"
        # while holding partial data (and stay that way if later polls fail).
        fresh = fresh[fresh["available_at"] <= self.local_now()]
        old = self._bars.get(key)
        merged = fresh if old is None else pd.concat([old, fresh]).drop_duplicates("key_time", keep="last")
        self._bars[key] = merged.sort_values("key_time").reset_index(drop=True).tail(BOOTSTRAP_BARS + 50)

    def _poll_funding(self, inst: Instrument) -> None:
        rows = self._fetch(f"{self.futures_base}/fapi/v1/fundingRate?symbol={inst.symbol}&limit=100")
        if not rows:
            return
        key = pd.Series(pd.to_datetime([int(r["fundingTime"]) for r in rows], unit="ms", utc=True)).dt.round(
            "min"
        )
        df = pd.DataFrame({"key_time": key.astype(TS_DTYPE), "rate": [float(r["fundingRate"]) for r in rows]})
        df["available_at"] = df["key_time"]
        gap_hours = df["key_time"].diff().dropna().dt.total_seconds() / 3600
        df["interval_hours"] = round(float(gap_hours.median())) if len(gap_hours) else 8
        old = self._funding.get(inst.symbol)
        merged = df if old is None else pd.concat([old, df]).drop_duplicates("key_time", keep="last")
        self._funding[inst.symbol] = merged.sort_values("key_time").reset_index(drop=True)

    def drain_errors(self) -> list[str]:
        out, self._errors = self._errors, []
        return out

    # -- MarketFeed reads ------------------------------------------------------------------------------
    def _pit(self, df: pd.DataFrame, now: pd.Timestamp, lookback: pd.Timedelta) -> pd.DataFrame:
        return df[(df["available_at"] <= now) & (df["key_time"] >= now - lookback)].reset_index(drop=True)

    def _dataset_frame(self, ds: Dataset, symbol: str) -> pd.DataFrame | None:
        if ds == FUNDING:
            return self._funding.get(symbol)
        return self._bars.get((ds.name, symbol))

    def snapshot(self, requests: list[DataRequest], now: pd.Timestamp) -> Snapshot:
        data = {}
        for r in requests:
            df = self._dataset_frame(r.dataset, r.symbol)
            if df is not None:  # unsupported datasets are simply absent: the strategy sees missing data
                data[(r.dataset.name, r.symbol)] = self._pit(df, now, r.lookback)
        return Snapshot(now, data)

    def mark(self, instrument: Instrument) -> float | None:
        b = self._books.get(instrument.key)
        return None if b is None else (b.bid + b.ask) / 2

    def book(self, instrument: Instrument) -> TopOfBook | None:
        return self._books.get(instrument.key)

    def closes(self, instrument: Instrument, lookback: pd.Timedelta, now: pd.Timestamp) -> pd.Series:
        df = self._bars.get((klines(instrument.market, self.exec_interval).name, instrument.symbol))
        return pd.Series(dtype="float64") if df is None else self._pit(df, now, lookback)["close"]

    def funding_settlements(
        self, instrument: Instrument, after: pd.Timestamp, upto: pd.Timestamp
    ) -> list[tuple[pd.Timestamp, float]]:
        f = self._funding.get(instrument.symbol)
        if f is None:
            return []
        f = f[(f["key_time"] > after) & (f["key_time"] <= upto)]
        return list(zip(f["key_time"], f["rate"].astype("float64"), strict=True))

    def book_age_s(self, now: pd.Timestamp) -> float:
        ages = [
            (now - self._books[i.key].ts).total_seconds() if i.key in self._books else math.inf
            for i in self.instruments
        ]
        return max(ages, default=0.0)

    def bars_late(self, now: pd.Timestamp) -> int:
        bar = INTERVALS[self.exec_interval]
        expected = now.floor(bar) - bar  # open time of the newest bar that should have closed
        worst = 0
        for inst in self.instruments:
            df = self._bars.get((klines(inst.market, self.exec_interval).name, inst.symbol))
            closed = None if df is None else df[df["available_at"] <= now]
            if closed is None or closed.empty:
                return 99
            worst = max(worst, int((expected - closed["key_time"].iloc[-1]) / bar))
        return worst

    def depeg(self) -> float:
        return self._depeg

    def clock_drift_ms(self) -> float:
        return self._drift_ms
