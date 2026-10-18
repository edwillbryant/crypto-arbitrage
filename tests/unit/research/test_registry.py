from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.errors import DataIntegrityError
from agent.research.registry import Registry


def _rec(i: int) -> dict[str, object]:
    return {"strategy": "c2_trend", "params": {"n": i}, "segment": "train", "metrics": {"sharpe": 0.1 * i}}


def test_append_and_read(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "trials.jsonl")
    for i in range(3):
        reg.append(_rec(i))
    rows = reg.trials("c2_trend")
    assert [r["params"] for r in rows] == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert all("git" in r and "recorded_at" in r and "id" in r for r in rows)
    assert reg.trials("other") == []
    reg.verify()


def test_edits_and_deletions_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "trials.jsonl"
    reg = Registry(path)
    for i in range(3):
        reg.append(_rec(i))
    lines = path.read_text().splitlines()

    edited = json.loads(lines[1])
    edited["metrics"]["sharpe"] = 9.9
    path.write_text("\n".join([lines[0], json.dumps(edited), lines[2]]) + "\n")
    with pytest.raises(DataIntegrityError):
        reg.verify()

    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    with pytest.raises(DataIntegrityError):
        reg.verify()


def test_holdout_records_are_refused_here(tmp_path: Path) -> None:
    """Hold-out runs go through the lock workflow (M4), never a plain append."""
    with pytest.raises(PermissionError):
        Registry(tmp_path / "t.jsonl").append({**_rec(0), "segment": "holdout"})
