"""Turns RiskGate decisions into orders.

- Quantities round TOWARD ZERO to the exchange step: rounding can never add exposure.
- Normal orders are IOC limits capped at mark +/- max_slippage. Market orders are used only to flatten.
- Any order that only shrinks a perp position is sent reduce-only, so it can never open or flip one.
- Exchange positions that no decision covers (orphans) are flattened.
- Hedge groups: spot leg first, then the perp sized to what the spot actually filled. If the hedge leg
  under-fills, the excess of the first leg is unwound. A group is never left half-built on purpose.
- Client order ids are deterministic per (cycle, instrument, leg), so retries can't duplicate orders.
- Exchange errors and rejects are reported via `on_error` (they feed the RiskGate's API-error halt),
  never raised: one bad instrument must not stop the rest of the cycle.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from agent.core.types import Instrument, Target
from agent.data.datasets import Market
from agent.execution.types import Ack, Broker, Order, OrderType
from agent.risk.gate import GateResult, Mode


def client_order_id(cycle_id: str, key: str, leg: str) -> str:
    """32 chars, [0-9a-z]: within Binance's 36-character clientOrderId limit."""
    return "ag" + hashlib.sha1(f"{cycle_id}|{key}|{leg}".encode()).hexdigest()[:30]


def floor_to_step(qty: float, step: float) -> float:
    return math.copysign(math.floor(abs(qty) / step + 1e-9) * step, qty) if qty else 0.0


def round_to_tick(price: float, tick: float, *, up: bool) -> float:
    n = price / tick
    if abs(n - round(n)) < 1e-6:  # 50050.0 / 0.1 is 500499.99999...: snap float noise first
        n = round(n)
    return round((math.ceil(n) if up else math.floor(n)) * tick, 10)


@dataclass
class ExecutionReport:
    orders: list[Order] = field(default_factory=list)
    acks: list[Ack] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


@dataclass
class _Leg:
    target: Target
    position: float
    mark: float
    delta: float
    going_flat: bool


