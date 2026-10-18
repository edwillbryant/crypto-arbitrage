"""The live loop end-to-end against a store-backed feed and the paper broker (synthetic data)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from agent.data.datasets import FUNDING, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.execution.order_manager import OrderManager
from agent.execution.paper import PaperBroker
from agent.execution.reconciler import Reconciler
from agent.execution.hedge import HedgeMonitor
from agent.execution.types import Order, OrderType
from agent.live.feed import StoreFeed
from agent.live.loop import LiveLoop
from agent.ops.log import DecisionLog
from agent.risk.gate import Mode, RiskGate
from agent.risk.killswitch import KillSwitch
from agent.risk.ledger import RiskLedger
from agent.risk.limits import Limits
from agent.sizing.voltarget import VolTargetSizer
from agent.strategies.c1_carry import CarryParams, FundingCarry
from tests.fixtures.factories import funding_csv, kline_csv_from_prices
from tests.unit.execution.fakes import RULES, FailingBroker

T0 = pd.Timestamp("2024-01-10", tz="UTC")
H = pd.Timedelta(hours=1)


@pytest.fixture
def world(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = PITStore(tmp_path / "store")
    n = 24 * 12
    for m in ("spot", "perp"):
        ds = klines(m, "1h")
        store.write(
            ds,
            "BTCUSDT",
            parse_csv(ds, kline_csv_from_prices("2024-01-01", "1h", [50_000.0] * n, [50_000.0] * n)),
        )
    store.write(
        FUNDING, "BTCUSDT", parse_csv(FUNDING, funding_csv("2024-01-01", 36, rate=0.0004))
    )  # ~44% APR
    feed = StoreFeed(store, half_spread_bps=1.0, depth=100.0)
    broker = PaperBroker(
        cash=10_000.0, rules=RULES, book=feed.book, taker_fee_bps={"perp": 5.0, "spot": 10.0}
    )
    ledger = RiskLedger(tmp_path / "risk.db")
    ks = KillSwitch(ledger, tmp_path / "KILL")
    return {"tmp": tmp_path, "feed": feed, "broker": broker, "ledger": ledger, "ks": ks}


def _loop(w, broker=None) -> LiveLoop:  # type: ignore[no-untyped-def]
    b = broker or w["broker"]
    ledger = w["ledger"]
    gate = RiskGate(Limits(), ledger, killswitch=w["ks"])
    return LiveLoop(
        strategy=FundingCarry(CarryParams(), symbols=("BTCUSDT",)),
        feed=w["feed"], broker=b, gate=gate, sizer=VolTargetSizer(lookback_bars=24),
        orders=OrderManager(b, max_slippage_bps=10.0, min_trade_weight=0.01, on_error=lambda k, d: None),
        reconciler=Reconciler(RULES), hedge=HedgeMonitor(), ledger=ledger,
        log=DecisionLog(w["tmp"] / "decisions.jsonl"), heartbeat_path=w["tmp"] / "heartbeat",
    )  # fmt: skip


def test_a_cycle_trades_logs_and_heartbeats(world) -> None:  # type: ignore[no-untyped-def]
    rep = _loop(world).step(T0)
    pos = world["broker"].positions()
    assert rep.mode is Mode.NORMAL
    assert pos["spot:BTCUSDT"] > 0 and pos["perp:BTCUSDT"] < 0
    assert pos["spot:BTCUSDT"] == pytest.approx(-pos["perp:BTCUSDT"])
    assert (world["tmp"] / "heartbeat").exists()
    assert len((world["tmp"] / "decisions.jsonl").read_text().splitlines()) == 1


def test_kill_switch_flattens_on_the_next_cycle(world) -> None:  # type: ignore[no-untyped-def]
    loop = _loop(world)
    loop.step(T0)
    (world["tmp"] / "KILL").write_text("operator")
    rep = loop.step(T0 + H)
    assert rep.mode is Mode.FLATTEN
    assert world["broker"].positions() == {}
    assert loop.step(T0 + 2 * H).mode is Mode.FLATTEN  # stays locked


def test_repeated_api_errors_halt_trading(world) -> None:  # type: ignore[no-untyped-def]
    bad = FailingBroker(world["broker"], raise_on={"spot:BTCUSDT", "perp:BTCUSDT"})
    loop = _loop(world, broker=bad)
    modes = [loop.step(T0 + i * H).mode for i in range(4)]
    assert modes[-1] is Mode.HALT
    placed_before = len(bad.placed)
    loop.step(T0 + 4 * H)
    assert len(bad.placed) == placed_before  # HALT: nothing sent


def test_a_trade_made_outside_the_agent_halts_it(world) -> None:  # type: ignore[no-untyped-def]
    loop = _loop(world)
    loop.step(T0)
    world["broker"].place(Order("manual", __import__("agent.core.types", fromlist=["Instrument"]).Instrument("BTCUSDT", "perp"),
                                -0.05, OrderType.MARKET, None, False))  # fmt: skip
    rep = loop.step(T0 + H)
    assert rep.mode is Mode.HALT and "unreconciled" in rep.triggers


def test_stale_book_flattens(world) -> None:  # type: ignore[no-untyped-def]
    loop = _loop(world)
    loop.step(T0)
    world["feed"].book_age_override = 61.0
    assert loop.step(T0 + H).mode is Mode.FLATTEN
    assert world["broker"].positions() == {}


def test_strategy_crash_fails_safe(world) -> None:  # type: ignore[no-untyped-def]
    loop = _loop(world)
    loop.step(T0)

    def boom(snap):  # type: ignore[no-untyped-def]
        raise RuntimeError("bug")

    loop.strategy.decide = boom  # type: ignore[method-assign]
    rep = loop.step(T0 + H)
    assert any("strategy error" in e for e in rep.events)
    assert world["broker"].positions() == {}  # targets default to flat
