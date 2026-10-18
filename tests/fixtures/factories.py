"""Synthetic fixtures in Binance archive formats.

These exist only to exercise code paths. No research result is ever computed on them.
"""

from __future__ import annotations

import hashlib
import io
import zipfile

import numpy as np
import pandas as pd

KLINE_HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore"
)


def kline_csv(
    start: str,
    n: int,
    interval: str = "1h",
    *,
    seed: int = 0,
    header: bool = False,
    micros: bool = False,
    price0: float = 100.0,
) -> bytes:
    """Random-walk klines in the data.binance.vision CSV layout."""
    rng = np.random.default_rng(seed)
    step = pd.Timedelta(interval)
    t0 = pd.Timestamp(start, tz="UTC")
    scale = 1000 if micros else 1
    lines = [KLINE_HEADER] if header else []
    price = price0
    for i in range(n):
        o = price
        c = o * float(np.exp(rng.normal(0, 0.01)))
        h = max(o, c) * (1 + abs(float(rng.normal(0, 0.002))))
        lo = min(o, c) * (1 - abs(float(rng.normal(0, 0.002))))
        vol = float(rng.uniform(1, 10))
        ot = t0 + i * step
        ot_ms = int(ot.value // 1_000_000)
        ct_ms = ot_ms + int(step.value // 1_000_000) - 1
        lines.append(
            f"{ot_ms * scale},{o:.2f},{h:.2f},{lo:.2f},{c:.2f},{vol:.4f},{ct_ms * scale + (scale - 1)},"
            f"{vol * c:.2f},{int(rng.integers(10, 100))},{vol / 2:.4f},{vol * c / 2:.2f},0"
        )
        price = c
    return ("\n".join(lines) + "\n").encode()


def funding_csv(start: str, n: int, *, interval_hours: int = 8, rate: float = 0.0001) -> bytes:
    t0 = pd.Timestamp(start, tz="UTC")
    lines = ["calc_time,funding_interval_hours,last_funding_rate"]
    for i in range(n):
        t = t0 + pd.Timedelta(hours=interval_hours * i)
        lines.append(f"{t.value // 1_000_000},{interval_hours},{rate:.8f}")
    return ("\n".join(lines) + "\n").encode()


def metrics_csv(start: str, n: int, symbol: str = "BTCUSDT") -> bytes:
    t0 = pd.Timestamp(start, tz="UTC")
    lines = [
        "create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
        "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio"
    ]
    for i in range(n):
        t = t0 + pd.Timedelta(minutes=5 * i)
        lines.append(f"{t:%Y-%m-%d %H:%M:%S},{symbol},{80000 + i},{6.5e9 + i},1.1,1.2,1.3,0.9")
    return ("\n".join(lines) + "\n").encode()


def make_zip(member: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, payload)
    return buf.getvalue()


def checksum_text(zip_bytes: bytes, filename: str) -> bytes:
    return f"{hashlib.sha256(zip_bytes).hexdigest()}  {filename}\n".encode()
