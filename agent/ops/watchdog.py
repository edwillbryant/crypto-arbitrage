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
from agent.execution.types import Ack, Broker, Order, OrderType


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
