"""Order and fill types shared by brokers and the order manager."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from agent.core.types import Instrument

AckStatus = Literal["FILLED", "PARTIAL", "NONE", "REJECTED"]


class OrderType(StrEnum):
    LIMIT_IOC = "LIMIT_IOC"  # normal trading: immediate-or-cancel with a price cap
    MARKET = "MARKET"  # only for flattening, and then reduce-only on perps


@dataclass(frozen=True)
class SymbolRules:
    step: float  # quantity increment
    tick: float  # price increment
    min_notional: float


@dataclass(frozen=True)
class Order:
    client_id: str
    instrument: Instrument
    qty: float  # signed: + buy, - sell
    type: OrderType
    limit_price: float | None
    reduce_only: bool


@dataclass(frozen=True)
class Ack:
    client_id: str
    status: AckStatus
    filled_qty: float  # signed
    avg_price: float
    fee: float
    reason: str = ""


class Broker(Protocol):
    def positions(self) -> dict[str, float]: ...  # instrument key -> signed quantity (spot: base held)

    def open_orders(self) -> list[str]: ...

    def equity(self, marks: dict[str, float]) -> float: ...

    def rules(self, instrument: Instrument) -> SymbolRules: ...

    def place(self, order: Order) -> Ack: ...

    def cancel_all(self) -> None: ...
