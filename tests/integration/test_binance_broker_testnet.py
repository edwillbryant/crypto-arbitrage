"""BinanceBroker against the real USDⓈ-M futures testnet with signed requests.

Opt-in twice: `-m integration` AND testnet keys in BINANCE_TESTNET_KEY / BINANCE_TESTNET_SECRET (testnet keys
only: they come from testnet.binancefuture.com and cannot touch real funds). The order is an IOC buy priced 3%
under the bid, so it can never fill: it proves signing, the order mapping and the ack parsing, nothing more.
"""

from __future__ import annotations

import os

import ccxt
import pytest

from agent.core.types import Instrument
from agent.execution.binance_broker import BinanceBroker
from agent.execution.exchange_whitelist import SafeExchange
from agent.execution.order_manager import floor_to_step, round_to_tick
from agent.execution.types import Order, OrderType

pytestmark = pytest.mark.integration
KEY, SECRET = os.environ.get("BINANCE_TESTNET_KEY"), os.environ.get("BINANCE_TESTNET_SECRET")


@pytest.mark.skipif(not (KEY and SECRET), reason="needs BINANCE_TESTNET_KEY and BINANCE_TESTNET_SECRET")
def test_signed_round_trip_on_futures_testnet() -> None:
    raw = ccxt.binanceusdm(
        {"apiKey": KEY, "secret": SECRET, "options": {"disableFuturesSandboxWarning": True}}
    )
    raw.set_sandbox_mode(True)
    perp = Instrument("BTCUSDT", "perp")
    b = BinanceBroker(spot=SafeExchange(object()), perp=SafeExchange(raw), instruments=[perp])
    rules = b.rules(perp)
    assert rules.step > 0 and rules.tick > 0 and rules.min_notional > 0
    bid = float(raw.fetch_ticker("BTC/USDT:USDT")["bid"])
    price = round_to_tick(bid * 0.97, rules.tick, up=False)
    qty = floor_to_step(max(rules.min_notional * 1.5 / price, rules.step), rules.step)
    before = b.positions()
    ack = b.place(Order("agent-testnet-ioc-probe", perp, qty, OrderType.LIMIT_IOC, price, reduce_only=False))
    assert ack.status == "NONE", ack
    assert "agent-testnet-ioc-probe" not in b.open_orders()
    assert b.positions() == before
    b.cancel_all()
