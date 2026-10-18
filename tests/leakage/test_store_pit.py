"""Point-in-time guarantees of the store. If any of these fail, no backtest result can be trusted."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent.core.errors import DataIntegrityError
from agent.data.datasets import FUNDING, METRICS, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from tests.fixtures.factories import funding_csv, kline_csv, metrics_csv

H1 = klines("perp", "1h")
T0 = pd.Timestamp("2024-01-01", tz="UTC")


def _bars(n: int = 48, start: str = "2024-01-01", seed: int = 0) -> pd.DataFrame:
    return parse_csv(H1, kline_csv(start, n, "1h", seed=seed))


def test_bar_not_visible_until_it_has_closed(store: PITStore) -> None:
    store.write(H1, "BTCUSDT", _bars(3))
    just_before_close = T0 + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1)
    assert store.read(H1, "BTCUSDT", as_of=just_before_close).empty
    at_close = store.read(H1, "BTCUSDT", as_of=T0 + pd.Timedelta(hours=1))
    assert list(at_close["key_time"]) == [T0]


@settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(n=st.integers(1, 80), offset_min=st.integers(-120, 90 * 60))
def test_read_returns_exactly_the_available_rows(tmp_path: Path, n: int, offset_min: int) -> None:
    store = PITStore(tmp_path / f"s{n}_{offset_min}")
    bars = _bars(n)
    store.write(H1, "BTCUSDT", bars)
    as_of = T0 + pd.Timedelta(minutes=offset_min)
    got = store.read(H1, "BTCUSDT", as_of=as_of)
    assert (got["available_at"] <= as_of).all()
    expected = bars[bars["available_at"] <= as_of]
    assert list(got["key_time"]) == list(expected["key_time"])


def test_future_poisoning_does_not_change_the_past(store: PITStore) -> None:
    """Append garbage after t: everything readable at t must be byte-identical."""
    store.write(H1, "BTCUSDT", _bars(24))
    as_of = T0 + pd.Timedelta(hours=24)
    before = store.read(H1, "BTCUSDT", as_of=as_of)

    future = _bars(24, start="2024-01-02", seed=99)
    future[["open", "high", "low", "close"]] = 1e9
    store.write(H1, "BTCUSDT", future)

    after = store.read(H1, "BTCUSDT", as_of=as_of)
    pd.testing.assert_frame_equal(before, after)


def test_funding_visible_only_from_settlement_time(store: PITStore) -> None:
    store.write(FUNDING, "BTCUSDT", parse_csv(FUNDING, funding_csv("2024-01-01", 3)))
    settle = T0 + pd.Timedelta(hours=8)
    assert len(store.read(FUNDING, "BTCUSDT", as_of=settle - pd.Timedelta(milliseconds=1))) == 1
    assert len(store.read(FUNDING, "BTCUSDT", as_of=settle)) == 2


def test_metrics_respect_publication_lag(store: PITStore) -> None:
    store.write(METRICS, "BTCUSDT", parse_csv(METRICS, metrics_csv("2024-01-01", 1)))
    assert store.read(METRICS, "BTCUSDT", as_of=T0).empty
    assert len(store.read(METRICS, "BTCUSDT", as_of=T0 + METRICS.publication_lag)) == 1


def test_naive_as_of_is_rejected(store: PITStore) -> None:
    store.write(H1, "BTCUSDT", _bars(2))
    with pytest.raises(ValueError, match="timezone"):
        store.read(H1, "BTCUSDT", as_of=pd.Timestamp("2024-01-02"))


def test_write_rejects_rows_available_before_the_bar_closed(store: PITStore) -> None:
    bars = _bars(2)
    bars.loc[1, "available_at"] = bars.loc[1, "key_time"]
    with pytest.raises(DataIntegrityError, match="available_at"):
        store.write(H1, "BTCUSDT", bars)


def test_returned_frame_cannot_mutate_the_store(store: PITStore) -> None:
    store.write(H1, "BTCUSDT", _bars(5))
    as_of = T0 + pd.Timedelta(days=1)
    first = store.read(H1, "BTCUSDT", as_of=as_of)
    first.loc[:, "close"] = -1.0
    assert (store.read(H1, "BTCUSDT", as_of=as_of)["close"] > 0).all()
