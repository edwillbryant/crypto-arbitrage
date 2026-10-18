"""Parsers for data.binance.vision CSV archives into the store schema.

Every frame leaving here has `key_time` (unique, UTC, sorted), `available_at`, and the dataset's columns.
Format details marked [UNVERIFIED] are checked against the real archive in tests/integration.
"""

from __future__ import annotations

import io
from typing import Final

import numpy as np
import pandas as pd

from agent.core.errors import DataIntegrityError
from agent.data.datasets import Dataset, Kind

TS_DTYPE: Final = "datetime64[ns, UTC]"

_KLINE_NAMES = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
                "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]  # fmt: skip

# Epoch values at or above this are microseconds. [UNVERIFIED] Spot archives switched from ms to µs
# on 2025-01-01; futures archives are ms. Per-value detection handles both and mixed files.
_MICROS_THRESHOLD = 10**14


def epoch_to_utc(values: pd.Series) -> pd.Series:
    v = pd.to_numeric(values, errors="raise").astype("int64")
    ns = np.where(v >= _MICROS_THRESHOLD, v * 1_000, v * 1_000_000)
    return pd.Series(pd.to_datetime(ns, utc=True), index=values.index).astype(TS_DTYPE)


def _has_header(raw: bytes) -> bool:
    first = raw.lstrip()[:64].split(b",", 1)[0].strip()
    return not first.lstrip(b"-").isdigit()


def _read(raw: bytes, names: list[str] | None) -> pd.DataFrame:
    try:
        if names is None:
            return pd.read_csv(io.BytesIO(raw))
        return pd.read_csv(io.BytesIO(raw), header=0 if _has_header(raw) else None, names=names)
    except (ValueError, pd.errors.ParserError) as e:
        raise DataIntegrityError(f"unparseable CSV: {e}") from e


# Columns that must never be empty. Metrics ratios are allowed gaps; open interest is not.
_REQUIRED = {Kind.METRICS: ("sum_open_interest", "sum_open_interest_value")}


def _finish(dataset: Dataset, key_time: pd.Series, body: pd.DataFrame) -> pd.DataFrame:
    required = _REQUIRED.get(dataset.kind, dataset.columns)
    if key_time.isna().any() or body[list(required)].isna().any().any():
        raise DataIntegrityError(f"{dataset.name}: empty values in required columns")
    out = pd.DataFrame({"key_time": key_time.astype(TS_DTYPE)})
    out["available_at"] = dataset.availability(out["key_time"]).astype(TS_DTYPE)
    for col in dataset.columns:
        out[col] = body[col].to_numpy()
    # Some archive files repeat rows verbatim (e.g. BTCUSDT metrics 2020-09-01). Identical repeats carry no
    # information and are dropped; the same timestamp with DIFFERENT values is ambiguous and is refused.
    before = len(out)
    out = out.drop_duplicates()
    if out["key_time"].duplicated().any():
        raise DataIntegrityError(f"{dataset.name}: conflicting duplicate rows for the same key_time")
    out = out.sort_values("key_time", kind="stable").reset_index(drop=True)
    out.attrs["exact_duplicates_dropped"] = before - len(out)
    return out


def parse_csv(dataset: Dataset, raw: bytes) -> pd.DataFrame:
    try:
        if dataset.is_bars:
            df = _read(raw, _KLINE_NAMES)
            key = epoch_to_utc(df["open_time"])
            floats = ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume",
                      "taker_buy_quote_volume"]  # fmt: skip
            body = df.astype({c: "float64" for c in floats})
            body["trades"] = df["trades"].astype("int64")
            body["close_time"] = epoch_to_utc(df["close_time"])
            return _finish(dataset, key, body)
        df = _read(raw, None)
        if dataset.kind is Kind.FUNDING:
            body = pd.DataFrame(
                {
                    "interval_hours": df["funding_interval_hours"].astype("int64"),
                    "rate": df["last_funding_rate"].astype("float64"),
                }
            )
            # Settlement happens on the hour, but ~45% of archive prints are stamped 1-47 ms late. Left as is,
            # a 16:00:00.002 print would be charged to the position opened BY the 16:00 decision.
            settled = epoch_to_utc(df["calc_time"]).dt.round("min")
            return _finish(dataset, settled, body)
        if dataset.kind is Kind.METRICS:
            key = pd.to_datetime(df["create_time"], utc=True, format="%Y-%m-%d %H:%M:%S")
            return _finish(dataset, key, df.astype({c: "float64" for c in dataset.columns}))
    except (KeyError, ValueError, TypeError) as e:
        raise DataIntegrityError(f"{dataset.name}: CSV does not match expected layout: {e}") from e
    raise DataIntegrityError(f"no parser for {dataset.name}")
