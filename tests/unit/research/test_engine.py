"""Engine accounting checked against hand-computed paths."""

from __future__ import annotations

import pandas as pd
import pytest

from agent.core.types import Instrument, Target
from agent.data.datasets import FUNDING, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.research.costs import CostModel
from agent.research.engine import BacktestConfig, run_backtest
from agent.risk.exposure import ExposureGate
from agent.sizing.voltarget import PassThroughSizer
from agent.strategies.base import Snapshot
from tests.fixtures.factories import funding_csv, kline_csv_from_prices
from tests.unit.research.conftest import T0, ScriptedStrategy, put_funding, put_prices

H = pd.Timedelta("1h")
OPEN_GATE = ExposureGate(max_weight=10.0, max_gross=10.0)  # test-only: wide limits to check pure accounting


def _run(store: PITStore, strat: ScriptedStrategy, hours: int, costs: CostModel | None = None, **kw):  # type: ignore[no-untyped-def]
    kw = {"require_funding": False, **kw}  # accounting fixtures carry no funding unless the test adds it
    cfg = BacktestConfig(start=T0 + H, end=T0 + hours * H, initial_equity=1000.0, min_trade_weight=0.0, **kw)
    return run_backtest(
        strat, store, cfg, sizer=PassThroughSizer(), gate=OPEN_GATE, costs=costs or CostModel.zero()
    )


def test_flat_strategy_keeps_equity(store: PITStore) -> None:
    put_prices(store, [100.0] * 10, [101.0] * 10)
    res = _run(store, ScriptedStrategy(lambda t: 0.0), 9)
    assert (res.equity == 1000.0).all()
    assert res.fills.empty


def test_long_one_x_tracks_price_from_the_fill_open(store: PITStore) -> None:
    # bar i opens at opens[i] and closes at closes[i]; decision at T0+1h fills at opens[1] = 100
    opens = [99.0, 100.0, 110.0, 121.0, 120.0]
    closes = [100.0, 110.0, 121.0, 120.0, 132.0]
    put_prices(store, opens, closes)
    res = _run(store, ScriptedStrategy(lambda t: 1.0), 4)
    # Decisions at 1h, 2h, 3h; the final timestamp (4h) only marks.
    # 1h: mark = close[0] = 100, equity 1000 -> buy 10 @ open[1] = 100.
    # 2h: mark 110 -> equity 1100, target qty 1100/110 = 10 -> no trade.
    # 3h: mark 121 -> 1210, target 10 -> no trade.   4h: mark 120 -> 1200.
    assert list(res.equity.round(6)) == [1000.0, 1100.0, 1210.0, 1200.0]
    assert len(res.fills) == 1
    first = res.fills.iloc[0]
    assert first["time"] == T0 + H and first["price"] == 100.0 and first["qty"] == pytest.approx(10.0)


def test_fees_and_slippage_by_hand(store: PITStore) -> None:
    put_prices(store, [100.0] * 6, [100.0] * 6)
    costs = CostModel(taker_fee_bps={"perp": 10.0, "spot": 10.0}, slippage_bps={}, default_slippage_bps=10.0)
    res = _run(store, ScriptedStrategy(lambda t: 1.0 if t == T0 + H else 0.0), 3, costs=costs)
    # buy 10 @ 100.1, fee 10*100.1*0.001 = 1.001; sell 10 @ 99.9, fee 0.999
    # equity at 3h = 1000 - 1001 - 1.001 + 999 - 0.999 = 996.0
    assert res.equity.iloc[-1] == pytest.approx(996.0)
    assert res.fills["fee"].sum() == pytest.approx(2.0)
    assert res.fills["slippage_cost"].sum() == pytest.approx(2.0)


def test_funding_paid_by_longs_only_when_held_at_settlement(store: PITStore) -> None:
    put_prices(store, [100.0] * 30, [100.0] * 30)
    put_funding(store, 4, rate=0.001)  # settlements at 00, 08, 16, 24h
    # long from 1h to 8h exactly: position opened at 1h, closed by the decision at 8h.
    # The 08:00 settlement happens before the 8h fill, so it IS paid: 10 * 100 * 0.001 = 1.0
    res = _run(store, ScriptedStrategy(lambda t: 1.0 if t < T0 + 8 * H else 0.0), 20, require_funding=True)
    assert res.funding["amount"].sum() == pytest.approx(-1.0)
    assert res.equity.iloc[-1] == pytest.approx(999.0)
    # short over the same window receives it
    res_short = _run(store, ScriptedStrategy(lambda t: -1.0 if t < T0 + 8 * H else 0.0), 20)
    assert res_short.equity.iloc[-1] == pytest.approx(1001.0)


