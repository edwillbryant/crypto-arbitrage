from __future__ import annotations

from pathlib import Path

import pytest

from agent.execution.paper import PaperBroker
from agent.execution.types import Order, OrderType
from tests.unit.execution.fakes import BTC_PERP, BTC_SPOT, RULES, Books, paper


def _o(inst, qty, *, typ=OrderType.LIMIT_IOC, price=None, reduce=False, cid="c1"):  # type: ignore[no-untyped-def]
    return Order(cid, inst, qty, typ, price, reduce)


def test_ioc_buy_fills_at_the_ask_when_the_limit_allows() -> None:
    b = paper()
    ack = b.place(_o(BTC_PERP, 0.01, price=50_010.0))
    assert (ack.status, ack.filled_qty, ack.avg_price) == ("FILLED", 0.01, 50_000.5)
    assert ack.fee == pytest.approx(0.01 * 50_000.5 * 5e-4)
    assert b.positions()[BTC_PERP.key] == pytest.approx(0.01)


def test_ioc_does_not_fill_through_the_limit() -> None:
    ack = paper().place(_o(BTC_PERP, 0.01, price=49_990.0))
    assert ack.status == "NONE" and ack.filled_qty == 0.0


def test_partial_fill_limited_by_top_of_book_size() -> None:
    books = Books(qty=0.004)
    ack = paper(books=books).place(_o(BTC_PERP, 0.01, price=60_000.0))
    assert ack.status == "PARTIAL" and ack.filled_qty == pytest.approx(0.004)


def test_reduce_only_can_never_open_or_flip() -> None:
    b = paper()
    assert b.place(_o(BTC_PERP, -0.01, typ=OrderType.MARKET, reduce=True)).status == "REJECTED"  # flat
    b.place(_o(BTC_PERP, 0.01, price=60_000.0, cid="open"))
    ack = b.place(_o(BTC_PERP, -0.05, typ=OrderType.MARKET, reduce=True, cid="close"))
    assert ack.filled_qty == pytest.approx(-0.01)
    assert b.positions().get(BTC_PERP.key, 0.0) == 0.0


def test_spot_cannot_be_oversold_or_overbought() -> None:
    b = paper(cash=1_000.0)
    assert b.place(_o(BTC_SPOT, -0.001, price=1.0)).status == "REJECTED"  # nothing to sell
    assert b.place(_o(BTC_SPOT, 1.0, price=60_000.0, cid="big")).status == "REJECTED"  # not enough cash


def test_equity_and_funding() -> None:
    b = paper(cash=10_000.0)
    b.place(_o(BTC_PERP, 0.1, price=60_000.0))
    marks = {BTC_PERP.key: 51_000.0}
    fee = 0.1 * 50_000.5 * 5e-4
    assert b.equity(marks) == pytest.approx(10_000.0 - fee + 0.1 * (51_000.0 - 50_000.5))
    b.apply_funding(BTC_PERP, rate=0.0001, mark=51_000.0)
    assert b.equity(marks) == pytest.approx(
        10_000.0 - fee + 0.1 * (51_000.0 - 50_000.5) - 0.1 * 51_000.0 * 0.0001
    )


def test_duplicate_client_id_is_idempotent() -> None:
    b = paper()
    first = b.place(_o(BTC_PERP, 0.01, price=60_000.0, cid="same"))
    again = b.place(_o(BTC_PERP, 0.01, price=60_000.0, cid="same"))
    assert again == first and b.positions()[BTC_PERP.key] == pytest.approx(0.01)


def test_no_book_means_no_fill() -> None:
    books = Books()
    del books.quotes[BTC_PERP.key]
    assert paper(books=books).place(_o(BTC_PERP, 0.01, price=60_000.0)).status == "REJECTED"


def test_state_persists(tmp_path: Path) -> None:
    path = tmp_path / "paper.json"
    b = PaperBroker(
        cash=5_000.0, rules=RULES, book=Books(), taker_fee_bps={"perp": 5.0, "spot": 10.0}, state_path=path
    )
    b.place(_o(BTC_PERP, 0.01, price=60_000.0))
    again = PaperBroker(
        cash=0.0, rules=RULES, book=Books(), taker_fee_bps={"perp": 5.0, "spot": 10.0}, state_path=path
    )
    assert again.positions() == b.positions() and again.cash() == pytest.approx(b.cash())
