"""Transaction-cost model. Every fill pays a taker fee and slippage against the reference price.

Defaults are [ASSUMPTION]s to be confirmed against Binance's live fee page and recorded book data:
VIP0 taker 5 bps (USDⓈ-M perps) / 10 bps (spot), slippage 2 bps BTC / 3 bps others per side.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.core.types import Instrument


@dataclass(frozen=True)
class Fill:
    price: float
    fee: float
    slippage_cost: float


@dataclass(frozen=True)
class CostModel:
    taker_fee_bps: dict[str, float] = field(default_factory=lambda: {"perp": 5.0, "spot": 10.0})
    slippage_bps: dict[str, float] = field(default_factory=lambda: {"BTCUSDT": 2.0})
    default_slippage_bps: float = 3.0
    multiplier: float = 1.0

    @classmethod
    def zero(cls) -> CostModel:
        return cls(taker_fee_bps={"perp": 0.0, "spot": 0.0}, slippage_bps={}, default_slippage_bps=0.0)

    def scaled(self, multiplier: float) -> CostModel:
        if multiplier < 0:
            raise ValueError("cost multiplier must be >= 0")
        return CostModel(self.taker_fee_bps, self.slippage_bps, self.default_slippage_bps, multiplier)

    def fill(self, instrument: Instrument, qty: float, reference_price: float) -> Fill:
        side = 1.0 if qty > 0 else -1.0
        slip = self.slippage_bps.get(instrument.symbol, self.default_slippage_bps) * self.multiplier / 1e4
        price = reference_price * (1 + side * slip)
        fee = abs(qty) * price * self.taker_fee_bps[instrument.market.value] * self.multiplier / 1e4
        return Fill(price, fee, abs(qty) * abs(price - reference_price))
