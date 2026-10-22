"""Watchdog for a second host: if the primary goes silent, cancel everything and go flat.

It runs on a different machine and provider, with its own trade-only API key. It acts once per outage
and re-arms when heartbeats resume. Everything is flattened (perps reduce-only, spot sold), because
flattening only perps would turn a hedged carry pair into a naked spot long.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from agent.core.types import Instrument
from agent.data.datasets import Market
from agent.execution.order_manager import client_order_id
from agent.execution.types import Ack, Broker, Order, OrderType, SymbolRules


@dataclass
class WatchdogAction:
    at: pd.Timestamp
    orders: list[Order] = field(default_factory=list)
    acks: list[Ack] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Watchdog:
    def __init__(self, broker: Broker, *, silence: pd.Timedelta = pd.Timedelta(seconds=60)) -> None:
        self.broker, self.silence = broker, silence
        self._last: pd.Timestamp | None = None
        self._fired = False

    @property
    def last_heartbeat(self) -> pd.Timestamp | None:
        return self._last

    def heartbeat(self, t: pd.Timestamp) -> None:
        self._last, self._fired = t, False

    def check(self, now: pd.Timestamp) -> WatchdogAction | None:
        if self._last is None or self._fired or now - self._last <= self.silence:
            return None
        self._fired = True
        action = WatchdogAction(now)
        try:
            self.broker.cancel_all()
        except Exception as e:  # broad on purpose - keep going: flattening matters more than the cancel
            action.errors.append(f"cancel_all failed: {e}")
        try:
            positions = self.broker.positions()
        except Exception as e:  # broad on purpose
            action.errors.append(f"positions failed: {e}")
            return action
        for key, qty in sorted(positions.items()):
            inst = Instrument.from_key(key)
            perp = inst.market is Market.PERP
            if not perp and qty <= 0:
                continue
            order = Order(
                client_order_id(f"watchdog-{now.isoformat()}", key, "flat"),
                inst,
                -qty,
                OrderType.MARKET,
                None,
                perp,
            )
            try:
                action.acks.append(self.broker.place(order))
                action.orders.append(order)
            except Exception as e:  # broad on purpose
                action.errors.append(f"{key}: {e}")
        return action


class NoPositionsBroker:
    """Stands in for the exchange broker while the primary is paper trading: paper positions exist only
    on the primary, so a watchdog on another host has nothing to cancel or flatten and can only page the
    owner. Live mode replaces this with a BinanceBroker on the watchdog's own trade-only key."""

    def cancel_all(self) -> None:
        return None

    def positions(self) -> dict[str, float]:
        return {}

    def open_orders(self) -> list[str]:
        return []

    def equity(self, marks: dict[str, float]) -> float:
        return 0.0

    def rules(self, instrument: Instrument) -> SymbolRules:
        raise RuntimeError("paper watchdog has no symbol rules")

    def place(self, order: Order) -> Ack:
        raise RuntimeError("paper watchdog never places orders")
