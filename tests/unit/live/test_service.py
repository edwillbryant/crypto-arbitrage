from __future__ import annotations

import json
import threading
from pathlib import Path

import pandas as pd
import pytest
import yaml

from agent.data.datasets import FUNDING, klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from agent.live.config import load_paper_config
from agent.live.feed import StoreFeed
from agent.live.loop import CycleReport
from agent.live.service import Service, build_paper_runtime
from agent.ops.alerts import AlertSink
from agent.risk.gate import Mode
from tests.fixtures.factories import funding_csv, kline_csv_from_prices

T0 = pd.Timestamp("2024-01-10 23:58", tz="UTC")


class ScriptedLoop:
    def __init__(self, modes: list[Mode]) -> None:
        self.modes, self.steps = modes, []  # type: ignore[var-annotated]

    def step(self, now: pd.Timestamp) -> CycleReport:
        self.steps.append(now)
        mode = self.modes[min(len(self.steps) - 1, len(self.modes) - 1)]
        return CycleReport(
            now,
            mode,
            ("stale_book",) if mode is not Mode.NORMAL else (),
            10_000.0,
            ["error: feed: GET /x"] if mode is not Mode.NORMAL else [],
        )


def _service(tmp: Path, loop: ScriptedLoop, beats: list[str]) -> Service:
    clock = {"t": T0}

    def now() -> pd.Timestamp:
        t = clock["t"]
        clock["t"] = t + pd.Timedelta(minutes=1)
        return t

    class Beat:
        def send(self, t: pd.Timestamp, mode: str) -> bool:
            beats.append(mode)
            return True

    return Service(
        loop,
        now=now,
        cycle=pd.Timedelta(seconds=0),
        alerts=AlertSink(tmp / "alerts.jsonl"),
        heartbeat=Beat(),
        summary_dir=tmp / "daily",
    )


def test_service_alerts_on_mode_changes_and_heartbeats_every_cycle(tmp_path: Path) -> None:
    beats: list[str] = []
    loop = ScriptedLoop([Mode.NORMAL, Mode.NORMAL, Mode.REDUCE_ONLY, Mode.REDUCE_ONLY, Mode.NORMAL])
    _service(tmp_path, loop, beats).run(threading.Event(), max_cycles=5)
    alerts = [json.loads(x) for x in (tmp_path / "alerts.jsonl").read_text().splitlines()]
    titles = [a["title"] for a in alerts]
    assert titles.count("mode REDUCE_ONLY") == 1 and titles.count("mode NORMAL") == 1  # transitions only
    assert len(beats) == 5


def test_daily_summary_written_at_utc_rollover(tmp_path: Path) -> None:
    _service(tmp_path, ScriptedLoop([Mode.NORMAL, Mode.HALT, Mode.NORMAL]), []).run(
        threading.Event(), max_cycles=4
    )
    summary = json.loads((tmp_path / "daily" / "2024-01-10.json").read_text())
    assert summary["cycles"] == 2 and summary["modes"] == {"NORMAL": 1, "HALT": 1} and summary["errors"] == 1


def test_stop_event_ends_the_service(tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    loop = ScriptedLoop([Mode.NORMAL])
    _service(tmp_path, loop, []).run(stop)
    assert loop.steps == []


def _cfg(tmp: Path, **kw: object) -> Path:
    p = tmp / "paper.yaml"
    p.write_text(
        yaml.safe_dump({"initial_cash": 10_000.0, "strategies": [], "state_dir": str(tmp / "state"), **kw})
    )
    return p


def test_monitor_only_config_rules(tmp_path: Path) -> None:
    assert load_paper_config(_cfg(tmp_path, monitor_only=True)).monitor_only
    with pytest.raises(ValueError, match="monitor_only"):
        load_paper_config(_cfg(tmp_path, monitor_only=True, strategies=[{"name": "c2_trend"}]))


def test_monitor_only_runtime_end_to_end(tmp_path: Path) -> None:
    store = PITStore(tmp_path / "store")
    n = 24 * 12
    for m in ("spot", "perp"):
        for sym in ("BTCUSDT", "ETHUSDT"):
            ds = klines(m, "1h")
            store.write(
                ds, sym, parse_csv(ds, kline_csv_from_prices("2024-01-01", "1h", [100.0] * n, [100.0] * n))
            )
    for sym in ("BTCUSDT", "ETHUSDT"):
        store.write(FUNDING, sym, parse_csv(FUNDING, funding_csv("2024-01-01", 36)))
    cfg = load_paper_config(_cfg(tmp_path, monitor_only=True))
    service, close = build_paper_runtime(
        cfg, feed=StoreFeed(store), killswitch_port=0, now=lambda: pd.Timestamp("2024-01-10 12:00", tz="UTC")
    )
    try:
        service.run(threading.Event(), max_cycles=2)
    finally:
        close()
    state = tmp_path / "state"
    lines = (state / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 2 and all(json.loads(x)["orders"] == [] for x in lines)
    assert (state / "heartbeat").exists() and (state / "risk.db").exists()
