"""Paper broker: fills against a live (or replayed) top of book. Nothing is ever sent to an exchange.

Fill model: IOC limits fill at the touch if the limit allows, up to the displayed size (a partial fill
if larger); market orders fill at the touch up to the displayed size. Taker fees on every fill. There's
no queue position and no impact beyond the touch, so paper fills are optimistic for size; the live
cost-drift kill rule exists for that reason. Margin isn't modelled: perps are cash-settled at notional,
the same accounting the backtest engine uses.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from agent.core.types import Instrument, TopOfBook
from agent.data.datasets import Market
from agent.execution.types import Ack, AckStatus, Order, OrderType, SymbolRules

MAX_REMEMBERED_ACKS = 10_000


class PaperBroker:
    def __init__(
        self,
        *,
        cash: float,
        rules: dict[str, SymbolRules],
        book: Callable[[Instrument], TopOfBook | None],
        taker_fee_bps: dict[str, float],
        state_path: Path | None = None,
    ) -> None:
        self._rules, self._book, self._fees, self._path = rules, book, taker_fee_bps, state_path
        self._cash = cash
        self._pos: dict[str, float] = {}
        self._acks: dict[str, Ack] = {}
        if state_path is not None and state_path.exists():
            state = json.loads(state_path.read_text())
            self._cash, self._pos = float(state["cash"]), {k: float(v) for k, v in state["positions"].items()}
            self._acks = {k: Ack(**v) for k, v in state["acks"].items()}

    # -- Broker protocol --------------------------------------------------------------------------------
    def positions(self) -> dict[str, float]:
        return dict(self._pos)

    def cash(self) -> float:
        return self._cash

    def open_orders(self) -> list[str]:
        return []  # IOC and market orders never rest

    def equity(self, marks: dict[str, float]) -> float:
        return self._cash + sum(q * marks[k] for k, q in self._pos.items())

    def rules(self, instrument: Instrument) -> SymbolRules:
        return self._rules[instrument.key]

    def cancel_all(self) -> None:
        return None

    def apply_funding(self, instrument: Instrument, *, rate: float, mark: float) -> float:
        amount = -self._pos.get(instrument.key, 0.0) * mark * rate
        self._cash += amount
        self._save()
        return amount

    def place(self, order: Order) -> Ack:
        if order.client_id in self._acks:
            return self._acks[order.client_id]  # idempotent: a retried order never fills twice
        ack = self._fill(order)
        self._acks[order.client_id] = ack
        if len(self._acks) > MAX_REMEMBERED_ACKS:
            self._acks.pop(next(iter(self._acks)))
        self._save()
        return ack

    # -- internals --------------------------------------------------------------------------------------
    def _reject(self, order: Order, reason: str) -> Ack:
        return Ack(order.client_id, "REJECTED", 0.0, 0.0, 0.0, reason)

    def _fill(self, order: Order) -> Ack:
        key, inst = order.instrument.key, order.instrument
        q = self._book(inst)
        if q is None or order.qty == 0:
            return self._reject(order, "no book" if q is None else "zero quantity")
        side = 1.0 if order.qty > 0 else -1.0
        size, pos = abs(order.qty), self._pos.get(key, 0.0)
        if order.reduce_only:
            if pos == 0 or math.copysign(1.0, pos) == side:
                return self._reject(order, "reduce-only order would increase the position")
            size = min(size, abs(pos))
        price, depth = (q.ask, q.ask_qty) if side > 0 else (q.bid, q.bid_qty)
        if order.type is OrderType.LIMIT_IOC and order.limit_price is not None:
            if (side > 0 and price > order.limit_price) or (side < 0 and price < order.limit_price):
                return Ack(order.client_id, "NONE", 0.0, 0.0, 0.0, "limit not marketable")
        fee_rate = self._fees[inst.market.value] / 1e4
        if inst.market is Market.SPOT:
            if side < 0 and size > pos + 1e-12:
                return self._reject(order, "insufficient base balance")
            if side > 0 and min(size, depth) * price * (1 + fee_rate) > self._cash:
                return self._reject(order, "insufficient quote balance")
        fill = min(size, depth)
        fee = fill * price * fee_rate
        self._cash -= side * fill * price + fee
        new = pos + side * fill
        step = self._rules[key].step
        if abs(new) < step / 2:
            self._pos.pop(key, None)
        else:
            self._pos[key] = new
        status: AckStatus = "FILLED" if math.isclose(fill, size, rel_tol=1e-9) else "PARTIAL"
        return Ack(order.client_id, status, side * fill, price, fee)

    def _save(self) -> None:
        if self._path is None:
            return
        state = {
            "cash": self._cash,
            "positions": self._pos,
            "acks": {k: asdict(v) for k, v in self._acks.items()},
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self._path)
