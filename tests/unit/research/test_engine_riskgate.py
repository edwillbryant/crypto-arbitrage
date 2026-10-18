"""The backtest engine running under the production RiskGate."""

from __future__ import annotations

import pandas as pd

from agent.data.store import PITStore
from agent.research.costs import CostModel
from agent.research.engine import BacktestConfig, run_backtest
from agent.risk.gate import RiskGate
from agent.risk.ledger import RiskLedger
from agent.risk.limits import Limits
from agent.sizing.voltarget import PassThroughSizer
from tests.unit.research.conftest import T0, ScriptedStrategy, put_prices

H = pd.Timedelta("1h")


def _run(store: PITStore, prices: list[float], weight: float):  # type: ignore[no-untyped-def]
    put_prices(store, prices[:-1], prices[1:])  # bar i opens at prices[i] and closes at prices[i+1]
    gate = RiskGate(Limits(), RiskLedger(":memory:"))
    cfg = BacktestConfig(start=T0 + H, end=T0 + (len(prices) - 2) * H, initial_equity=1000.0,
                         min_trade_weight=0.0, require_funding=False)  # fmt: skip
    return run_backtest(
        ScriptedStrategy(lambda t: weight),
        store,
        cfg,
        sizer=PassThroughSizer(),
        gate=gate,
        costs=CostModel.zero(),
    )


def test_drawdown_breach_flattens_and_stays_flat(store: PITStore) -> None:
    # 25% long into a falling market: -8% equity needs a ~32% price drop. Then the price recovers.
    prices = [100.0, 100.0] + [100.0 - 4 * i for i in range(1, 11)] + [100.0] * 10
    res = _run(store, prices, 0.25)
    d = res.decisions
    first_flat = d.index[d["verdict"] == "FLATTEN"][0]
    assert (d.loc[first_flat:, "final_weight"] == 0).all()  # locked: never re-enters, even after recovery
    assert "max_drawdown" in d.loc[first_flat, "rule"]
    dd = 1 - res.equity / res.equity.cummax()
    assert dd.max() < 0.10  # stopped near the 8% limit, not ridden down


def test_daily_loss_blocks_new_risk_for_the_rest_of_the_day(store: PITStore) -> None:
    prices = [100.0, 100.0, 95.0, 95.0, 95.0, 95.0, 95.0]
    res = _run(store, prices, 0.25)  # -5% price * 25% = -1.25% equity
    d = res.decisions
    assert d["verdict"].iloc[0] == "ACCEPT"
    assert (d["verdict"].iloc[2:] == "REDUCE").all() or (
        d["final_weight"].iloc[2:] <= d["final_weight"].iloc[1]
    ).all()
