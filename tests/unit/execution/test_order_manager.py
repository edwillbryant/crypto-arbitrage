from __future__ import annotations

import pytest

from agent.core.types import Target
from agent.execution.order_manager import OrderManager, client_order_id
from agent.execution.types import OrderType
from agent.risk.exposure import Decision, Verdict
from agent.risk.gate import GateResult, Mode
from tests.unit.execution.fakes import BTC_PERP, BTC_SPOT, ETH_PERP, T, Books, FailingBroker, paper

MARKS = {BTC_PERP.key: 50_000.0, BTC_SPOT.key: 50_000.0, ETH_PERP.key: 2_000.0}


def _result(
    *targets: Target, mode: Mode = Mode.NORMAL, cancel: bool = False, submit: bool = True
) -> GateResult:
    verdict = Verdict.FLATTEN if mode is Mode.FLATTEN else Verdict.ACCEPT
    return GateResult(mode, (), [Decision(t, t, verdict) for t in targets], cancel, submit, False)


def _om(broker, errors: list[tuple[str, str]] | None = None) -> OrderManager:  # type: ignore[no-untyped-def]
    sink = errors if errors is not None else []
    return OrderManager(
        broker, max_slippage_bps=10.0, min_trade_weight=0.0, on_error=lambda k, d: sink.append((k, d))
    )


def test_quantity_rounds_toward_zero_and_price_is_capped() -> None:
    b = paper()
    rep = _om(b).execute(_result(Target(BTC_PERP, 0.10)), now=T, equity=10_000.0, marks=MARKS, cycle_id="c1")
    o = rep.orders[0]
    assert o.qty == pytest.approx(0.02)  # 0.1 * 10_000 / 50_000 = 0.02 exactly
    assert o.type is OrderType.LIMIT_IOC and o.limit_price == pytest.approx(50_050.0) and not o.reduce_only
    rep2 = _om(paper()).execute(
        _result(Target(BTC_PERP, 0.10)), now=T, equity=10_249.0, marks=MARKS, cycle_id="c2"
    )
    assert rep2.orders[0].qty == pytest.approx(
        0.020
    )  # 0.020498 floors to 0.020: rounding never adds exposure


def test_below_min_notional_is_skipped() -> None:
    rep = _om(paper()).execute(
        _result(Target(BTC_PERP, 0.001)), now=T, equity=10_000.0, marks=MARKS, cycle_id="c"
    )
    assert rep.orders == []  # $10 < $100 minimum


def test_going_flat_closes_everything_reduce_only() -> None:
    b = paper()
    om = _om(b)
    om.execute(_result(Target(BTC_PERP, 0.1)), now=T, equity=10_000.0, marks=MARKS, cycle_id="open")
    rep = om.execute(_result(Target(BTC_PERP, 0.0)), now=T, equity=10_000.0, marks=MARKS, cycle_id="close")
    assert rep.orders[0].reduce_only and rep.orders[0].qty == pytest.approx(-0.02)
    assert b.positions().get(BTC_PERP.key, 0.0) == 0.0


def test_orphan_positions_are_flattened() -> None:
    b = paper()
    _om(b).execute(_result(Target(ETH_PERP, 0.1)), now=T, equity=10_000.0, marks=MARKS, cycle_id="x")
    rep = _om(b).execute(_result(Target(BTC_PERP, 0.0)), now=T, equity=10_000.0, marks=MARKS, cycle_id="y")
    assert [(o.instrument, o.reduce_only) for o in rep.orders] == [(ETH_PERP, True)]
    assert "orphan" in rep.events[0]


def test_halt_sends_nothing_but_cancels() -> None:
    b = FailingBroker(paper())
    cancelled = []
    b.inner.cancel_all = lambda: cancelled.append(True)  # type: ignore[method-assign]
    rep = _om(b).execute(_result(Target(BTC_PERP, 0.1), mode=Mode.HALT, cancel=True, submit=False),
                         now=T, equity=10_000.0, marks=MARKS, cycle_id="h")  # fmt: skip
    assert rep.orders == [] and b.placed == [] and cancelled == [True]


def test_flatten_uses_reduce_only_market_orders() -> None:
    b = paper()
    _om(b).execute(
        _result(Target(BTC_PERP, 0.1), Target(ETH_PERP, -0.1)),
        now=T,
        equity=10_000.0,
        marks=MARKS,
        cycle_id="a",
    )
    rep = _om(b).execute(_result(Target(BTC_PERP, 0.0), Target(ETH_PERP, 0.0), mode=Mode.FLATTEN, cancel=True),
                         now=T, equity=10_000.0, marks=MARKS, cycle_id="k")  # fmt: skip
    assert {(o.type, o.reduce_only) for o in rep.orders} == {(OrderType.MARKET, True)}
    assert b.positions() == {}


def test_carry_places_spot_first_and_sizes_perp_to_the_spot_fill() -> None:
    books = Books()
    books.set(BTC_SPOT.key, 50_000.0, qty=0.01)  # thin spot book: only half the spot leg fills
    b = paper(books=books)
    rep = _om(b).execute(_result(Target(BTC_SPOT, 0.2, group="g"), Target(BTC_PERP, -0.2, group="g")),
                         now=T, equity=10_000.0, marks=MARKS, cycle_id="carry")  # fmt: skip
    assert [o.instrument for o in rep.orders] == [BTC_SPOT, BTC_PERP]
    pos = b.positions()
    assert pos[BTC_SPOT.key] == pytest.approx(0.01)
    assert pos[BTC_PERP.key] == pytest.approx(-0.01)  # hedged to what actually filled


def test_failed_hedge_leg_unwinds_the_first_leg() -> None:
    errors: list[tuple[str, str]] = []
    b = FailingBroker(paper(), reject_on={BTC_PERP.key})
    rep = _om(b, errors).execute(_result(Target(BTC_SPOT, 0.2, group="g"), Target(BTC_PERP, -0.2, group="g")),
                                 now=T, equity=10_000.0, marks=MARKS, cycle_id="carry")  # fmt: skip
    assert b.positions().get(BTC_SPOT.key, 0.0) == pytest.approx(0.0, abs=1e-9)
    assert any("unwound" in e for e in rep.events)
    assert ("reject", "perp:BTCUSDT: simulated reject") in errors


def test_api_errors_are_reported_not_raised() -> None:
    errors: list[tuple[str, str]] = []
    b = FailingBroker(paper(), raise_on={BTC_PERP.key})
    rep = _om(b, errors).execute(_result(Target(BTC_PERP, 0.1), Target(ETH_PERP, 0.1)),
                                 now=T, equity=10_000.0, marks=MARKS, cycle_id="e")  # fmt: skip
    assert errors[0][0] == "error"
    assert ETH_PERP.key in b.positions()  # one failure doesn't stop the other instruments


def test_client_ids_are_deterministic_unique_and_exchange_safe() -> None:
    a = client_order_id("cycle-1", BTC_PERP.key, "main")
    assert a == client_order_id("cycle-1", BTC_PERP.key, "main")
    assert (
        a
        != client_order_id("cycle-1", BTC_PERP.key, "unwind")
        != client_order_id("cycle-2", BTC_PERP.key, "main")
    )
    assert len(a) <= 36 and a.replace("-", "").replace("_", "").isalnum()


def test_missing_mark_skips_the_instrument() -> None:
    rep = _om(paper()).execute(_result(Target(BTC_PERP, 0.1)), now=T, equity=10_000.0, marks={}, cycle_id="m")
    assert rep.orders == [] and "no mark" in rep.events[0]
