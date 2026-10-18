"""Point-in-time store over parquet, one file per dataset/symbol/month.

The only read path for strategies and backtests is `read(..., as_of=...)`, which never returns a row whose
`available_at` is after `as_of`. History is append-only: rewriting a stored row with different values
raises DataConflict instead of silently revising the past.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from agent.core.clock import utc
from agent.core.errors import DataConflict, DataIntegrityError
from agent.data.datasets import Dataset
from agent.data.parse import TS_DTYPE

_SYMBOL = re.compile(r"^[A-Z0-9]{2,20}$")


def _check_symbol(symbol: str) -> str:
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError(f"invalid symbol {symbol!r}")
    return symbol


def validate_frame(dataset: Dataset, df: pd.DataFrame) -> None:
    missing = [c for c in ("key_time", "available_at", *dataset.columns) if c not in df.columns]
    if missing:
        raise DataIntegrityError(f"{dataset.name}: missing columns {missing}")
    for col in ("key_time", "available_at"):
        if str(df[col].dtype) != TS_DTYPE:
            raise DataIntegrityError(f"{dataset.name}: {col} must be {TS_DTYPE}, got {df[col].dtype}")
    if df["key_time"].duplicated().any():
        raise DataIntegrityError(f"{dataset.name}: duplicate key_time values in write")
    if (df["available_at"] < dataset.availability(df["key_time"])).any():
        raise DataIntegrityError(f"{dataset.name}: available_at earlier than the data could have been known")


class PITStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, dataset: Dataset, symbol: str) -> Path:
        return self.root / dataset.name / _check_symbol(symbol)

    def partitions(self, dataset: Dataset, symbol: str) -> list[Path]:
        d = self._dir(dataset, symbol)
        return sorted(d.glob("*.parquet")) if d.exists() else []

    def write(self, dataset: Dataset, symbol: str, df: pd.DataFrame) -> int:
        """Merge rows into the store. Returns the number of new rows. Identical re-writes are no-ops."""
        validate_frame(dataset, df)
        cols = ["key_time", "available_at", *dataset.columns]
        df = df[cols]
        d = self._dir(dataset, symbol)
        d.mkdir(parents=True, exist_ok=True)
        added = 0
        for month, part in df.groupby(df["key_time"].dt.strftime("%Y-%m"), sort=True):
            path = d / f"{month}.parquet"
            if path.exists():
                old = pd.read_parquet(path)
                both = old.merge(part, on="key_time", how="inner", suffixes=("_old", ""))
                for c in cols[1:]:
                    a, b = both[f"{c}_old"], both[c]
                    if not (a.eq(b) | (a.isna() & b.isna())).all():
                        raise DataConflict(f"{dataset.name}/{symbol} {month}: stored values differ in {c!r}")
                new = part[~part["key_time"].isin(old["key_time"])]
                merged = pd.concat([old, new], ignore_index=True)
            else:
                new = part
                merged = part
            if len(new):
                merged = merged.sort_values("key_time").reset_index(drop=True)
                tmp = path.with_suffix(".tmp")
                merged.to_parquet(tmp, index=False)
                tmp.replace(path)  # atomic: a crash never leaves a half-written partition
            added += len(new)
        return added

    def _load(self, dataset: Dataset, symbol: str, upto_month: str | None = None) -> Iterator[pd.DataFrame]:
        for path in self.partitions(dataset, symbol):
            if upto_month is None or path.stem <= upto_month:
                yield pd.read_parquet(path)

    def read(
        self,
        dataset: Dataset,
        symbol: str,
        as_of: pd.Timestamp,
        lookback: pd.Timedelta | None = None,
    ) -> pd.DataFrame:
        """Rows knowable at `as_of` (available_at <= as_of), optionally only key_time >= as_of - lookback."""
        as_of = utc(as_of)
        frames = list(self._load(dataset, symbol, upto_month=as_of.strftime("%Y-%m")))
        if not frames:
            return self._empty(dataset)
        df = pd.concat(frames, ignore_index=True)
        mask = df["available_at"] <= as_of
        if lookback is not None:
            mask &= df["key_time"] >= as_of - lookback
        return df.loc[mask].reset_index(drop=True)

    def scan_full_history(self, dataset: Dataset, symbol: str) -> pd.DataFrame:
        """Everything stored, ignoring time. For data-quality reports ONLY, never for signals."""
        frames = list(self._load(dataset, symbol))
        return pd.concat(frames, ignore_index=True) if frames else self._empty(dataset)

    @staticmethod
    def _empty(dataset: Dataset) -> pd.DataFrame:
        df = pd.DataFrame({c: pd.Series(dtype="float64") for c in dataset.columns})
        df.insert(0, "available_at", pd.Series(dtype=TS_DTYPE))
        df.insert(0, "key_time", pd.Series(dtype=TS_DTYPE))
        return df
