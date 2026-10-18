"""Every registered strategy runs end-to-end through the engine and the production RiskGate.

Synthetic data: this checks wiring (data requests, instruments, hedge groups), not performance.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from agent.data.datasets import FUNDING, METRICS, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.research.costs import CostModel
from agent.research.engine import BacktestConfig, run_backtest
from agent.risk.gate import RiskGate
from agent.risk.ledger import RiskLedger
from agent.risk.limits import Limits
from agent.sizing.voltarget import VolTargetSizer
from agent.strategies.registry import REGISTRY, variants
from tests.fixtures.factories import funding_csv, kline_csv, metrics_csv

DAYS = 45


@pytest.fixture(scope="module")
def market(tmp_path_factory: pytest.TempPathFactory) -> PITStore:
    store = PITStore(Path(tmp_path_factory.mktemp("market")))
    for i, sym in enumerate(("BTCUSDT", "ETHUSDT")):
        for market in ("spot", "perp"):
            h1, d1 = klines(market, "1h"), klines(market, "1d")
            store.write(h1, sym, parse_csv(h1, kline_csv("2024-01-01", DAYS * 24, "1h", seed=i)))
            store.write(d1, sym, parse_csv(d1, kline_csv("2024-01-01", DAYS, "1d", seed=i)))
        store.write(FUNDING, sym, parse_csv(FUNDING, funding_csv("2024-01-01", DAYS * 3, rate=0.0004)))
        store.write(METRICS, sym, parse_csv(METRICS, metrics_csv("2024-01-01", DAYS * 288, symbol=sym)))
    return store


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_strategy_runs_under_the_production_gate(market: PITStore, name: str) -> None:
    spec = REGISTRY[name]
    strat = spec.build(variants(name)[0])
    cfg = BacktestConfig(start=pd.Timestamp("2024-01-20", tz="UTC"), end=pd.Timestamp("2024-02-10", tz="UTC"))
    res = run_backtest(strat, market, cfg, sizer=VolTargetSizer(lookback_bars=24 * 7),
                       gate=RiskGate(Limits(), RiskLedger(":memory:")), costs=CostModel())  # fmt: skip
    assert len(res.equity) > 1
    assert set(res.decisions["instrument"]) == {i.key for i in strat.instruments()}
    assert not any("undeclared" in e for e in res.events)


def test_carry_trades_both_legs_together(market: PITStore) -> None:
    """Funding at 0.04%/8h (~44% APR) switches C1 on: spot and perp legs must move as a pair."""
    strat = REGISTRY["c1_carry"].build({"entry_apr": 0.15, "exit_apr": 0.05, "n_periods": 3})
    cfg = BacktestConfig(start=pd.Timestamp("2024-01-20", tz="UTC"), end=pd.Timestamp("2024-01-25", tz="UTC"),
                         min_trade_weight=0.0)  # fmt: skip
    res = run_backtest(strat, market, cfg, sizer=VolTargetSizer(lookback_bars=24 * 7),
                       gate=RiskGate(Limits(), RiskLedger(":memory:")), costs=CostModel())  # fmt: skip
    assert not res.fills.empty
    first = res.fills[res.fills["time"] == res.fills["time"].min()]
    assert set(first["instrument"]) == {"spot:BTCUSDT", "perp:BTCUSDT", "spot:ETHUSDT", "perp:ETHUSDT"}
    assert (res.episodes["key"].str.startswith("carry:")).all()
