from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from agent.data.datasets import FUNDING, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.research.holdout import make_lock, run_holdout, verdict
from agent.research.registry import Registry
from tests.fixtures.factories import funding_csv, kline_csv

PERIOD = (pd.Timestamp("2020-03-01", tz="UTC"), pd.Timestamp("2020-03-20", tz="UTC"))
PARAMS = {"n": 20, "k_atr": 3.0}


@pytest.fixture
def env(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = PITStore(tmp_path / "store")
    for i, sym in enumerate(("BTCUSDT", "ETHUSDT")):
        for ds, n, iv in ((klines("perp", "1h"), 24 * 80, "1h"), (klines("perp", "1d"), 80, "1d")):
            store.write(ds, sym, parse_csv(ds, kline_csv("2020-01-01", n, iv, seed=i)))
        store.write(FUNDING, sym, parse_csv(FUNDING, funding_csv("2020-01-01", 80 * 3)))
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"datasets": {f"{d}:{s}": "PASS" for d in ("perp/klines/1h", "perp/klines/1d",
                      "perp/fundingRate") for s in ("BTCUSDT", "ETHUSDT")}}))  # fmt: skip
    protocol = tmp_path / "protocol.md"
    protocol.write_text("pre-registered rules\n")
    return {"store": store, "status": status, "locks": tmp_path / "locks", "reg": Registry(tmp_path / "t.jsonl"),
            "protocol": protocol}  # fmt: skip


def _run(env, committed: bool = True):  # type: ignore[no-untyped-def]
    return run_holdout("c2_trend", store=env["store"], quality_status=env["status"], locks_dir=env["locks"],
                       registry=env["reg"], protocol_path=env["protocol"], period=PERIOD,
                       is_committed=lambda p: committed)  # fmt: skip


def test_lock_records_params_commit_and_protocol_hash(env) -> None:  # type: ignore[no-untyped-def]
    path = make_lock(
        "c2_trend", PARAMS, locks_dir=env["locks"], protocol_path=env["protocol"], registry=env["reg"]
    )
    lock = json.loads(path.read_text())
    assert lock["strategy"] == "c2_trend" and lock["params"] == PARAMS and len(lock["protocol_sha256"]) == 64
    with pytest.raises(FileExistsError):
        make_lock(
            "c2_trend", PARAMS, locks_dir=env["locks"], protocol_path=env["protocol"], registry=env["reg"]
        )


def test_only_pre_registered_variants_can_be_locked(env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="pre-registered"):
        make_lock("c2_trend", {"n": 37, "k_atr": 3.0}, locks_dir=env["locks"], protocol_path=env["protocol"],
                  registry=env["reg"])  # fmt: skip


def test_holdout_needs_a_committed_lock_and_an_unchanged_protocol(env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PermissionError, match="no lock"):
        _run(env)
    make_lock("c2_trend", PARAMS, locks_dir=env["locks"], protocol_path=env["protocol"], registry=env["reg"])
    with pytest.raises(PermissionError, match="committed"):
        _run(env, committed=False)
    env["protocol"].write_text("rules quietly edited after locking\n")
    with pytest.raises(PermissionError, match="protocol"):
        _run(env)


def test_holdout_runs_once_with_all_required_runs(env) -> None:  # type: ignore[no-untyped-def]
    make_lock("c2_trend", PARAMS, locks_dir=env["locks"], protocol_path=env["protocol"], registry=env["reg"])
    recs = _run(env)
    assert [(r["universe"], r["cost_multiplier"]) for r in recs] == [
        ("both", 1.0), ("both", 2.0), ("both", 3.0), ("BTCUSDT", 1.0), ("ETHUSDT", 1.0)]  # fmt: skip
    assert all(r["segment"] == "holdout" for r in recs)
    assert {"sharpe_daily", "sr_daily", "n_days", "max_trade_share", "max_month_share", "funding_periods_held"} <= set(
        recs[0]["metrics"]) | set(recs[0])  # fmt: skip
    env["reg"].verify()
    with pytest.raises(PermissionError, match="already"):
        _run(env)


def test_plain_registry_append_still_refuses_holdout(env) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PermissionError):
        env["reg"].append({"strategy": "c2_trend", "params": PARAMS, "segment": "holdout"})


def _hold(universe: str, cost: float, **m: float) -> dict[str, object]:
    base = {"sharpe_daily": 1.2, "sr_daily": 0.063, "n_days": 640, "skew_daily": 0.0, "kurt_daily": 3.0,
            "net_pnl": 500.0, "trades": 40}  # fmt: skip
    return {"universe": universe, "cost_multiplier": cost, "metrics": {**base, **m}, "max_trade_share": 0.1,
            "max_month_share": 0.15, "funding_periods_held": 0}  # fmt: skip


def _records(**over: object) -> list[dict[str, object]]:
    recs = [
        _hold("both", 1.0),
        _hold("both", 2.0),
        _hold("both", 3.0),
        _hold("BTCUSDT", 1.0),
        _hold("ETHUSDT", 1.0),
    ]
    for k, v in over.items():
        recs[0][k] = v  # type: ignore[index]
    return recs


TRAIN = [0.02, 0.03, 0.01, 0.04, 0.025]


def test_verdict_pass() -> None:
    assert verdict("c2_trend", _records(), TRAIN)["verdict"] == "PASS"


def test_verdict_reject_on_negative_sharpe_or_cost_fragility() -> None:
    r = _records()
    r[0]["metrics"]["sharpe_daily"] = -0.1  # type: ignore[index]
    assert verdict("c2_trend", r, TRAIN)["verdict"] == "REJECT"
    r = _records()
    r[1]["metrics"]["net_pnl"] = -5.0  # type: ignore[index]
    assert verdict("c2_trend", r, TRAIN)["verdict"] == "REJECT"


def test_verdict_insufficient_evidence_on_few_trades_unless_carry_held_funding() -> None:
    r = _records()
    r[0]["metrics"]["trades"] = 7  # type: ignore[index]
    assert verdict("c2_trend", r, TRAIN)["verdict"] == "INSUFFICIENT EVIDENCE"
    r[0]["funding_periods_held"] = 300
    assert verdict("c1_carry", r, TRAIN)["verdict"] == "PASS"


def test_verdict_reject_on_dsr_single_asset_or_concentration() -> None:
    r = _records()
    r[0]["metrics"]["sr_daily"] = 0.001  # type: ignore[index]
    assert verdict("c2_trend", r, TRAIN)["verdict"] == "REJECT"
    r = _records()
    r[4]["metrics"]["sharpe_daily"] = -0.2  # type: ignore[index]
    assert verdict("c2_trend", r, TRAIN)["verdict"] == "REJECT"
    assert verdict("c2_trend", _records(max_month_share=0.4), TRAIN)["verdict"] == "REJECT"
