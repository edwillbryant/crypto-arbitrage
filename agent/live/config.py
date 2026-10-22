"""Paper-trading configuration, with the approval and hold-out-lock checks enforced at load time.

A strategy can be enabled only if:
  1. it is registered and live-eligible (C4 analyzer and C5 control never are),
  2. a hold-out lock file exists (M4) and its parameters match the config exactly, and
  3. the owner's approval is recorded (M7).
Exchange symbol rules below are [UNVERIFIED] placeholders for paper/replay; live trading reads them from
the exchange's market metadata instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.execution.types import SymbolRules
from agent.strategies.registry import REGISTRY

DEFAULT_RULES: dict[str, SymbolRules] = {
    "perp:BTCUSDT": SymbolRules(step=0.001, tick=0.1, min_notional=100.0),
    "perp:ETHUSDT": SymbolRules(step=0.001, tick=0.01, min_notional=20.0),
    "spot:BTCUSDT": SymbolRules(step=0.00001, tick=0.01, min_notional=5.0),
    "spot:ETHUSDT": SymbolRules(step=0.0001, tick=0.01, min_notional=5.0),
}


class StrategyEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    params: dict[str, Any]
    lock_file: str
    approved_by: str
    approved_on: str

    @model_validator(mode="after")
    def _checks(self) -> StrategyEntry:
        spec = REGISTRY.get(self.name)
        if spec is None:
            raise ValueError(f"unknown strategy {self.name!r}")
        if not spec.live_eligible:
            raise ValueError(f"{self.name} is not eligible for paper or live trading")
        if not self.approved_by.strip() or not self.approved_on.strip():
            raise ValueError("approved_by and approved_on are required (owner approval at M7)")
        lock = Path(self.lock_file)
        if not lock.exists():
            raise ValueError(f"hold-out lock file {lock} not found: run M4 first")
        locked = json.loads(lock.read_text())
        if locked.get("strategy") != self.name or locked.get("params") != self.params:
            raise ValueError(f"params for {self.name} do not match the hold-out lock {lock}")
        spec.build(self.params)  # constructs, so invalid parameters fail at load time
        return self


class PaperConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    initial_cash: float = Field(10_000.0, gt=0)
    strategies: list[StrategyEntry] = Field(default_factory=list)
    state_dir: str = "data/state/paper"
    limits_file: str = "config/limits.yaml"
    # Run the full loop with no strategy (flat targets): the M7 infrastructure test after M4 found no edge.
    monitor_only: bool = False
    cycle_seconds: int = Field(default=60, ge=5)
    killswitch_port: int = 8765  # localhost only
    futures_base: str = "https://fapi.binance.com"
    spot_base: str = "https://api.binance.com"
    watchdog_url: str | None = None  # e.g. http://watchdog-host:8787 ; secret read from the env var below
    watchdog_secret_env: str = "AGENT_WATCHDOG_SECRET"
    alert_webhook_env: str = "AGENT_ALERT_WEBHOOK"

    @model_validator(mode="before")
    @classmethod
    def _one_mode(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get("monitor_only") and data.get("strategies"):
                raise ValueError("monitor_only and strategies are mutually exclusive: pick one")
            if len(data.get("strategies") or []) > 1:
                raise ValueError("v1 paper trading runs one strategy at a time")
        return data


def load_paper_config(path: Path) -> PaperConfig:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return PaperConfig(**data)
