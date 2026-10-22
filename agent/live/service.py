"""The long-running paper service: one LiveLoop cycle every `cycle` seconds, until told to stop.

Around each cycle it alerts on risk-mode changes (not on every cycle), sends the watchdog heartbeat, and keeps
daily summaries (cycles per mode, errors) written at each UTC day rollover and on exit. A crashing cycle is
alerted and the service carries on: with no fresh cycle the watchdog and staleness rules take over.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from agent.execution.hedge import HedgeMonitor
from agent.execution.order_manager import OrderManager
from agent.execution.paper import PaperBroker
from agent.execution.reconciler import Reconciler
from agent.live.binance_feed import BinanceRestFeed
from agent.live.config import DEFAULT_RULES, PaperConfig
from agent.live.feed import MarketFeed
from agent.live.loop import CycleReport, LiveLoop
from agent.ops.alerts import AlertSink
from agent.ops.heartbeat import HeartbeatClient
from agent.ops.log import DecisionLog
from agent.risk.gate import Mode, RiskGate
from agent.risk.killswitch import KillSwitch, serve_http
from agent.risk.ledger import RiskLedger
from agent.risk.limits import load_limits
from agent.sizing.voltarget import VolTargetSizer
from agent.strategies.monitor import MonitorOnly
from agent.strategies.registry import REGISTRY

LEVEL = {Mode.NORMAL: "INFO", Mode.REDUCE_ONLY: "WARN", Mode.HALT: "CRITICAL", Mode.FLATTEN: "CRITICAL"}


class _Loop(Protocol):
    def step(self, now: pd.Timestamp) -> CycleReport: ...


class _Beat(Protocol):
    def send(self, now: pd.Timestamp, mode: str) -> bool: ...


class Service:
    def __init__(
        self,
        loop: _Loop,
        *,
        now: Callable[[], pd.Timestamp],
        cycle: pd.Timedelta,
        alerts: AlertSink,
        heartbeat: _Beat | None,
        summary_dir: Path,
    ) -> None:
        self.loop, self.now, self.cycle, self.alerts, self.heartbeat = loop, now, cycle, alerts, heartbeat
        self.summary_dir = summary_dir
        self._day: Any = None
        self._stats: dict[str, Any] = {}

    def _flush(self) -> None:
        if self._day is None or not self._stats.get("cycles"):
            return
        self.summary_dir.mkdir(parents=True, exist_ok=True)
        (self.summary_dir / f"{self._day}.json").write_text(json.dumps(self._stats, indent=1, sort_keys=True))

    def run(self, stop: Any, max_cycles: int | None = None) -> int:
        prev: Mode | None = None
        done = 0
        while not stop.is_set() and (max_cycles is None or done < max_cycles):
            started = time.monotonic()
            now = self.now().floor("s")
            if self._day != now.date():
                self._flush()
                self._day, self._stats = now.date(), {"cycles": 0, "modes": {}, "errors": 0, "crashes": 0}
            try:
                rep = self.loop.step(now)
            except Exception as e:  # broad on purpose: alert and keep the service alive
                self._stats["crashes"] += 1
                self.alerts.send("CRITICAL", "cycle crashed", repr(e), now=now)
                mode = Mode.HALT
            else:
                mode = rep.mode
                self._stats["errors"] += sum(1 for ev in rep.events if ev.startswith(("error", "reject")))
                if mode is not prev and (prev is not None or mode is not Mode.NORMAL):
                    self.alerts.send(LEVEL[mode], f"mode {mode.name}", ", ".join(rep.triggers), now=now)
            self._stats["cycles"] += 1
            self._stats["modes"][mode.name] = self._stats["modes"].get(mode.name, 0) + 1
            prev = mode
            if self.heartbeat is not None:
                self.heartbeat.send(now, mode.name)
            done += 1
            stop.wait(max(0.0, self.cycle.total_seconds() - (time.monotonic() - started)))
        self._flush()
        return done


def build_paper_runtime(
    cfg: PaperConfig,
    *,
    feed: MarketFeed | None = None,
    killswitch_port: int | None = None,
    now: Callable[[], pd.Timestamp] | None = None,
) -> tuple[Service, Callable[[], None]]:
    """Wire the paper runtime from config. Returns the service and a close() for its resources."""
    state = Path(cfg.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    limits, digest = load_limits(Path(cfg.limits_file))
    if cfg.monitor_only:
        strategy: Any = MonitorOnly()
    elif cfg.strategies:
        entry = cfg.strategies[0]  # loading the config already checked its lock file and approval
        strategy = REGISTRY[entry.name].build(entry.params)
    else:
        raise ValueError("config enables nothing: set monitor_only: true or add an approved strategy")
    feed = feed or BinanceRestFeed(
        instruments=strategy.instruments(), futures_base=cfg.futures_base, spot_base=cfg.spot_base
    )
    secrets = [
        v for v in (os.environ.get(cfg.watchdog_secret_env), os.environ.get(cfg.alert_webhook_env)) if v
    ]
    broker = PaperBroker(
        cash=cfg.initial_cash,
        rules=DEFAULT_RULES,
        book=feed.book,
        taker_fee_bps={"perp": 5.0, "spot": 10.0},
        state_path=state / "paper.json",
    )
    ledger = RiskLedger(state / "risk.db")
    ks = KillSwitch(ledger, state / "KILL")
    http = serve_http(ks, port=cfg.killswitch_port if killswitch_port is None else killswitch_port)
    loop = LiveLoop(
        strategy=strategy,
        feed=feed,
        broker=broker,
        gate=RiskGate(limits, ledger, killswitch=ks),
        sizer=VolTargetSizer(),
        orders=OrderManager(broker, on_error=lambda k, d: None),
        reconciler=Reconciler(DEFAULT_RULES),
        hedge=HedgeMonitor(),
        ledger=ledger,
        log=DecisionLog(state / "decisions.jsonl", secrets=secrets),
        heartbeat_path=state / "heartbeat",
    )
    alerts = AlertSink(
        state / "alerts.jsonl", webhook_url=os.environ.get(cfg.alert_webhook_env), secrets=secrets
    )
    wd_secret = os.environ.get(cfg.watchdog_secret_env)
    beat = HeartbeatClient(cfg.watchdog_url, wd_secret) if cfg.watchdog_url and wd_secret else None
    clock = now or (lambda: pd.Timestamp.now(tz="UTC"))
    service = Service(
        loop,
        now=clock,
        cycle=pd.Timedelta(seconds=cfg.cycle_seconds),
        alerts=alerts,
        heartbeat=beat,
        summary_dir=state / "daily",
    )
    alerts.send(
        "INFO",
        "paper service started",
        f"strategy={strategy.name} limits_sha256={digest[:12]} watchdog={'on' if beat else 'off'}",
        now=clock(),
    )

    def close() -> None:
        http.shutdown()
        http.server_close()
        ledger.close()

    return service, close