class OrderManager:
    def __init__(
        self,
        broker: Broker,
        *,
        max_slippage_bps: float = 10.0,
        min_trade_weight: float = 0.01,
        on_error: Callable[[str, str], None],
    ) -> None:
        self.broker, self.max_slippage_bps, self.min_trade_weight = broker, max_slippage_bps, min_trade_weight
        self.on_error = on_error

    def execute(
        self, result: GateResult, *, now: pd.Timestamp, equity: float, marks: dict[str, float], cycle_id: str
    ) -> ExecutionReport:
        rep = ExecutionReport()
        if result.cancel_all:
            self._call("cancel_all", "*", self.broker.cancel_all)
        if not result.submit_orders:
            return rep
        positions = self._call("positions", "*", self.broker.positions)
        if positions is None:
            rep.events.append("positions unavailable; no orders sent")
            return rep
        targets = [d.target for d in result.decisions]
        covered = {t.instrument.key for t in targets}
        for key in sorted(set(positions) - covered):
            rep.events.append(f"orphan position {key} ({positions[key]}): flattening")
            targets.append(Target(Instrument.from_key(key), 0.0, None, "orphan"))

        groups: dict[str, list[Target]] = {}
        for i, t in enumerate(targets):
            groups.setdefault(t.group if t.group is not None else f"\0{i}", []).append(t)
        flatten = result.mode is Mode.FLATTEN
        for members in groups.values():
            legs = [self._leg(t, positions, marks, equity, rep) for t in members]
            if any(leg is None for leg in legs):
                continue  # a leg without a mark: don't trade half a group
            live = [leg for leg in legs if leg is not None and leg.delta != 0]
            live.sort(key=lambda leg: leg.target.instrument.market is not Market.SPOT)  # spot first
            if flatten or len(live) < 2:
                for leg in live:
                    self._send(leg, leg.delta, cycle_id, "main", rep, market=flatten)
                continue
            self._hedged(live, cycle_id, rep)
        return rep

    # -- planning ---------------------------------------------------------------------------------------
    def _leg(
        self,
        t: Target,
        positions: dict[str, float],
        marks: dict[str, float],
        equity: float,
        rep: ExecutionReport,
    ) -> _Leg | None:
        key = t.instrument.key
        pos = positions.get(key, 0.0)
        mark = marks.get(key)
        if mark is None or not mark > 0:
            if t.weight != 0 or pos != 0:
                rep.events.append(f"{key}: no mark; skipped")
            return None
        rules = self.broker.rules(t.instrument)
        going_flat = t.weight == 0 and pos != 0
        delta = -pos if going_flat else floor_to_step(t.weight * equity / mark, rules.step) - pos
        delta = round(delta, 12)
        notional = abs(delta) * mark
        if going_flat and t.instrument.market is Market.SPOT and notional < rules.min_notional:
            # Spot dust can't be sold (no reduce-only exemption); retrying would only rack up rejects.
            rep.events.append(f"{key}: dust {pos} below min notional; left in place")
            delta = 0.0
        elif not going_flat and (notional < rules.min_notional or notional < self.min_trade_weight * equity):
            delta = 0.0
        return _Leg(t, pos, mark, delta, going_flat)

    def _hedged(self, legs: list[_Leg], cycle_id: str, rep: ExecutionReport) -> None:
        first, rest = legs[0], legs[1:]
        ack = self._send(first, first.delta, cycle_id, "main", rep)
        filled = ack.filled_qty if ack else 0.0
        ratio = filled / first.delta if first.delta else 0.0
        worst = 1.0
        for leg in rest:
            want = floor_to_step(leg.delta * ratio, self.broker.rules(leg.target.instrument).step)
            if want == 0:
                worst = min(worst, 0.0 if ratio > 0 else 1.0)
                continue
            a = self._send(leg, want, cycle_id, "hedge", rep)
            worst = min(worst, (a.filled_qty / want) if a else 0.0)
        if filled and worst < 1.0:
            excess = floor_to_step(-filled * (1 - worst), self.broker.rules(first.target.instrument).step)
            if excess:
                rep.events.append(
                    f"hedge leg under-filled ({worst:.0%}); unwound {excess} of {first.target.instrument.key}"
                )
                self._send(first, excess, cycle_id, "unwind", rep, market=True)

    # -- sending ----------------------------------------------------------------------------------------
    def _send(
        self, leg: _Leg, qty: float, cycle_id: str, tag: str, rep: ExecutionReport, *, market: bool = False
    ) -> Ack | None:
        inst = leg.target.instrument
        rules = self.broker.rules(inst)
        is_perp = inst.market is Market.PERP
        pos_after_prior = leg.position + sum(a.filled_qty for o, a in zip(rep.orders, rep.acks, strict=True)
                                             if o.instrument == inst)  # fmt: skip
        reduces = (
            pos_after_prior != 0
            and (qty > 0) != (pos_after_prior > 0)
            and abs(qty) <= abs(pos_after_prior) + 1e-12
        )
        if market:
            otype, price = OrderType.MARKET, None
        else:
            slip = self.max_slippage_bps / 1e4
            cap = leg.mark * (1 + slip) if qty > 0 else leg.mark * (1 - slip)
            price = round_to_tick(cap, rules.tick, up=qty < 0)  # never worse than the cap
            otype = OrderType.LIMIT_IOC
        order = Order(client_order_id(cycle_id, inst.key, tag), inst, qty, otype, price, is_perp and reduces)
        ack = self._call("place", inst.key, lambda: self.broker.place(order))
        if ack is None:
            ack = Ack(order.client_id, "REJECTED", 0.0, 0.0, 0.0, "exception")
        elif ack.status == "REJECTED":
            self.on_error("reject", f"{inst.key}: {ack.reason}")
        rep.orders.append(order)
        rep.acks.append(ack)
        return ack

    def _call[R](self, what: str, key: str, fn: Callable[[], R]) -> R | None:
        try:
            return fn()
        except Exception as e:  # broad on purpose - every exchange failure counts towards the API-error halt
            self.on_error("error", f"{key}: {what} failed: {e}")
            return None
