from __future__ import annotations

import pytest

from agent.core.types import Instrument
from agent.research.costs import CostModel

BTC_PERP = Instrument("BTCUSDT", "perp")
ETH_SPOT = Instrument("ETHUSDT", "spot")


def test_buy_pays_up_sell_receives_less() -> None:
    cm = CostModel(
        taker_fee_bps={"perp": 5.0, "spot": 10.0}, slippage_bps={"BTCUSDT": 2.0}, default_slippage_bps=3.0
    )
    buy = cm.fill(BTC_PERP, qty=2.0, reference_price=100.0)
    assert buy.price == pytest.approx(100.02)
    assert buy.fee == pytest.approx(2.0 * 100.02 * 0.0005)
    assert buy.slippage_cost == pytest.approx(2.0 * 0.02)
    sell = cm.fill(ETH_SPOT, qty=-1.0, reference_price=50.0)
    assert sell.price == pytest.approx(50.0 * (1 - 0.0003))
    assert sell.fee == pytest.approx(50.0 * (1 - 0.0003) * 0.001)


def test_cost_multiplier_scales_fees_and_slippage_exactly() -> None:
    base = CostModel()
    double = base.scaled(2.0)
    a = base.fill(BTC_PERP, 1.0, 1000.0)
    b = double.fill(BTC_PERP, 1.0, 1000.0)
    assert b.price - 1000.0 == pytest.approx(2 * (a.price - 1000.0))
    assert b.fee / b.price == pytest.approx(2 * a.fee / a.price)


def test_zero_cost_model() -> None:
    f = CostModel.zero().fill(BTC_PERP, 1.0, 100.0)
    assert (f.price, f.fee, f.slippage_cost) == (100.0, 0.0, 0.0)


def test_negative_multiplier_rejected() -> None:
    with pytest.raises(ValueError):
        CostModel().scaled(-1.0)
