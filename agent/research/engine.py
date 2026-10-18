"""Event-driven backtest engine.

Each decision time t (a multiple of the strategy's decision interval):
  1. funding settled in (prev, t] is charged on the position held over that interval
  2. positions are marked at the last close available at t; equity is recorded
  3. the strategy sees a Snapshot of rows with available_at <= t and returns targets
  4. targets go through the Sizer and the risk gate (the same objects used live)
  5. orders fill at the open of the execution bar starting at t, plus costs

The fill price is the one deliberate exception to point-in-time reads: the open of the bar beginning at t
is the first traded price after the decision, which is what a live order would get (before slippage).
Only that bar's `open` is read; see `_Feed.open_at`. The last timestamp only marks; it never trades.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd

from agent.core.clock import utc
from agent.core.types import Instrument, Target
from agent.data.datasets import FUNDING, INTERVALS, Market, klines
from agent.data.store import PITStore
from agent.research.costs import CostModel
from agent.risk.exposure import Decision
from agent.sizing.voltarget import Sizer
from agent.strategies.base import Snapshot, Strategy

FILL_COLUMNS = ["time", "instrument", "qty", "price", "reference_price", "fee", "slippage_cost"]
FUNDING_COLUMNS = ["time", "instrument", "qty", "rate", "mark", "amount"]
EPISODE_COLUMNS = ["key", "open_time", "close_time", "pnl", "is_open"]
DECISION_COLUMNS = ["time", "instrument", "raw_weight", "sized_weight", "final_weight", "verdict", "rule"]


class Gate(Protocol):
    def check(self, targets: list[Target]) -> list[Decision]: ...


@dataclass(frozen=True)
class BacktestConfig:
    start: pd.Timestamp
    end: pd.Timestamp
    initial_equity: float = 10_000.0
    exec_interval: str = "1h"
    min_trade_weight: float = 0.01  # skip rebalances below this fraction of equity (going flat always trades)
    stale_bars: int = 3  # a mark older than this many execution bars blocks trading in that instrument
    vol_lookback: pd.Timedelta = pd.Timedelta(days=60)  # price history handed to the sizer
    require_funding: bool = True  # perp backtests without funding data are refused, not silently optimistic


@dataclass
class BacktestResult:
    strategy: str
    config: BacktestConfig
    equity: pd.Series
    gross_exposure: pd.Series
    fills: pd.DataFrame
    funding: pd.DataFrame
    episodes: pd.DataFrame
    decisions: pd.DataFrame
    events: list[str] = field(default_factory=list)


def _ns(col: pd.Series) -> np.ndarray:
    """UTC timestamps as int64 nanoseconds, for fast binary search."""
    return col.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]").view("int64")


class _Feed:
    """In-memory point-in-time view over one stored series."""

    def __init__(self, df: pd.DataFrame) -> None:
        if not df["available_at"].is_monotonic_increasing:
            raise ValueError("feed must be sorted by available_at")
        self.df = df.reset_index(drop=True)
        self._avail = _ns(self.df["available_at"])
        self._key = _ns(self.df["key_time"])
        self._key_pos = {k: i for i, k in enumerate(self.df["key_time"])}
        self._close = self.df["close"].to_numpy("float64") if "close" in self.df else None
        self._open = self.df["open"].to_numpy("float64") if "open" in self.df else None

    def _n_available(self, t: pd.Timestamp) -> int:
        return int(np.searchsorted(self._avail, t.value, side="right"))

    def upto(self, t: pd.Timestamp, lookback: pd.Timedelta) -> pd.DataFrame:
        hi = self._n_available(t)
        lo = int(np.searchsorted(self._key, (t - lookback).value, side="left"))
        return self.df.iloc[lo:hi]  # pandas copy-on-write: a strategy mutating this cannot touch the feed

    def last_close(self, t: pd.Timestamp) -> tuple[float, int] | None:
        """(close, available_at in ns) of the newest bar available at t."""
        n = self._n_available(t)
        if n == 0 or self._close is None:
            return None
        return float(self._close[n - 1]), int(self._avail[n - 1])

    def between(self, a: pd.Timestamp, b: pd.Timestamp) -> pd.DataFrame:
        """Rows with a < key_time <= b that are available by b."""
        lo = int(np.searchsorted(self._key, a.value, side="right"))
        hi = int(np.searchsorted(self._key, b.value, side="right"))
        rows = self.df.iloc[lo:hi]
        return rows[self._avail[lo:hi] <= b.value]

    def settlements(self, a: pd.Timestamp, b: pd.Timestamp) -> list[tuple[pd.Timestamp, float]]:
        """(key_time, rate) for funding rows with a < key_time <= b, available by b. Fast path when none."""
        lo = int(np.searchsorted(self._key, a.value, side="right"))
        hi = int(np.searchsorted(self._key, b.value, side="right"))
        if lo == hi:
            return []
        rows = self.df.iloc[lo:hi]
        ok = self._avail[lo:hi] <= b.value
        return list(zip(rows["key_time"][ok], rows["rate"][ok].astype("float64"), strict=True))

    def closes_upto(self, t: pd.Timestamp, lookback: pd.Timedelta) -> pd.Series:
        hi = self._n_available(t)
        lo = int(np.searchsorted(self._key, (t - lookback).value, side="left"))
        return self.df["close"].iloc[lo:hi]

    def open_at(self, t: pd.Timestamp) -> float | None:
        i = self._key_pos.get(t)
        return None if i is None or self._open is None else float(self._open[i])


class _Simulation:
    def __init__(
        self,
        strategy: Strategy,
        store: PITStore,
        config: BacktestConfig,
        *,
        sizer: Sizer,
        gate: Gate,
        costs: CostModel,
    ) -> None:
        self.strategy, self.config, self.sizer, self.gate, self.costs = strategy, config, sizer, gate, costs
        self.start, self.end = utc(config.start), utc(config.end)
        self.step = strategy.decision_interval
        self.bar = INTERVALS[config.exec_interval]
        self._validate()

        self.instruments = strategy.instruments()
        self.by_key = {i.key: i for i in self.instruments}
        end = self.end
        self.exec_feeds = {
            i.key: _Feed(store.read(klines(i.market, config.exec_interval), i.symbol, as_of=end))
            for i in self.instruments
        }
        self.funding_feeds: dict[str, _Feed] = {}
        for i in self.instruments:
            if i.market is Market.PERP:
                f = _Feed(store.read(FUNDING, i.symbol, as_of=end))
                if config.require_funding and f.between(self.start, end).empty:
                    raise ValueError(
                        f"no funding data for {i.symbol} in [{self.start}, {end}]; refusing to run"
                    )
                self.funding_feeds[i.key] = f
        self.req_feeds = {
            (r.dataset.name, r.symbol): (_Feed(store.read(r.dataset, r.symbol, as_of=end)), r.lookback)
            for r in strategy.requests()
        }

        self.cash = config.initial_equity
        self.qty: dict[str, float] = {}
        self.inst_cash: dict[str, float] = {}  # per-instrument cash flows, for PnL attribution
        self.last_mark: dict[str, float] = {}
        self.episode_key: dict[str, str] = {}  # instrument -> its hedge group, or itself
        self.open_episode: dict[str, dict[str, Any]] = {}
        self.events: list[str] = []
        self.fills: list[dict[str, Any]] = []
        self.funding_rows: list[dict[str, Any]] = []
        self.decision_rows: list[dict[str, Any]] = []
        self.episodes: list[dict[str, Any]] = []
        self.equity_pts: dict[pd.Timestamp, float] = {}
        self.exposure_pts: dict[pd.Timestamp, float] = {}

    def _validate(self) -> None:
        aligned = self.start.value % self.step.value == 0 and self.end.value % self.step.value == 0
        if not aligned:
            raise ValueError(f"start/end must be aligned to the decision interval {self.step}")
        if self.step.value % self.bar.value:
            raise ValueError(
                f"decision interval {self.step} must be a multiple of the execution bar {self.bar}"
            )
        if self.end <= self.start:
            raise ValueError("end must be after start")
        sizer_bar = getattr(self.sizer, "bar", None)
        if sizer_bar is not None and sizer_bar != self.bar:
            raise ValueError(f"sizer bar {sizer_bar} does not match execution bar {self.bar}")

    # -- accounting ----------------------------------------------------------------------------------------
    def _members(self, ek: str) -> list[str]:
        return [k for k, v in self.episode_key.items() if v == ek]

    def _book(self, k: str, flow: float) -> None:
        self.cash += flow
        self.inst_cash[k] = self.inst_cash.get(k, 0.0) + flow

    def _fill(self, t: pd.Timestamp, inst: Instrument, dq: float, ref: float, ek: str) -> None:
        k = inst.key
        group = self._members(ek)
        if all(self.qty.get(m, 0.0) == 0 for m in group):
            self.open_episode[ek] = {"open_time": t, "start": sum(self.inst_cash.get(m, 0.0) for m in group)}
        f = self.costs.fill(inst, dq, ref)
        self._book(k, -(dq * f.price) - f.fee)
        new_qty = self.qty.get(k, 0.0) + dq
        self.qty[k] = 0.0 if math.isclose(new_qty, 0.0, abs_tol=1e-12) else new_qty
        self.fills.append({"time": t, "instrument": k, "qty": dq, "price": f.price, "reference_price": ref,
                           "fee": f.fee, "slippage_cost": f.slippage_cost})  # fmt: skip
        if all(self.qty.get(m, 0.0) == 0 for m in group):
            ep = self.open_episode.pop(ek)
            pnl = sum(self.inst_cash.get(m, 0.0) for m in group) - ep["start"]
            self.episodes.append(
                {"key": ek, "open_time": ep["open_time"], "close_time": t, "pnl": pnl, "is_open": False}
            )

    # -- the five steps ------------------------------------------------------------------------------------
    def _apply_funding(self, prev: pd.Timestamp, t: pd.Timestamp) -> None:
        for k, feed in self.funding_feeds.items():
            q = self.qty.get(k, 0.0)
            if q == 0:
                continue
            for s, rate in feed.settlements(prev, t):
                mark = self.exec_feeds[k].last_close(s)
                if mark is None:
                    self.events.append(f"{s} {k}: funding settlement without a mark price; skipped")
                    continue
                amount = -q * mark[0] * float(rate)
                self._book(k, amount)
                self.funding_rows.append({"time": s, "instrument": k, "qty": q, "rate": float(rate),
                                          "mark": mark[0], "amount": amount})  # fmt: skip

    def _mark(self, t: pd.Timestamp) -> tuple[dict[str, float | None], float]:
        marks: dict[str, float | None] = {}
        for k, feed in self.exec_feeds.items():
            last = feed.last_close(t)
            fresh = last is not None and t.value - last[1] <= self.config.stale_bars * self.bar.value
            marks[k] = last[0] if fresh and last is not None else None
            if last is not None:
                self.last_mark[k] = last[0]
            if not fresh and self.qty.get(k, 0.0) != 0:
                self.events.append(f"{t} {k}: stale mark; valued at last known price, trading blocked")
        equity = self.cash + sum(q * self.last_mark.get(k, 0.0) for k, q in self.qty.items())
        gross = sum(abs(q) * self.last_mark.get(k, 0.0) for k, q in self.qty.items())
        self.equity_pts[t] = equity
        self.exposure_pts[t] = gross / equity if equity > 0 else math.inf
        return marks, equity

    def _decide(self, t: pd.Timestamp) -> tuple[list[Target], list[Target], list[Decision]]:
        snap = Snapshot(t, {key: feed.upto(t, lb) for key, (feed, lb) in self.req_feeds.items()})
        raw = self.strategy.decide(snap)
        seen = {x.instrument.key for x in raw}
        if len(seen) != len(raw) or not seen <= self.by_key.keys():
            raise ValueError(f"{t}: strategy returned duplicate or undeclared instruments {sorted(seen)}")
        targets = raw + [Target(i, 0.0) for i in self.instruments if i.key not in seen]
        closes = {k: self.exec_feeds[k].closes_upto(t, self.config.vol_lookback) for k in self.by_key}
        sized = self.sizer.size(targets, closes)
        return targets, sized, self.gate.check(sized)

    def _execute(self, t: pd.Timestamp, d: Decision, marks: dict[str, float | None], equity: float) -> None:
        tgt = d.target
        k = tgt.instrument.key
        if tgt.weight != 0:
            ek = self.episode_key.setdefault(k, tgt.group or k)
            if ek != (tgt.group or k):
                raise ValueError(f"{k} changed group from {ek} to {tgt.group or k}")
        else:
            ek = self.episode_key.get(k, k)  # only used when a position is open, and then k is mapped
        mark, q = marks[k], self.qty.get(k, 0.0)
        if mark is None:
            if tgt.weight != 0 or q != 0:
                self.events.append(f"{t} {k}: no fresh mark; order skipped")
            return
        target_qty = tgt.weight * equity / mark
        if tgt.instrument.market is Market.SPOT and target_qty < 0:
            self.events.append(f"{t} {k}: spot short refused by the simulator")
            return
        dq = target_qty - q
        going_flat = tgt.weight == 0 and q != 0
        if dq == 0 or (abs(dq) * mark < self.config.min_trade_weight * equity and not going_flat):
            return
        ref = self.exec_feeds[k].open_at(t)
        if ref is None:
            self.events.append(f"{t} {k}: no fill bar at {t}; order not filled")
            return
        if q != 0 and target_qty != 0 and (q > 0) != (target_qty > 0):
            self._fill(t, tgt.instrument, -q, ref, ek)  # a sign flip is a close followed by an open
            self._fill(t, tgt.instrument, target_qty, ref, ek)
        else:
            self._fill(t, tgt.instrument, dq, ref, ek)

    def run(self) -> BacktestResult:
        prev: pd.Timestamp | None = None
        for t in pd.date_range(self.start, self.end, freq=self.step):
            if prev is not None:
                self._apply_funding(prev, t)
            marks, equity = self._mark(t)
            if t == self.end:
                break
            if equity <= 0:
                self.events.append(f"{t}: equity {equity:.2f} <= 0; backtest stopped")
                break
            targets, sized, decisions = self._decide(t)
            for d, raw_t, sized_t in zip(decisions, targets, sized, strict=True):
                row = {"time": t, "instrument": d.target.instrument.key, "raw_weight": raw_t.weight,
                       "sized_weight": sized_t.weight, "final_weight": d.target.weight,
                       "verdict": str(d.verdict), "rule": d.rule}  # fmt: skip
                self.decision_rows.append(row)
                self._execute(t, d, marks, equity)
            prev = t
        return self._result()

    def _result(self) -> BacktestResult:
        final_t = max(self.equity_pts)
        for ek, ep in self.open_episode.items():
            value = sum(self.inst_cash.get(m, 0.0) + self.qty.get(m, 0.0) * self.last_mark.get(m, 0.0)
                        for m in self._members(ek))  # fmt: skip
            self.episodes.append(
                {
                    "key": ek,
                    "open_time": ep["open_time"],
                    "close_time": final_t,
                    "pnl": value - ep["start"],
                    "is_open": True,
                }
            )
        return BacktestResult(
            strategy=self.strategy.name,
            config=self.config,
            equity=pd.Series(self.equity_pts, name="equity", dtype="float64"),
            gross_exposure=pd.Series(self.exposure_pts, name="gross_exposure", dtype="float64"),
            fills=pd.DataFrame(self.fills, columns=FILL_COLUMNS),
            funding=pd.DataFrame(self.funding_rows, columns=FUNDING_COLUMNS),
            episodes=pd.DataFrame(self.episodes, columns=EPISODE_COLUMNS),
            decisions=pd.DataFrame(self.decision_rows, columns=DECISION_COLUMNS),
            events=self.events,
        )


def run_backtest(
    strategy: Strategy, store: PITStore, config: BacktestConfig, *, sizer: Sizer, gate: Gate, costs: CostModel
) -> BacktestResult:
    return _Simulation(strategy, store, config, sizer=sizer, gate=gate, costs=costs).run()
