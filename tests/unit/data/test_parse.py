from __future__ import annotations

import pandas as pd
import pytest

from agent.core.errors import DataIntegrityError
from agent.data.datasets import FUNDING, METRICS, PREMIUM_1H, klines
from agent.data.parse import parse_csv
from tests.fixtures.factories import funding_csv, kline_csv, metrics_csv

H1 = klines("perp", "1h")


def test_header_and_headerless_klines_parse_identically() -> None:
    a = parse_csv(H1, kline_csv("2024-01-01", 5, header=False))
    b = parse_csv(H1, kline_csv("2024-01-01", 5, header=True))
    pd.testing.assert_frame_equal(a, b)


def test_microsecond_timestamps_parse_like_milliseconds() -> None:
    """data.binance.vision spot archives switched to microseconds from 2025-01-01 [verify in integration]."""
    spot = klines("spot", "1h")
    ms = parse_csv(spot, kline_csv("2025-01-01", 5))
    us = parse_csv(spot, kline_csv("2025-01-01", 5, micros=True))
    pd.testing.assert_series_equal(ms["key_time"], us["key_time"])
    pd.testing.assert_series_equal(ms["available_at"], us["available_at"])


def test_kline_schema_and_availability() -> None:
    df = parse_csv(H1, kline_csv("2024-01-01", 3))
    assert str(df["key_time"].dtype) == "datetime64[ns, UTC]"
    assert df.loc[0, "key_time"] == pd.Timestamp("2024-01-01", tz="UTC")
    assert df.loc[0, "available_at"] == pd.Timestamp("2024-01-01 01:00", tz="UTC")
    for col in ("open", "high", "low", "close", "volume", "quote_volume", "trades"):
        assert col in df.columns
    assert "ignore" not in df.columns


def test_premium_index_parses_as_bars() -> None:
    df = parse_csv(PREMIUM_1H, kline_csv("2024-01-01", 3))
    assert len(df) == 3


def test_funding_parse() -> None:
    df = parse_csv(FUNDING, funding_csv("2024-01-01", 3, rate=0.0001))
    assert list(df["rate"]) == [0.0001] * 3
    assert list(df["interval_hours"]) == [8, 8, 8]
    assert (df["available_at"] == df["key_time"]).all()


def test_metrics_parse_string_timestamps_as_utc() -> None:
    df = parse_csv(METRICS, metrics_csv("2024-01-01", 2))
    assert df.loc[1, "key_time"] == pd.Timestamp("2024-01-01 00:05", tz="UTC")
    assert df.loc[0, "available_at"] == df.loc[0, "key_time"] + METRICS.publication_lag
    assert "sum_open_interest" in df.columns
    assert "symbol" not in df.columns


def test_garbage_csv_is_rejected() -> None:
    with pytest.raises(DataIntegrityError):
        parse_csv(H1, b"not,a,kline\n1,2,3\n")


def test_unsorted_input_is_sorted() -> None:
    raw = kline_csv("2024-01-01", 4).decode().strip().split("\n")
    shuffled = "\n".join([raw[2], raw[0], raw[3], raw[1]]).encode()
    assert parse_csv(H1, shuffled)["key_time"].is_monotonic_increasing
