"""Persistent risk state (SQLite, WAL). Survives restarts so a crash can't reset loss counters or locks."""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from agent.core.clock import utc

ErrorKind = Literal["error", "reject"]

MIN_REASON = 10


@dataclass(frozen=True)
class LedgerState:
    peak_equity: float
    day_start_equity: float
    locked: bool = False
    lock_reason: str = ""
    api_errors: int = 0
    order_rejects: int = 0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_errors (ts INTEGER NOT NULL, kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL
);
"""


def _serialized[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """One SQLite connection is shared by the trading loop and the kill-switch HTTP thread."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0]._lock:  # type: ignore[attr-defined]
            return fn(*args, **kwargs)

    return wrapper


class RiskLedger:
    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)

    @_serialized
    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> RiskLedger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        # Last resort for forgotten handles; normal code closes explicitly or uses `with`.
        db = getattr(self, "_db", None)
        if db is not None:  # pragma: no branch - only absent if __init__ failed before connecting
            db.close()

    @contextmanager
    def _tx(self) -> Iterator[None]:
        """All-or-nothing: a crash mid-update never leaves a lock without its audit record."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    # -- key/value --------------------------------------------------------------------------------------
    def _get(self, key: str, default: str | None = None) -> str | None:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row[0])

    def _set(self, **values: object) -> None:
        for k, v in values.items():
            self._db.execute(
                "INSERT INTO kv(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, str(v)),
            )

    def _audit(self, t: pd.Timestamp, kind: str, **detail: object) -> None:
        self._db.execute(
            "INSERT INTO audit(ts, kind, detail) VALUES (?, ?, ?)", (utc(t).value, kind, json.dumps(detail))
        )

    # -- updates ----------------------------------------------------------------------------------------
    @_serialized
    def on_equity(self, t: pd.Timestamp, equity: float) -> None:
        day = utc(t).strftime("%Y-%m-%d")
        peak = float(self._get("peak_equity", "-inf") or "-inf")
        updates: dict[str, object] = {"last_equity": equity, "peak_equity": max(peak, equity)}
        if self._get("day") != day:
            # Start the day from the last equity seen BEFORE midnight, so losses made while the process
            # was down (or between the last observation and now) still count against today's limit.
            previous = self._get("last_equity")
            updates |= {"day": day, "day_start_equity": float(previous) if previous is not None else equity}
        with self._tx():
            self._set(**updates)

    @_serialized
    def record_api_error(self, t: pd.Timestamp, kind: ErrorKind) -> None:
        if kind not in ("error", "reject"):
            raise ValueError(f"unknown error kind {kind!r}")
        self._db.execute("INSERT INTO api_errors(ts, kind) VALUES (?, ?)", (utc(t).value, kind))

    @_serialized
    def lock(self, t: pd.Timestamp, reason: str, *, source: str) -> None:
        if self._get("locked") == "1":
            return
        with self._tx():
            self._set(locked=1, lock_reason=reason)
            self._audit(t, "lock", reason=reason, source=source)

    @_serialized
    def unlock(self, t: pd.Timestamp, *, reason: str) -> None:
        """Human-only. Resets the drawdown peak to current equity: the loss has been acknowledged."""
        if len(reason.strip()) < MIN_REASON:
            raise ValueError(f"unlock needs a reason of at least {MIN_REASON} characters")
        with self._tx():
            last = self._get("last_equity")
            self._set(locked=0, lock_reason="", **({"peak_equity": last} if last is not None else {}))
            self._audit(t, "unlock", reason=reason)

    @_serialized
    def record(self, t: pd.Timestamp, kind: str, **detail: object) -> None:
        """Append an audit record (mode changes, operator actions)."""
        self._audit(t, kind, **detail)

    # -- reads ------------------------------------------------------------------------------------------
    @_serialized
    def state(self, now: pd.Timestamp, *, window_s: float) -> LedgerState:
        since = utc(now).value - int(window_s * 1e9)
        counts = dict(
            self._db.execute(
                "SELECT kind, COUNT(*) FROM api_errors WHERE ts > ? GROUP BY kind", (since,)
            ).fetchall()
        )
        last = float(self._get("last_equity", "nan") or "nan")
        return LedgerState(
            peak_equity=float(self._get("peak_equity", str(last)) or last),
            day_start_equity=float(self._get("day_start_equity", str(last)) or last),
            locked=self._get("locked") == "1",
            lock_reason=self._get("lock_reason", "") or "",
            api_errors=int(counts.get("error", 0)),
            order_rejects=int(counts.get("reject", 0)),
        )

    @property
    def locked(self) -> bool:
        with self._lock:
            return self._get("locked") == "1"

    @_serialized
    def events(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT ts, kind, detail FROM audit ORDER BY id").fetchall()
        return [
            {"time": pd.Timestamp(ts, tz="UTC"), "kind": kind, "detail": json.loads(d)}
            for ts, kind, d in rows
        ]
