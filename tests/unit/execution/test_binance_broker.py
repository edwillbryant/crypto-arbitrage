"""BinanceBroker against a fake ccxt client that mimics the unified-API shapes of the pinned ccxt version."""

from __future__ import annotations

from typing import Any

import ccxt
import pytest

from agent.core.types import Instrument
from agent.execution.binance_broker import BinanceBroker
from agent.execution.exchange_whitelist import SafeExchange
from agent.execution.types import Order, OrderType

BTC_PERP = Instrument("BTCUSDT", "perp")
BTC_SPOT = Instrument("BTCUSDT", "spot")


class FakeCcxt:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        fee = {"cost": 0.05, "currency": "USDT"}
        self.next_order: dict[str, Any] | Exception = {
            "status": "closed",
            "filled": 0.002,
            "average": 50_010.0,
            "fee": fee,
        }
        self.withdraw = lambda *a, **k: pytest.fail("withdraw reached")  # must stay unreachable

    def load_markets(self) -> dict[str, Any]:
        sym = "BTC/USDT:USDT" if self.kind == "perp" else "BTC/USDT"
        return {
            sym: {
                "id": "BTCUSDT",
                "symbol": sym,
                "base": "BTC",
                "quote": "USDT",
                "precision": {"amount": 0.001 if self.kind == "perp" else 0.00001, "price": 0.1},
                "limits": {"cost": {"min": 100.0 if self.kind == "perp" else 5.0}},
            }
        }

    def fetch_positions(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        return [{"symbol": "BTC/USDT:USDT", "contracts": 0.003, "side": "short", "unrealizedPnl": -1.5}]

    def fetch_balance(self) -> dict[str, Any]:
        if self.kind == "perp":
            return {"total": {"USDT": 1_000.0}}
        return {"total": {"USDT": 500.0, "BTC": 0.004, "BNB": 0.1}}

    def fetch_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return [{"clientOrderId": f"{self.kind}-open"}]

    def create_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_order", args, kwargs))
        if isinstance(self.next_order, Exception):
            raise self.next_order
        return self.next_order

    def cancel_all_orders(self, symbol: str) -> list[Any]:
        self.calls.append(("cancel_all_orders", (symbol,), {}))
        return []


@pytest.fixture
def fakes() -> tuple[FakeCcxt, FakeCcxt, BinanceBroker]:
    spot, perp = FakeCcxt("spot"), FakeCcxt("perp")
    return (
        spot,
        perp,
        BinanceBroker(spot=SafeExchange(spot), perp=SafeExchange(perp), instruments=[BTC_PERP, BTC_SPOT]),
    )


def test_positions_are_signed_and_spot_counts_base_held(
    fakes: tuple[FakeCcxt, FakeCcxt, BinanceBroker],
) -> None:
    _, _, b = fakes
    assert b.positions() == {BTC_PERP.key: -0.003, BTC_SPOT.key: 0.004}


def test_rules_come_from_markets(fakes: tuple[FakeCcxt, FakeCcxt, BinanceBroker]) -> None:
    _, _, b = fakes
    r = b.rules(BTC_PERP)
    assert (r.step, r.tick, r.min_notional) == (0.001, 0.1, 100.0)
    assert b.rules(BTC_SPOT).step == 0.00001


def test_equity_sums_both_wallets_spot_holdings_and_unrealized(
    fakes: tuple[FakeCcxt, FakeCcxt, BinanceBroker],
) -> None:
    _, _, b = fakes
    # spot USDT 500 + 0.004 BTC * 50,000 + futures wallet 1,000 + unrealized -1.5 ; untracked BNB ignored
    assert b.equity({BTC_SPOT.key: 50_000.0, BTC_PERP.key: 50_000.0}) == pytest.approx(
        500 + 200 + 1_000 - 1.5
    )


def test_ioc_order_maps_to_ccxt_and_back(fakes: tuple[FakeCcxt, FakeCcxt, BinanceBroker]) -> None:
    _, perp, b = fakes
    ack = b.place(Order("cid-1", BTC_PERP, -0.002, OrderType.LIMIT_IOC, 49_990.0, reduce_only=True))
    _, args, kwargs = perp.calls[-1]
    assert args[:5] == ("BTC/USDT:USDT", "limit", "sell", 0.002, 49_990.0)
    assert kwargs["params"] == {"timeInForce": "IOC", "newClientOrderId": "cid-1", "reduceOnly": True}
    assert (ack.status, ack.filled_qty, ack.avg_price, ack.fee) == ("FILLED", -0.002, 50_010.0, 0.05)


def test_spot_orders_never_send_reduce_only(fakes: tuple[FakeCcxt, FakeCcxt, BinanceBroker]) -> None:
    spot, _, b = fakes
    b.place(Order("cid-2", BTC_SPOT, 0.002, OrderType.LIMIT_IOC, 50_010.0, reduce_only=False))
    assert "reduceOnly" not in spot.calls[-1][2]["params"]


def test_partial_and_unfilled_ioc(fakes: tuple[FakeCcxt, FakeCcxt, BinanceBroker]) -> None:
    _, perp, b = fakes
    perp.next_order = {"status": "canceled", "filled": 0.001, "average": 50_000.0, "fee": None}
    assert b.place(Order("c", BTC_PERP, 0.002, OrderType.LIMIT_IOC, 50_010.0, False)).status == "PARTIAL"
    perp.next_order = {"status": "expired", "filled": 0.0, "average": None, "fee": None}
    ack = b.place(Order("d", BTC_PERP, 0.002, OrderType.LIMIT_IOC, 50_010.0, False))
    assert (ack.status, ack.filled_qty, ack.avg_price) == ("NONE", 0.0, 0.0)


def test_exchange_rejections_become_rejected_acks_but_network_errors_raise(
    fakes: tuple[FakeCcxt, FakeCcxt, BinanceBroker],
) -> None:
    _, perp, b = fakes
    perp.next_order = ccxt.InsufficientFunds("margin is insufficient")
    ack = b.place(Order("e", BTC_PERP, 0.002, OrderType.LIMIT_IOC, 50_010.0, False))
    assert ack.status == "REJECTED" and "InsufficientFunds" in ack.reason
    perp.next_order = ccxt.NetworkError("timeout")
    with pytest.raises(ccxt.NetworkError):  # unknown outcome: the reconciler must find out, not us guess
        b.place(Order("f", BTC_PERP, 0.002, OrderType.LIMIT_IOC, 50_010.0, False))


def test_market_orders_only_reduce_only_on_perps(fakes: tuple[FakeCcxt, FakeCcxt, BinanceBroker]) -> None:
    _, _, b = fakes
    with pytest.raises(ValueError, match="reduce-only"):
        b.place(Order("g", BTC_PERP, 0.002, OrderType.MARKET, None, reduce_only=False))


def test_open_orders_and_cancel_all_cover_both_venues(
    fakes: tuple[FakeCcxt, FakeCcxt, BinanceBroker],
) -> None:
    spot, perp, b = fakes
    assert sorted(b.open_orders()) == ["perp-open", "spot-open"]
    b.cancel_all()
    assert ("cancel_all_orders", ("BTC/USDT:USDT",), {}) in perp.calls
    assert ("cancel_all_orders", ("BTC/USDT",), {}) in spot.calls


def test_broker_requires_safe_exchange() -> None:
    with pytest.raises(TypeError, match="SafeExchange"):
        BinanceBroker(spot=FakeCcxt("spot"), perp=FakeCcxt("perp"), instruments=[BTC_PERP])  # type: ignore[arg-type]