def test_perp_backtest_without_funding_data_is_refused(store: PITStore) -> None:
    put_prices(store, [100.0] * 5, [100.0] * 5)
    with pytest.raises(ValueError, match="funding"):
        _run(store, ScriptedStrategy(lambda t: 1.0), 4, require_funding=True)


def test_missing_fill_bar_means_no_fill(store: PITStore) -> None:
    put_prices(store, [100.0] * 3, [100.0] * 3)  # bars 0..2 only; decision at 3h has no bar to fill in
    res = _run(store, ScriptedStrategy(lambda t: 1.0 if t >= T0 + 3 * H else 0.0), 4)
    assert res.fills.empty
    assert any("no fill bar" in e for e in res.events)


def test_episodes_record_round_trips(store: PITStore) -> None:
    put_prices(store, [100.0, 100.0, 110.0, 110.0, 105.0, 105.0], [100.0, 110.0, 110.0, 105.0, 105.0, 105.0])
    sched = {1: 1.0, 2: 0.0, 3: -1.0, 4: 0.0}
    res = _run(store, ScriptedStrategy(lambda t: sched.get(int((t - T0) / H), 0.0)), 5)
    ep = res.episodes
    assert len(ep) == 2 and not ep["is_open"].any()
    assert ep["pnl"].round(6).tolist() == [100.0, pytest.approx(1100 / 110 * 5)]


def test_sign_flip_is_two_episodes(store: PITStore) -> None:
    put_prices(store, [100.0] * 6, [100.0] * 6)
    sched = {1: 1.0, 2: -1.0, 3: 0.0}
    res = _run(store, ScriptedStrategy(lambda t: sched.get(int((t - T0) / H), 0.0)), 4)
    assert len(res.episodes) == 2


SPOT = Instrument("BTCUSDT", "spot")


class ShortSpot(ScriptedStrategy):
    def instruments(self) -> list[Instrument]:
        return [SPOT]

    def decide(self, snap: Snapshot) -> list[Target]:
        return [Target(SPOT, -1.0)]


def test_spot_short_is_never_filled_even_if_the_gate_allows_it(store: PITStore) -> None:
    spot = klines("spot", "1h")
    store.write(
        spot, "BTCUSDT", parse_csv(spot, kline_csv_from_prices("2024-01-01", "1h", [100.0] * 5, [100.0] * 5))
    )
    cfg = BacktestConfig(start=T0 + H, end=T0 + 4 * H, min_trade_weight=0.0)
    permissive = ExposureGate(max_weight=10.0, max_gross=10.0, allow_spot_short=True)
    res = run_backtest(
        ShortSpot(lambda t: 0.0),
        store,
        cfg,
        sizer=PassThroughSizer(),
        gate=permissive,
        costs=CostModel.zero(),
    )
    assert res.fills.empty
    assert any("spot short" in e for e in res.events)


def test_unaligned_start_rejected(store: PITStore) -> None:
    put_prices(store, [100.0] * 3, [100.0] * 3)
    with pytest.raises(ValueError, match="aligned"):
        cfg = BacktestConfig(start=T0 + pd.Timedelta("30min"), end=T0 + 2 * H)
        run_backtest(
            ScriptedStrategy(lambda t: 0.0),
            store,
            cfg,
            sizer=PassThroughSizer(),
            gate=OPEN_GATE,
            costs=CostModel.zero(),
        )


def test_offset_funding_prints_are_charged_to_the_position_held_at_settlement(store: PITStore) -> None:
    """A print stamped 08:00:00.002 settles at 08:00: a position opened by the 08:00 decision must not pay it,
    and one closed by the 08:00 decision must."""
    put_prices(store, [100.0] * 30, [100.0] * 30)
    store.write(FUNDING, "BTCUSDT", parse_csv(FUNDING, funding_csv("2024-01-01", 4, rate=0.001, offset_ms=2)))
    opened_at_8 = _run(store, ScriptedStrategy(lambda t: 1.0 if T0 + 8 * H <= t < T0 + 10 * H else 0.0), 20,
                       require_funding=True)  # fmt: skip
    assert opened_at_8.funding.empty
    closed_at_8 = _run(
        store, ScriptedStrategy(lambda t: 1.0 if t < T0 + 8 * H else 0.0), 20, require_funding=True
    )
    assert closed_at_8.funding["amount"].sum() == pytest.approx(-1.0)
