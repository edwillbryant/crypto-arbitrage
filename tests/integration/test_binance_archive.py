"""Verifies the archive-format assumptions against the real data.binance.vision. Opt-in: pytest -m integration."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from agent.data.datasets import FUNDING, METRICS, klines
from agent.data.ingest import ingest
from agent.data.store import PITStore

pytestmark = pytest.mark.integration
END = pd.Timestamp("2030-01-01", tz="UTC")


@pytest.mark.parametrize(
    ("dataset", "start", "end", "rows"),
    [
        (klines("perp", "1d"), dt.date(2024, 1, 1), dt.date(2024, 1, 31), 31),
        (klines("spot", "1d"), dt.date(2025, 1, 1), dt.date(2025, 1, 31), 31),  # microsecond-era spot file
        (FUNDING, dt.date(2024, 1, 1), dt.date(2024, 1, 31), 93),
        (METRICS, dt.date(2024, 1, 1), dt.date(2024, 1, 1), 288),
    ],
)
def test_real_archive_matches_parser_assumptions(tmp_path, dataset, start, end, rows) -> None:  # type: ignore[no-untyped-def]
    store = PITStore(tmp_path)
    rep = ingest(store, dataset, "BTCUSDT", start, end)
    assert rep.missing == [] and rep.unverified == []
    df = store.read(dataset, "BTCUSDT", as_of=END)
    assert len(df) == rows
    assert df["key_time"].min() == pd.Timestamp(start, tz="UTC")
