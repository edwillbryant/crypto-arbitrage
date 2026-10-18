from __future__ import annotations

import pandas as pd
import pytest

from agent.core.errors import DataConflict, DataIntegrityError
from agent.data.datasets import klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from tests.fixtures.factories import kline_csv

H1 = klines("perp", "1h")
END = pd.Timestamp("2030-01-01", tz="UTC")


def _bars(start: str, n: int, seed: int = 0) -> pd.DataFrame:
    return parse_csv(H1, kline_csv(start, n, "1h", seed=seed))


def test_rewriting_identical_rows_is_idempotent(store: PITStore) -> None:
    bars = _bars("2024-01-01", 10)
    assert store.write(H1, "BTCUSDT", bars) == 10
    assert store.write(H1, "BTCUSDT", bars) == 0
    assert len(store.read(H1, "BTCUSDT", as_of=END)) == 10


def test_revised_history_is_a_conflict_not_a_silent_overwrite(store: PITStore) -> None:
    store.write(H1, "BTCUSDT", _bars("2024-01-01", 10))
    revised = _bars("2024-01-01", 10)
    revised.loc[3, "close"] = revised.loc[3, "close"] * 1.01
    with pytest.raises(DataConflict):
        store.write(H1, "BTCUSDT", revised)


def test_duplicate_keys_within_one_write_are_rejected(store: PITStore) -> None:
    bars = _bars("2024-01-01", 3)
    with pytest.raises(DataIntegrityError, match="duplicate"):
        store.write(H1, "BTCUSDT", pd.concat([bars, bars.iloc[[0]]], ignore_index=True))


def test_reads_span_month_partitions(store: PITStore) -> None:
    store.write(H1, "BTCUSDT", _bars("2024-01-31", 48))
    got = store.read(H1, "BTCUSDT", as_of=END)
    assert len(got) == 48
    assert got["key_time"].is_monotonic_increasing
    assert {p.name for p in store.partitions(H1, "BTCUSDT")} == {"2024-01.parquet", "2024-02.parquet"}


def test_lookback_limits_history(store: PITStore) -> None:
    store.write(H1, "BTCUSDT", _bars("2024-01-01", 48))
    as_of = pd.Timestamp("2024-01-03", tz="UTC")
    got = store.read(H1, "BTCUSDT", as_of=as_of, lookback=pd.Timedelta(hours=6))
    assert len(got) == 6
    assert got["key_time"].min() == as_of - pd.Timedelta(hours=6)


@pytest.mark.parametrize("bad", ["../etc", "btcusdt", "BTC/USDT", ""])
def test_symbol_names_are_validated(store: PITStore, bad: str) -> None:
    with pytest.raises(ValueError, match="symbol"):
        store.read(H1, bad, as_of=END)


def test_unknown_symbol_reads_empty(store: PITStore) -> None:
    assert store.read(H1, "ETHUSDT", as_of=END).empty


def test_schema_is_enforced(store: PITStore) -> None:
    bars = _bars("2024-01-01", 3).drop(columns=["close"])
    with pytest.raises(DataIntegrityError, match="close"):
        store.write(H1, "BTCUSDT", bars)
