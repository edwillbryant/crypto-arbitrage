"""The engine must never hand a strategy data from the future."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from agent.core.types import Target
from agent.data.datasets import klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.research.costs import CostModel
from agent.research.engine import BacktestConfig, BacktestResult, run_backtest
from agent.risk.exposure import ExposureGate
from agent.sizing.voltarget import PassThroughSizer
from agent.strategies.base import Snapshot
from tests.fixtures.factories import kline_csv
from tests.unit.research.conftest import BTC_PERP, T0, ScriptedStrategy

H1 = klines("perp", "1h")
H = pd.Timedelta("1h")


class Momentum(ScriptedStrategy):
    def decide(self, snap: Snapshot) -> list[Target]:
        self.seen.append(snap)
        c = snap.get(H1, "BTCUSDT")["close"]
        return [Target(BTC_PERP, 0.0 if len(c) < 3 else (1.0 if c.iloc[-1] > c.iloc[-3] else -1.0))]


def _run(store: PITStore, strat: ScriptedStrategy, end_h: int) -> BacktestResult:
    cfg = BacktestConfig(start=T0 + 3 * H, end=T0 + end_h * H, min_trade_weight=0.0)
    return run_backtest(strat, store, cfg, sizer=PassThroughSizer(), gate=ExposureGate(), costs=CostModel())


def test_snapshots_contain_only_available_rows(store: PITStore) -> None:
    store.write(H1, "BTCUSDT", parse_csv(H1, kline_csv("2024-01-01", 200)))
    strat = Momentum(lambda t: 0.0)
    _run(store, strat, 150)
    assert len(strat.seen) > 100
    for snap in strat.seen:
        df = snap.get(H1, "BTCUSDT")
        assert (df["available_at"] <= snap.as_of).all()
        assert df["key_time"].max() == snap.as_of - H  # newest bar is the one that just closed


def test_future_poisoning_changes_nothing_before_the_cut(store: PITStore, tmp_path: Path) -> None:
    clean = parse_csv(H1, kline_csv("2024-01-01", 200, seed=3))
    store.write(H1, "BTCUSDT", clean)
    cut = T0 + 120 * H
    poisoned = clean.copy()
    after = poisoned["key_time"] >= cut
    poisoned.loc[after, ["open", "high", "low", "close"]] *= 5
    poisoned_store = PITStore(tmp_path / "poisoned")
    poisoned_store.write(H1, "BTCUSDT", poisoned)

    ra = _run(store, Momentum(lambda t: 0.0), 190)
    rb = _run(poisoned_store, Momentum(lambda t: 0.0), 190)
    # Equity at t is marked before the fill at t, using bars that closed by t: identical up to the cut.
    pd.testing.assert_series_equal(ra.equity[ra.equity.index <= cut], rb.equity[rb.equity.index <= cut])
    fa = ra.fills[ra.fills["time"] < cut].reset_index(drop=True)
    fb = rb.fills[rb.fills["time"] < cut].reset_index(drop=True)
    assert len(fa) > 5
    pd.testing.assert_frame_equal(fa, fb)
