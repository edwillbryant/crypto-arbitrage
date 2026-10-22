"""BinanceRestFeed against the real futures testnet (public endpoints, no keys). Opt-in: -m integration."""

from __future__ import annotations

import pandas as pd
import pytest

from agent.core.types import Instrument
from agent.live.binance_feed import FUTURES_TESTNET, BinanceRestFeed
from agent.risk.gate import GateContext, Health, LedgerState, Mode, assess
from agent.risk.limits import Limits

pytestmark = pytest.mark.integration


def test_live_testnet_feed_is_fresh_and_healthy() -> None:
    perps = [Instrument("BTCUSDT", "perp"), Instrument("ETHUSDT", "perp")]
    feed = BinanceRestFeed(
        instruments=perps, futures_base=FUTURES_TESTNET, spot_base="https://unused.invalid"
    )
    now = pd.Timestamp.now(tz="UTC")
    feed.advance(now)
    assert feed.drain_errors() == []
    assert feed.book_age_s(pd.Timestamp.now(tz="UTC")) < 5
    assert feed.bars_late(now) == 0
    assert feed.clock_drift_ms() < 1000
    assert feed.depeg() < 0.01
    assert all(feed.mark(i) for i in perps)
    assert feed.funding_settlements(perps[0], now - pd.Timedelta(days=2), now)
    h = Health(
        book_age_s=feed.book_age_s(pd.Timestamp.now(tz="UTC")),
        bars_late=feed.bars_late(now),
        clock_drift_ms=feed.clock_drift_ms(),
        depeg=feed.depeg(),
    )
    mode, triggers, _ = assess(GateContext(now, 10_000.0), h, LedgerState(10_000.0, 10_000.0), Limits())
    assert mode is Mode.NORMAL, triggers
