from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent.cli import main
from agent.data.datasets import FUNDING, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.live.config import load_paper_config
from tests.fixtures.factories import funding_csv, kline_csv_from_prices

REPO = Path(__file__).resolve().parents[3]


def _cfg(tmp_path: Path, strategies: list[dict[str, object]]) -> Path:
    p = tmp_path / "paper.yaml"
    p.write_text(yaml.safe_dump({"initial_cash": 10_000.0, "strategies": strategies}))
    return p


def _lock(tmp_path: Path, name: str, params: dict[str, object]) -> str:
    p = tmp_path / f"{name}.lock.json"
    p.write_text(
        json.dumps({"strategy": name, "params": params, "git_commit": "abc", "locked_at": "2026-11-01"})
    )
    return str(p)


ENTRY = {
    "name": "c2_trend",
    "params": {"n": 50, "k_atr": 3.0},
    "approved_by": "owner",
    "approved_on": "2026-11-02",
}


def test_repo_config_enables_nothing_yet() -> None:
    cfg = load_paper_config(REPO / "config" / "paper.yaml")
    assert cfg.strategies == []  # nothing passes until M4 hold-out + M7 approval


def test_valid_entry_with_matching_lock(tmp_path: Path) -> None:
    entry = {**ENTRY, "lock_file": _lock(tmp_path, "c2_trend", ENTRY["params"])}  # type: ignore[arg-type]
    assert load_paper_config(_cfg(tmp_path, [entry])).strategies[0].name == "c2_trend"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": "c5_seasonality", "params": {"start_hour": 0, "length": 8, "days": "all"}}, "not eligible"),
        ({"lock_file": "/nonexistent/lock.json"}, "lock file"),
        ({"params": {"n": 20, "k_atr": 3.0}}, "do not match"),
        ({"approved_by": ""}, "approved_by"),
        ({"name": "c4_xarb"}, "unknown"),
    ],
)
def test_entries_that_must_be_refused(tmp_path: Path, change: dict[str, object], message: str) -> None:
    entry = {**ENTRY, "lock_file": _lock(tmp_path, "c2_trend", ENTRY["params"]), **change}  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=message):
        load_paper_config(_cfg(tmp_path, [entry]))


def test_there_is_no_live_mode() -> None:
    with pytest.raises(SystemExit):
        main(["run", "--mode", "live"])


def test_paper_mode_without_strategies_refuses(tmp_path: Path) -> None:
    assert main(["run", "--mode", "paper", "--config", str(_cfg(tmp_path, []))]) == 2


def test_replay_runs_the_live_stack_over_stored_history(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = PITStore(tmp_path / "store")
    n = 24 * 12
    for m in ("spot", "perp"):
        for sym in ("BTCUSDT", "ETHUSDT"):
            ds = klines(m, "1h")
            store.write(
                ds, sym, parse_csv(ds, kline_csv_from_prices("2024-01-01", "1h", [100.0] * n, [100.0] * n))
            )
    for sym in ("BTCUSDT", "ETHUSDT"):
        store.write(FUNDING, sym, parse_csv(FUNDING, funding_csv("2024-01-01", 36, rate=0.0004)))
    args = [
        "run",
        "--mode",
        "paper",
        "--replay",
        "--strategy",
        "c1_carry",
        "--store",
        str(tmp_path / "store"),
    ]
    code = main([*args, "--from", "2024-01-05", "--to", "2024-01-06", "--state-dir", str(tmp_path / "state")])
    out = capsys.readouterr().out
    assert code == 0
    assert "REPLAY (not validated, not paper trading)" in out
    assert (tmp_path / "state" / "decisions.jsonl").exists()
    assert len((tmp_path / "state" / "decisions.jsonl").read_text().splitlines()) == 24
