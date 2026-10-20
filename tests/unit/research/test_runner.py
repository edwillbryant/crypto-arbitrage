from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from agent.data.datasets import FUNDING, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.research.runner import (
    HOLDOUT_START,
    SEGMENTS,
    QualityGateError,
    first_lock_time,
    required_datasets,
    run_variant,
    select,
)
from agent.strategies.registry import REGISTRY
from tests.fixtures.factories import funding_csv, kline_csv


def test_segments_never_reach_the_holdout() -> None:
    assert HOLDOUT_START == pd.Timestamp("2025-01-01", tz="UTC")
    assert all(end <= HOLDOUT_START for _, end in SEGMENTS.values())
    assert set(SEGMENTS) == {"train", "validation"}


def test_first_lock_time() -> None:
    t = pd.date_range("2024-01-01", periods=5, freq="1D", tz="UTC")
    assert (
        first_lock_time(pd.Series([100.0, 110.0, 105.0, 101.0, 120.0], index=t), 0.08) == t[3]
    )  # 101/110 = -8.2%
    assert first_lock_time(pd.Series([100.0, 95.0, 99.0, 93.0, 94.0], index=t), 0.08) is None


def test_required_datasets_cover_execution_and_funding() -> None:
    names = {
        f"{d.name}:{s}" for d, s in required_datasets(REGISTRY["c2_trend"].build({"n": 20, "k_atr": 3.0}))
    }
    assert {"perp/klines/1d:BTCUSDT", "perp/klines/1h:BTCUSDT", "perp/fundingRate:BTCUSDT"} <= names


def _store(tmp: Path) -> PITStore:
    s = PITStore(tmp / "store")
    for sym in ("BTCUSDT", "ETHUSDT"):
        for ds, n, iv in ((klines("perp", "1h"), 24 * 60, "1h"), (klines("perp", "1d"), 60, "1d")):
            s.write(ds, sym, parse_csv(ds, kline_csv("2020-01-01", n, iv, seed=3)))
        s.write(FUNDING, sym, parse_csv(FUNDING, funding_csv("2020-01-01", 60 * 3)))
    return s


def _status(tmp: Path, store: PITStore, fail: str | None = None) -> Path:
    p = tmp / "quality_status.json"
    rows = {
        f"{d}:{s}": "PASS"
        for d in ("perp/klines/1h", "perp/klines/1d", "perp/fundingRate")
        for s in ("BTCUSDT", "ETHUSDT")
    }
    if fail:
        rows[fail] = "FAIL"
    p.write_text(json.dumps({"generated_at": "2026-10-20T00:00:00Z", "datasets": rows}))
    return p


