from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from agent.core.errors import ChecksumMismatch, DataIntegrityError, NotFound
from agent.data.datasets import FUNDING, METRICS, PREMIUM_1H, klines
from agent.data.ingest import BASE_URL, archive_path, extract_csv, ingest, plan_files, verify_checksum
from agent.data.store import PITStore
from tests.fixtures.factories import checksum_text, kline_csv, make_zip

H1 = klines("perp", "1h")
END = pd.Timestamp("2030-01-01", tz="UTC")


@pytest.mark.parametrize(
    ("dataset", "freq", "period", "expected"),
    [
        (klines("spot", "1d"), "monthly", "2024-01", "spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip"),
        (H1, "daily", "2024-01-05", "futures/um/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01-05.zip"),
        (
            FUNDING,
            "monthly",
            "2024-01",
            "futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip",
        ),
        (
            PREMIUM_1H,
            "monthly",
            "2024-01",
            "futures/um/monthly/premiumIndexKlines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip",
        ),
        (METRICS, "daily", "2024-01-05", "futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2024-01-05.zip"),
    ],
)
def test_archive_paths(dataset, freq, period, expected) -> None:  # type: ignore[no-untyped-def]
    assert archive_path(dataset, "BTCUSDT", freq, period) == expected


def test_plan_uses_monthly_files_then_daily_tail() -> None:
    files = plan_files(H1, "BTCUSDT", dt.date(2024, 1, 1), dt.date(2024, 3, 3))
    assert [(f.freq, f.period) for f in files] == [
        ("monthly", "2024-01"),
        ("monthly", "2024-02"),
        ("daily", "2024-03-01"),
        ("daily", "2024-03-02"),
        ("daily", "2024-03-03"),
    ]


def test_plan_metrics_is_daily_only() -> None:
    files = plan_files(METRICS, "BTCUSDT", dt.date(2024, 1, 30), dt.date(2024, 2, 2))
    assert [f.period for f in files] == ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"]
    assert {f.freq for f in files} == {"daily"}


def test_plan_funding_skips_partial_month() -> None:
    """Funding archives are monthly only; the partial month must come from REST (reported, not faked)."""
    files = plan_files(FUNDING, "BTCUSDT", dt.date(2024, 1, 1), dt.date(2024, 2, 10))
    assert [(f.freq, f.period) for f in files] == [("monthly", "2024-01")]


def test_checksum_verification() -> None:
    z = make_zip("BTCUSDT-1h-2024-01.csv", b"x")
    verify_checksum(z, checksum_text(z, "BTCUSDT-1h-2024-01.zip"), "BTCUSDT-1h-2024-01.zip")
    with pytest.raises(ChecksumMismatch):
        verify_checksum(z + b"tamper", checksum_text(z, "BTCUSDT-1h-2024-01.zip"), "BTCUSDT-1h-2024-01.zip")
    with pytest.raises(ChecksumMismatch, match="filename"):
        verify_checksum(z, checksum_text(z, "OTHER.zip"), "BTCUSDT-1h-2024-01.zip")


def test_extract_requires_exactly_one_csv() -> None:
    assert extract_csv(make_zip("a.csv", b"hello")) == b"hello"
    with pytest.raises(DataIntegrityError):
        extract_csv(make_zip("a.txt", b"hello"))


class FakeArchive:
    """In-memory stand-in for data.binance.vision."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []

    def add(self, path: str, csv: bytes, *, checksum: bool = True, tamper: bool = False) -> None:
        name = path.rsplit("/", 1)[1]
        z = make_zip(name.replace(".zip", ".csv"), csv)
        self.files[f"{BASE_URL}/{path}"] = z + (b"!" if tamper else b"")
        if checksum:
            self.files[f"{BASE_URL}/{path}.CHECKSUM"] = checksum_text(z, name)

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url not in self.files:
            raise NotFound(url)
        return self.files[url]


def test_ingest_end_to_end_and_idempotent(store: PITStore) -> None:
    arc = FakeArchive()
    arc.add(archive_path(H1, "BTCUSDT", "monthly", "2024-01"), kline_csv("2024-01-01", 31 * 24))
    arc.add(archive_path(H1, "BTCUSDT", "daily", "2024-02-01"), kline_csv("2024-02-01", 24, seed=1))

    rep = ingest(store, H1, "BTCUSDT", dt.date(2024, 1, 1), dt.date(2024, 2, 1), fetch=arc)
    assert rep.rows_written == 31 * 24 + 24
    assert rep.missing == [] and rep.unverified == []
    assert len(store.read(H1, "BTCUSDT", as_of=END)) == 31 * 24 + 24

    again = ingest(store, H1, "BTCUSDT", dt.date(2024, 1, 1), dt.date(2024, 2, 1), fetch=arc)
    assert again.rows_written == 0


def test_ingest_reports_missing_and_never_writes_unverified(store: PITStore) -> None:
    arc = FakeArchive()
    arc.add(archive_path(H1, "BTCUSDT", "daily", "2024-01-01"), kline_csv("2024-01-01", 24), checksum=False)
    rep = ingest(store, H1, "BTCUSDT", dt.date(2024, 1, 1), dt.date(2024, 1, 2), fetch=arc)
    assert [f.period for f in rep.unverified] == ["2024-01-01"]
    assert [f.period for f in rep.missing] == ["2024-01-02"]
    assert rep.rows_written == 0
    assert store.read(H1, "BTCUSDT", as_of=END).empty


def test_ingest_aborts_on_tampered_file(store: PITStore) -> None:
    arc = FakeArchive()
    arc.add(archive_path(H1, "BTCUSDT", "daily", "2024-01-01"), kline_csv("2024-01-01", 24), tamper=True)
    with pytest.raises(ChecksumMismatch):
        ingest(store, H1, "BTCUSDT", dt.date(2024, 1, 1), dt.date(2024, 1, 1), fetch=arc)
    assert store.read(H1, "BTCUSDT", as_of=END).empty


def test_raw_cache_avoids_refetch(store: PITStore, tmp_path) -> None:  # type: ignore[no-untyped-def]
    arc = FakeArchive()
    arc.add(archive_path(H1, "BTCUSDT", "daily", "2024-01-01"), kline_csv("2024-01-01", 24))
    raw = tmp_path / "raw"
    ingest(store, H1, "BTCUSDT", dt.date(2024, 1, 1), dt.date(2024, 1, 1), fetch=arc, raw_dir=raw)
    n = len(arc.calls)
    ingest(store, H1, "BTCUSDT", dt.date(2024, 1, 1), dt.date(2024, 1, 1), fetch=arc, raw_dir=raw)
    assert len(arc.calls) == n
