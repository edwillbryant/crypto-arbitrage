"""Append-only trial registry (JSON lines with a hash chain).

Every backtest variant is recorded so results can be judged against how many things were tried (the DSR
needs N and the spread of Sharpe ratios). Editing or deleting a line breaks the chain and `verify()`
fails. Truncating the tail is caught by git, which tracks the file.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from agent.core.errors import DataIntegrityError

GENESIS = "0" * 64
SEGMENTS = {"train", "validation", "diagnostic"}  # hold-out runs only through the lock workflow (M4)


def _git_state() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


def _digest(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _lines(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        segment = record.get("segment")
        if segment == "holdout":
            raise PermissionError(
                "hold-out results are recorded only via `agent research holdout` with a lock file"
            )
        if segment not in SEGMENTS or "strategy" not in record:
            raise ValueError(f"record needs 'strategy' and a segment in {sorted(SEGMENTS)}")
        return self._write(record)

    def append_holdout(self, record: dict[str, Any], *, lock: dict[str, Any]) -> dict[str, Any]:
        """Only the hold-out runner calls this, with the committed lock it checked."""
        if record.get("segment") != "holdout":
            raise ValueError("append_holdout is for hold-out records only")
        if record.get("strategy") != lock.get("strategy") or record.get("params") != lock.get("params"):
            raise PermissionError("hold-out record does not match its lock")
        return self._write(record)

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        lines = self._lines()
        full = {
            **record,
            "id": uuid.uuid4().hex,
            "recorded_at": dt.datetime.now(dt.UTC).isoformat(),
            "git": _git_state(),
            "prev_hash": lines[-1]["hash"] if lines else GENESIS,
        }
        full["hash"] = _digest(full)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(full, sort_keys=True, default=str) + "\n")
        return full

    def trials(self, strategy: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self._lines() if strategy is None or r["strategy"] == strategy]

    def verify(self) -> None:
        prev = GENESIS
        for n, rec in enumerate(self._lines(), start=1):
            if rec.get("prev_hash") != prev or _digest(rec) != rec.get("hash"):
                raise DataIntegrityError(
                    f"{self.path}: record {n} was edited, reordered or a record was removed"
                )
            prev = rec["hash"]
