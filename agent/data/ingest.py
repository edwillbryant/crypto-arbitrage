"""Downloader for Binance public archives (data.binance.vision), with checksum verification.

Every archive must match its published `.CHECKSUM` (SHA-256) before a single row reaches the store.
Files without a checksum are reported as unverified and skipped, never trusted.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import http.client
import io
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agent.core.errors import ChecksumMismatch, DataIntegrityError, NotFound
from agent.data.datasets import Dataset, Kind, Market
from agent.data.parse import parse_csv
from agent.data.store import PITStore

BASE_URL = "https://data.binance.vision/data"
Freq = Literal["monthly", "daily"]
Fetch = Callable[[str], bytes]

# [UNVERIFIED] which archive frequencies Binance publishes per dataset; checked by tests/integration.
_FREQS: dict[Kind, tuple[Freq, ...]] = {
    Kind.KLINES: ("monthly", "daily"),
    Kind.PREMIUM: ("monthly", "daily"),
    Kind.FUNDING: ("monthly",),
    Kind.METRICS: ("daily",),
}


@dataclass(frozen=True)
class ArchiveFile:
    freq: Freq
    period: str  # YYYY-MM or YYYY-MM-DD
    path: str


@dataclass
class IngestReport:
    dataset: str
    symbol: str
    rows_written: int = 0
    duplicates_dropped: int = 0
    files_ok: list[ArchiveFile] = field(default_factory=list)
    missing: list[ArchiveFile] = field(default_factory=list)
    unverified: list[ArchiveFile] = field(default_factory=list)


def archive_path(dataset: Dataset, symbol: str, freq: Freq, period: str) -> str:
    root = "spot" if dataset.market is Market.SPOT else "futures/um"
    if dataset.is_bars:
        iv = dataset.interval
        return f"{root}/{freq}/{dataset.kind}/{symbol}/{iv}/{symbol}-{iv}-{period}.zip"
    return f"{root}/{freq}/{dataset.kind}/{symbol}/{symbol}-{dataset.kind}-{period}.zip"


def _days(a: dt.date, b: dt.date) -> list[dt.date]:
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def plan_files(dataset: Dataset, symbol: str, start: dt.date, end: dt.date) -> list[ArchiveFile]:
    """Monthly archives for whole months up to `end`, daily archives for the remainder (where published)."""
    freqs = _FREQS[dataset.kind]
    out: list[ArchiveFile] = []

    def add(freq: Freq, period: str) -> None:
        out.append(ArchiveFile(freq, period, archive_path(dataset, symbol, freq, period)))

    y, m = start.year, start.month
    while dt.date(y, m, 1) <= end:
        first = dt.date(y, m, 1)
        last = dt.date(y, m, calendar.monthrange(y, m)[1])
        if "monthly" in freqs and last <= end:
            add("monthly", f"{y:04d}-{m:02d}")
        elif "daily" in freqs:
            for d in _days(max(first, start), min(last, end)):
                add("daily", d.isoformat())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def verify_checksum(zip_bytes: bytes, checksum: bytes, filename: str) -> None:
    parts = checksum.decode().split()
    if len(parts) != 2:
        raise ChecksumMismatch(f"malformed checksum file for {filename}")
    digest, name = parts
    if name != filename:
        raise ChecksumMismatch(f"checksum filename {name!r} does not match {filename!r}")
    actual = hashlib.sha256(zip_bytes).hexdigest()
    if actual != digest.lower():
        raise ChecksumMismatch(f"{filename}: sha256 {actual} != published {digest}")


def extract_csv(zip_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csvs = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(csvs) != 1:
            raise DataIntegrityError(f"expected exactly one CSV in archive, found {zf.namelist()}")
        return zf.read(csvs[0])


def http_fetch(url: str, *, timeout: float = 30.0, retries: int = 4) -> bytes:
    """GET with exponential backoff on transient errors. 404 raises NotFound immediately."""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data: bytes = resp.read()
                return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            if attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            # Dropped connections (RemoteDisconnected, resets) are transient; retry them like timeouts.
            if attempt == retries:
                raise
        time.sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable")


def _cached_fetch(fetch: Fetch, url: str, raw_dir: Path | None, rel: str) -> bytes:
    if raw_dir is None:
        return fetch(url)
    path = raw_dir / rel
    if path.exists():
        return path.read_bytes()
    data = fetch(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data


def ingest(
    store: PITStore,
    dataset: Dataset,
    symbol: str,
    start: dt.date,
    end: dt.date,
    *,
    fetch: Fetch = http_fetch,
    raw_dir: Path | None = None,
) -> IngestReport:
    report = IngestReport(dataset.name, symbol)
    for f in plan_files(dataset, symbol, start, end):
        url = f"{BASE_URL}/{f.path}"
        try:
            zip_bytes = _cached_fetch(fetch, url, raw_dir, f.path)
        except NotFound:
            report.missing.append(f)
            continue
        try:
            checksum = _cached_fetch(fetch, url + ".CHECKSUM", raw_dir, f.path + ".CHECKSUM")
        except NotFound:
            report.unverified.append(f)
            continue
        verify_checksum(zip_bytes, checksum, f.path.rsplit("/", 1)[1])
        frame = parse_csv(dataset, extract_csv(zip_bytes))
        report.duplicates_dropped += frame.attrs.get("exact_duplicates_dropped", 0)
        report.rows_written += store.write(dataset, symbol, frame)
        report.files_ok.append(f)
    return report
