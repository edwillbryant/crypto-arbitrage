"""Research runner: one backtest per (strategy, params, segment, cost multiplier), under the guards in
docs/04-RESEARCH-PROTOCOL.md.

Guards: no run may end after HOLDOUT_START (the hold-out is M4's, once); every dataset a run needs must be
PASS in the latest quality report; every run is appended to the trial registry by the caller.
Selection is the pre-registered rule, as code, so nobody picks a cell by eye.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from agent.data.datasets import FUNDING, Dataset, Market, klines
from agent.data.store import PITStore
from agent.research.costs import CostModel
from agent.research.engine import BacktestConfig, run_backtest
from agent.research.metrics import summarize
from agent.research.sweep import neighbour_stats
from agent.risk.exposure import ExposureGate
from agent.risk.limits import Limits
from agent.sizing.voltarget import VolTargetSizer
from agent.strategies.base import Strategy
from agent.strategies.registry import REGISTRY

HOLDOUT_START = pd.Timestamp("2025-01-01", tz="UTC")
SEGMENTS: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {
    "train": (pd.Timestamp("2020-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    "validation": (pd.Timestamp("2024-01-01", tz="UTC"), HOLDOUT_START),
}
EXEC_INTERVAL = "1h"
MIN_NEIGHBOUR_POSITIVE = 0.7


class QualityGateError(RuntimeError):
    """A dataset the run needs has no PASS in the latest quality report."""


def required_datasets(strategy: Strategy) -> list[tuple[Dataset, str]]:
    req = {(r.dataset, r.symbol) for r in strategy.requests()}
    for i in strategy.instruments():
        req.add((klines(i.market, EXEC_INTERVAL), i.symbol))
        if i.market is Market.PERP:
            req.add((FUNDING, i.symbol))
    return sorted(req, key=lambda x: (x[0].name, x[1]))


def check_quality(strategy: Strategy, status_path: Path) -> None:
    if not Path(status_path).exists():
        raise QualityGateError(
            f"no quality report at {status_path}: run `python -m agent data quality` first"
        )
    status = json.loads(Path(status_path).read_text())["datasets"]
    bad = [f"{d.name}:{s}={status.get(f'{d.name}:{s}', 'missing')}" for d, s in required_datasets(strategy)
           if status.get(f"{d.name}:{s}") != "PASS"]  # fmt: skip
    if bad:
        raise QualityGateError(f"{strategy.name} needs datasets that are not PASS (FAIL or missing): {bad}")


def first_lock_time(equity: pd.Series, max_drawdown: float) -> pd.Timestamp | None:
    """When the production drawdown lock (flatten + human unlock) would first have fired, if ever."""
    dd = 1 - equity / equity.cummax()
    hit = dd[dd >= max_drawdown - 1e-12]
    return None if hit.empty else hit.index[0]


def run_variant(
    name: str,
    params: dict[str, Any],
    *,
    store: PITStore,
    quality_status: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    segment: str,
    cost_multiplier: float,
    initial_equity: float = 10_000.0,
) -> dict[str, Any]:
    if segment not in SEGMENTS or end > HOLDOUT_START:
        raise PermissionError(
            f"research runs must end by {HOLDOUT_START.date()}; the hold-out is M4's, run once"
        )
    strategy = REGISTRY[name].build(params)
    check_quality(strategy, quality_status)
    cfg = BacktestConfig(start=start, end=end, initial_equity=initial_equity, exec_interval=EXEC_INTERVAL)
    result = run_backtest(strategy, store, cfg, sizer=VolTargetSizer(), gate=ExposureGate(),
                          costs=CostModel().scaled(cost_multiplier))  # fmt: skip
    lock = first_lock_time(result.equity, Limits().max_drawdown)
    return {
        "strategy": name,
        "params": params,
        "segment": segment,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cost_multiplier": cost_multiplier,
        "metrics": summarize(result, strategy.decision_interval),
        "would_lock_at": lock.isoformat() if lock is not None else None,
        "events": len(result.events),
    }


@dataclass
class Selection:
    verdict: str  # PROPOSE | FAILS_VALIDATION | REJECT
    params: dict[str, Any] | None
    reason: str
    table: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)


def select(results: pd.DataFrame, params: list[str]) -> Selection:
    """The pre-registered rule (docs/04-RESEARCH-PROTOCOL.md). `results`: one row per run with the param
    columns plus segment, cost_multiplier, sharpe and net_pnl."""

    def seg(segment: str, cost: float) -> pd.DataFrame:
        return results[(results["segment"] == segment) & (results["cost_multiplier"] == cost)].reset_index(
            drop=True
        )

    train = seg("train", 1.0)
    stats = neighbour_stats(train, params, "sharpe")
    table = train[[*params, "sharpe"]].merge(stats, on=params)
    pnl2 = seg("train", 2.0)[[*params, "net_pnl"]].rename(columns={"net_pnl": "net_pnl_2x"})
    table = table.merge(pnl2, on=params, how="left")
    table["survives"] = (
        (table["sharpe"] > 0) & (table["frac_positive"] >= MIN_NEIGHBOUR_POSITIVE) & (table["net_pnl_2x"] > 0)
    )
    survivors = table[table["survives"]]
    if survivors.empty:
        return Selection(
            "REJECT", None, "no cell has Sharpe > 0, >= 70% positive neighbours and profit at 2x costs", table
        )
    best = survivors.sort_values(["neighbour_median", "sharpe"], ascending=False).iloc[0]
    chosen = {p: _py(best[p]) for p in params}
    val = seg("validation", 1.0)
    mask = pd.Series(True, index=val.index)
    for p, v in chosen.items():
        mask &= val[p] == v
    v_sharpe = float(val.loc[mask, "sharpe"].iloc[0]) if mask.any() else math.nan
    if not v_sharpe > 0:
        return Selection("FAILS_VALIDATION", chosen, f"validation Sharpe {v_sharpe:.2f} <= 0", table)
    return Selection(
        "PROPOSE", chosen, f"plateau median {best['neighbour_median']:.2f}, validation {v_sharpe:.2f}", table
    )


def _py(v: Any) -> Any:
    return v.item() if hasattr(v, "item") else v