def test_run_variant_on_a_short_window(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = run_variant(
        "c2_trend",
        {"n": 20, "k_atr": 3.0},
        store=store,
        quality_status=_status(tmp_path, store),
        start=pd.Timestamp("2020-02-01", tz="UTC"),
        end=pd.Timestamp("2020-02-28", tz="UTC"),
        segment="train",
        cost_multiplier=2.0,
    )
    assert rec["strategy"] == "c2_trend" and rec["cost_multiplier"] == 2.0 and rec["segment"] == "train"
    assert {"sharpe", "max_drawdown", "trades", "net_pnl", "sr_per_period", "n_obs"} <= set(rec["metrics"])
    assert "would_lock_at" in rec


def test_holdout_dates_are_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(PermissionError, match="hold-out"):
        run_variant(
            "c2_trend",
            {"n": 20, "k_atr": 3.0},
            store=store,
            quality_status=_status(tmp_path, store),
            start=pd.Timestamp("2024-06-01", tz="UTC"),
            end=pd.Timestamp("2025-02-01", tz="UTC"),
            segment="validation",
            cost_multiplier=1.0,
        )


def test_failed_or_missing_quality_blocks_research(tmp_path: Path) -> None:
    store = _store(tmp_path)
    kw = {
        "store": store,
        "start": pd.Timestamp("2020-02-01", tz="UTC"),
        "end": pd.Timestamp("2020-02-10", tz="UTC"),
        "segment": "train",
        "cost_multiplier": 1.0,
    }
    with pytest.raises(QualityGateError, match="FAIL"):
        run_variant(
            "c2_trend",
            {"n": 20, "k_atr": 3.0},
            quality_status=_status(tmp_path, store, "perp/fundingRate:ETHUSDT"),
            **kw,
        )  # type: ignore[arg-type]
    with pytest.raises(QualityGateError, match="no quality report"):
        run_variant("c2_trend", {"n": 20, "k_atr": 3.0}, quality_status=tmp_path / "nope.json", **kw)  # type: ignore[arg-type]


def _results(cells: dict[tuple[int, int], tuple[float, float, float]]) -> pd.DataFrame:
    """cells: (a, b) -> (train sharpe 1x, train net pnl 2x, validation sharpe 1x)."""
    rows = []
    for (a, b), (sr, pnl2, vsr) in cells.items():
        rows.append(
            {"a": a, "b": b, "segment": "train", "cost_multiplier": 1.0, "sharpe": sr, "net_pnl": 100.0}
        )
        rows.append(
            {"a": a, "b": b, "segment": "train", "cost_multiplier": 2.0, "sharpe": sr - 0.1, "net_pnl": pnl2}
        )
        rows.append(
            {"a": a, "b": b, "segment": "validation", "cost_multiplier": 1.0, "sharpe": vsr, "net_pnl": 1.0}
        )
    return pd.DataFrame(rows)


def test_selection_picks_the_plateau_centre_not_the_peak() -> None:
    # (1,1) is a lone spike; the plateau around (3,2) is broad and positive everywhere.
    cells = {(a, b): (0.4, 10.0, 0.2) for a in (2, 3, 4) for b in (1, 2, 3)}
    cells |= {(1, b): (-0.5, -10.0, -0.1) for b in (1, 2, 3)}
    cells[(1, 1)] = (3.0, 50.0, 0.5)
    cells[(3, 2)] = (0.6, 10.0, 0.3)
    res = select(_results(cells), ["a", "b"])
    assert res.params == {"a": 3, "b": 2}
    assert res.verdict == "PROPOSE"


def test_selection_rejects_when_nothing_survives() -> None:
    cells = {(a, b): (-0.2, -1.0, 0.1) for a in (1, 2, 3) for b in (1, 2, 3)}
    cells[(2, 2)] = (1.0, 5.0, 0.4)  # isolated
    res = select(_results(cells), ["a", "b"])
    assert res.params is None and res.verdict == "REJECT"


def test_validation_veto() -> None:
    cells = {(a, b): (0.5, 10.0, -0.3) for a in (1, 2, 3) for b in (1, 2, 3)}
    res = select(_results(cells), ["a", "b"])
    assert res.params is not None and res.verdict == "FAILS_VALIDATION"


def test_costs_must_survive_doubling() -> None:
    cells = {(a, b): (0.5, -5.0, 0.3) for a in (1, 2, 3) for b in (1, 2, 3)}  # profitable only at 1x
    assert select(_results(cells), ["a", "b"]).verdict == "REJECT"


def test_diagnostic_runs_are_allowed_before_the_holdout_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    kw = {"store": store, "quality_status": _status(tmp_path, store), "cost_multiplier": 1.0}
    rec = run_variant(
        "c2_trend",
        {"n": 20, "k_atr": 3.0},
        start=pd.Timestamp("2020-02-01", tz="UTC"),
        end=pd.Timestamp("2020-02-20", tz="UTC"),
        segment="diagnostic",
        **kw,
    )  # type: ignore[arg-type]
    assert rec["segment"] == "diagnostic"
    with pytest.raises(PermissionError):
        run_variant(
            "c2_trend",
            {"n": 20, "k_atr": 3.0},
            start=pd.Timestamp("2024-12-01", tz="UTC"),
            end=pd.Timestamp("2025-03-01", tz="UTC"),
            segment="diagnostic",
            **kw,
        )  # type: ignore[arg-type]


def test_pnl_breakdown_reconciles_with_net_pnl(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = run_variant(
        "c2_trend",
        {"n": 20, "k_atr": 3.0},
        store=store,
        quality_status=_status(tmp_path, store),
        start=pd.Timestamp("2020-02-01", tz="UTC"),
        end=pd.Timestamp("2020-02-28", tz="UTC"),
        segment="diagnostic",
        cost_multiplier=1.0,
    )
    b = rec["pnl_breakdown"]
    assert b["price"] + b["funding"] - b["fees"] == pytest.approx(rec["metrics"]["net_pnl"], abs=1e-6)
