"""BinanceRestFeed against recorded testnet responses (fapi) and the documented spot format."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from agent.core.types import Instrument
from agent.data.datasets import klines
from agent.live.binance_feed import BinanceRestFeed
from agent.strategies.base import DataRequest

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "binance"
SERVER = pd.Timestamp(json.loads((FIX / "fapi_time.json").read_text())["serverTime"], unit="ms", tz="UTC")
PERP, SPOT = Instrument("BTCUSDT", "perp"), Instrument("BTCUSDT", "spot")


class FakeGet:
    """Maps URL paths to fixtures; any route can be made to fail."""

    def __init__(self) -> None:
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def __call__(self, url: str) -> Any:
        self.calls.append(url)
        path = url.split("binance", 1)[-1]
        for key in self.fail:
            if key in path:
                raise ConnectionError(f"simulated failure on {key}")
        for route, name in (
            ("/fapi/v1/time", "fapi_time"),
            ("/api/v3/time", "fapi_time"),
            ("ticker/bookTicker?symbol=USDCUSDT", "fapi_book_usdc"),
            ("/fapi/v1/ticker/bookTicker", "fapi_book_btc"),
            ("/api/v3/ticker/bookTicker", "spot_book_btc"),
            ("/fapi/v1/premiumIndex", "fapi_premium_btc"),
            ("/fapi/v1/fundingRate", "fapi_funding_btc"),
            ("klines", "fapi_klines_btc_1h"),
        ):
            if route in path:
                return json.loads((FIX / f"{name}.json").read_text())
        raise AssertionError(f"unexpected url {url}")


def _feed(get: FakeGet, now: pd.Timestamp = SERVER) -> BinanceRestFeed:
    f = BinanceRestFeed(
        instruments=[PERP, SPOT],
        get=get,
        local_now=lambda: now,
        futures_base="https://testnet.binancefuture.com",
        spot_base="https://api.binance.com",
    )
    f.advance(now)
    return f


def test_books_marks_and_age() -> None:
    f = _feed(FakeGet())
    b = f.book(PERP)
    assert b is not None and (b.bid, b.ask) == (81548.60, 81549.70) and b.bid_qty == pytest.approx(615.5921)
    assert f.mark(PERP) == pytest.approx((81548.60 + 81549.70) / 2)
    assert f.book(SPOT) is not None and f.book(SPOT).ts == SERVER  # type: ignore[union-attr]  # receive time
    assert 0 <= f.book_age_s(SERVER) < 2
    assert f.book_age_s(SERVER + pd.Timedelta(seconds=70)) > 60


def test_only_closed_bars_reach_strategies() -> None:
    f = _feed(FakeGet())
    snap = f.snapshot([DataRequest(klines("perp", "1h"), "BTCUSDT", pd.Timedelta(days=1))], SERVER)
    bars = snap.get(klines("perp", "1h"), "BTCUSDT")
    assert (bars["available_at"] <= SERVER).all()
    assert len(bars) == 4  # the fixture's 5th kline is the bar still forming
    assert f.closes(PERP, pd.Timedelta(days=1), SERVER).iloc[-1] == bars["close"].iloc[-1]


def test_bars_late_and_clock_drift() -> None:
    f = _feed(FakeGet())
    assert f.bars_late(SERVER) == 0
    assert f.bars_late(SERVER + pd.Timedelta(hours=3)) == 3  # no newer closed bar fetched
    late = _feed(FakeGet(), now=SERVER + pd.Timedelta(seconds=2))
    assert late.clock_drift_ms() == pytest.approx(2000, abs=50)


def test_depeg_from_usdc_usdt() -> None:
    assert _feed(FakeGet()).depeg() == pytest.approx(abs(1 - (1.0003450 + 1.0003460) / 2), rel=1e-6)


def test_funding_settlements_are_snapped_to_the_minute() -> None:
    f = _feed(FakeGet())
    s = f.funding_settlements(PERP, SERVER - pd.Timedelta(days=3), SERVER)
    assert s and all(t.second == 0 and t.microsecond == 0 for t, _ in s)


def test_failures_are_collected_not_raised_and_data_goes_stale() -> None:
    get = FakeGet()
    f = _feed(get)
    get.fail = {"bookTicker"}
    f.advance(SERVER + pd.Timedelta(seconds=90))
    errors = f.drain_errors()
    assert any("bookTicker" in e for e in errors) and f.drain_errors() == []
    assert f.book_age_s(SERVER + pd.Timedelta(seconds=90)) > 60  # the old quote is kept but flagged stale
