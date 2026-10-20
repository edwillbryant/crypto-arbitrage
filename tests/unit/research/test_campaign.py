from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from agent.data.datasets import FUNDING, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.research.campaign import results_frame, run_campaign
from agent.research.registry import Registry
from tests.fixtures.factories import funding_csv, kline_csv

SEG = {"train": (pd.Timestamp("2020-01-03", tz="UTC"), pd.Timestamp("2020-01-08", tz="UTC"))}


def _setup(tmp: Path) -> tuple[Path, Path]:
    store = PITStore(tmp / "store")
    ds = klines("perp", "1h")
    store.write(ds, "BTCUSDT", parse_csv(ds, kline_csv("2020-01-01", 24 * 10, "1h")))
    store.write(FUNDING, "BTCUSDT", parse_csv(FUNDING, funding_csv("2020-01-01", 30)))
    status = tmp / "quality_status.json"
    status.write_text(
        json.dumps({"datasets": {"perp/klines/1h:BTCUSDT": "PASS", "perp/fundingRate:BTCUSDT": "PASS"}})
    )
    return tmp / "store", status


def test_campaign_records_every_run_and_resumes(tmp_path: Path) -> None:
    store, status = _setup(tmp_path)
    reg = tmp_path / "trials.jsonl"
    kw = {
        "store_path": store,
        "status_path": status,
        "registry_path": reg,
        "segments": SEG,
        "costs": [1.0, 2.0],
        "workers": 1,
        "limit": 3,
    }
    n = run_campaign(["c5_seasonality"], **kw)  # type: ignore[arg-type]
    assert n == 6  # 3 variants x 1 segment x 2 cost levels
    Registry(reg).verify()
    assert run_campaign(["c5_seasonality"], **kw) == 0  # resume: nothing left to do  # type: ignore[arg-type]
    df = results_frame(Registry(reg), "c5_seasonality")
    assert len(df) == 6 and {"start_hour", "length", "days", "sharpe", "segment", "cost_multiplier"} <= set(
        df.columns
    )
