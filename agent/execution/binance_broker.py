"""Binance broker: the Broker protocol over two ccxt clients (spot, USDⓈ-M futures), both in SafeExchange.

Only the allow-listed methods are reachable, so this class cannot withdraw, transfer or convert, whatever its
callers do. Outcome rules:
- an exchange *rejection* (bad order, insufficient funds/margin) returns a REJECTED ack;
- a network error or timeout *raises*: the order's fate is unknown, and guessing would desync the books.
  The order manager reports it, the API-error counter halts on repeats, and the reconciler finds out.
- MARKET orders exist only for flattening: reduce-only on perps, sells on spot. Anything else is refused.

[UNVERIFIED until the opt-in testnet order test passes] the futures balance's `total.USDT` is the wallet
balance (unrealized PnL excluded), so unrealized PnL is added from positions. Fees charged in anything but
USDT (spot buys pay in the base asset, or BNB if enabled) are reported as 0 here; balances and positions are
always re-read from the exchange, so they stay right even when the ack's fee is understated.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import ccxt

from agent.core.types import Instrument
from agent.data.datasets import Market
from agent.execution.exchange_whitelist import SafeExchange
from agent.execution.types import Ack, AckStatus, Order, OrderType, SymbolRules

QUOTE = "USDT"


def ccxt_symbol(inst: Instrument) -> str:
    if not inst.symbol.endswith(QUOTE):
        raise ValueError(f"only {QUOTE}-quoted instruments are supported: {inst.symbol}")
    base = inst.symbol[: -len(QUOTE)]
    return f"{base}/{QUOTE}:{QUOTE}" if inst.market is Market.PERP else f"{base}/{QUOTE}"


class BinanceBroker:
    def __init__(self, *, spot: SafeExchange, perp: SafeExchange, instruments: Iterable[Instrument]) -> None:
        if not (isinstance(spot, SafeExchange) and isinstance(perp, SafeExchange)):
            raise TypeError("BinanceBroker only accepts clients wrapped in SafeExchange")
        self._ex = {Market.SPOT: spot, Market.PERP: perp}
        self.instruments = sorted(set(instruments), key=lambda i: i.key)
        self._rules: dict[str, SymbolRules] = {}

    def _client(self, inst: Instrument) -> SafeExchange:
        return self._ex[inst.market]

    def rules(self, instrument: Instrument) -> SymbolRules:
        if not self._rules:
            for market, ex in self._ex.items():
                markets = ex.load_markets()
                for inst in self.instruments:
                    if inst.market is market:
                        m = markets[ccxt_symbol(inst)]
                        self._rules[inst.key] = SymbolRules(
                            step=float(m["precision"]["amount"]),
                            tick=float(m["precision"]["price"]),
                            min_notional=float(m["limits"]["cost"]["min"] or 0.0),
                        )
        return self._rules[instrument.key]

    def positions(self) -> dict[str, float]:
        out: dict[str, float] = {}
        perps = [i for i in self.instruments if i.market is Market.PERP]
        if perps:
            by_symbol = {ccxt_symbol(i): i for i in perps}
            for p in self._ex[Market.PERP].fetch_positions(list(by_symbol)):
                inst = by_symbol.get(p["symbol"])
                qty = float(p.get("contracts") or 0.0)
                if inst is not None and qty:
                    out[inst.key] = -qty if p.get("side") == "short" else qty
        spots = [i for i in self.instruments if i.market is Market.SPOT]
        if spots:
            total = self._ex[Market.SPOT].fetch_balance()["total"]
            for inst in spots:
                held = float(total.get(inst.symbol[: -len(QUOTE)]) or 0.0)
                if held:
                    out[inst.key] = held
        return out

    def equity(self, marks: dict[str, float]) -> float:
        """Both wallets in USDT. Assets outside the instrument list (e.g. BNB for fees) are not counted."""
        spot_total = self._ex[Market.SPOT].fetch_balance()["total"]
        eq = float(spot_total.get(QUOTE) or 0.0)
        for inst in self.instruments:
            if inst.market is Market.SPOT:
                eq += float(spot_total.get(inst.symbol[: -len(QUOTE)]) or 0.0) * marks[inst.key]
        eq += float(self._ex[Market.PERP].fetch_balance()["total"].get(QUOTE) or 0.0)
        perps = [ccxt_symbol(i) for i in self.instruments if i.market is Market.PERP]
        if perps:
            eq += sum(
                float(p.get("unrealizedPnl") or 0.0) for p in self._ex[Market.PERP].fetch_positions(perps)
            )
        return eq

    def open_orders(self) -> list[str]:
        ids: list[str] = []
        for inst in self.instruments:
            ids += [
                str(o.get("clientOrderId")) for o in self._client(inst).fetch_open_orders(ccxt_symbol(inst))
            ]
        return ids

    def cancel_all(self) -> None:
        for inst in self.instruments:
            self._client(inst).cancel_all_orders(ccxt_symbol(inst))

    def place(self, order: Order) -> Ack:
        inst, perp = order.instrument, order.instrument.market is Market.PERP
        if (
            order.type is OrderType.MARKET
            and not (perp and order.reduce_only)
            and not (not perp and order.qty < 0)
        ):
            raise ValueError("MARKET orders are only for flattening: reduce-only on perps, sells on spot")
        params: dict[str, Any] = {"newClientOrderId": order.client_id}
        if order.type is OrderType.LIMIT_IOC:
            params["timeInForce"] = "IOC"
        if perp and order.reduce_only:
            params["reduceOnly"] = True
        side = "buy" if order.qty > 0 else "sell"
        kind = "limit" if order.type is OrderType.LIMIT_IOC else "market"
        try:
            res = self._client(inst).create_order(
                ccxt_symbol(inst), kind, side, abs(order.qty), order.limit_price, params=params
            )
        except (ccxt.InvalidOrder, ccxt.InsufficientFunds, ccxt.BadRequest, ccxt.PermissionDenied) as e:
            return Ack(order.client_id, "REJECTED", 0.0, 0.0, 0.0, f"{type(e).__name__}: {e}")
        filled = float(res.get("filled") or 0.0)
        fee = res.get("fee") or {}
        fee_usdt = float(fee.get("cost") or 0.0) if fee.get("currency") == QUOTE else 0.0
        sign = 1.0 if order.qty > 0 else -1.0
        status: AckStatus = (
            "FILLED" if filled >= abs(order.qty) - 1e-12 else "PARTIAL" if filled > 0 else "NONE"
        )
        return Ack(
            order.client_id,
            status,
            sign * filled,
            float(res.get("average") or 0.0) if filled else 0.0,
            fee_usdt,
        )
