"""M4 hold-out: lock a cell, run it once on 2025-01-01 -> 2026-10-01 under the production RiskGate, judge it.

Rules are in docs/06-HOLDOUT-PROTOCOL.md (pre-registered). Guards enforced here:
- only pre-registered variants can be locked, and a lock is immutable;
- the lock must be committed to git, and the protocol unchanged since locking (its SHA-256 is in the lock);
- a strategy's hold-out runs exactly once: a second invocation is refused.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from agent.data.store import PITStore
from agent.research.costs import CostModel
from agent.research.dsr import deflated_sharpe_from_trials
from agent.research.engine import BacktestConfig, BacktestResult, run_backtest
from agent.research.metrics import summarize
from agent.research.registry import Registry, _git_state
from agent.research.runner import EXEC_INTERVAL, HOLDOUT_START, check_quality
from agent.risk.gate import RiskGate
from agent.risk.ledger import RiskLedger
from agent.risk.limits import Limits
from agent.sizing.voltarget import VolTargetSizer
from agent.strategies.registry import REGISTRY, variants

HOLDOUT: tuple[pd.Timestamp, pd.Timestamp] = (HOLDOUT_START, pd.Timestamp("2026-10-01", tz="UTC"))
RUNS: tuple[tuple[str, float], ...] = (
    ("both", 1.0),
    ("both", 2.0),
    ("both", 3.0),
    ("BTCUSDT", 1.0),
    ("ETHUSDT", 1.0),
)
MIN_TRADES, MIN_FUNDING_PERIODS, MIN_DSR, MAX_SHARE = 30, 100, 0.95, 0.25


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_committed(path: Path) -> bool:
    """True if `path` is tracked and has no uncommitted changes."""
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)], capture_output=True, check=False
        )
        clean = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", str(path)], capture_output=True, check=False
        )
    except OSError:
        return False
    return tracked.returncode == 0 and clean.returncode == 0


def _has_holdout(registry: Registry, strategy: str) -> bool:
    return any(r.get("segment") == "holdout" for r in registry.trials(strategy))


def make_lock(
    strategy: str, params: dict[str, Any], *, locks_dir: Path, protocol_path: Path, registry: Registry
) -> Path:
    if params not in variants(strategy):
        raise ValueError(f"{params} is not a pre-registered variant of {strategy}")
    if _has_holdout(registry, strategy):
        raise PermissionError(f"{strategy} already has hold-out results; a new lock would be a second look")
    path = Path(locks_dir) / f"{strategy}.json"
    if path.exists():
        raise FileExistsError(f"{path} exists: locks are immutable")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = {
        "strategy": strategy,
        "params": params,
        "locked_at": dt.datetime.now(dt.UTC).isoformat(),
        "git": _git_state(),
        "protocol": str(protocol_path),
        "protocol_sha256": _sha(protocol_path),
    }
    path.write_text(json.dumps(lock, indent=1, sort_keys=True) + "\n")
    return path


def _shares(result: BacktestResult) -> tuple[float, float]:
    """Largest single round trip and largest calendar month, as a share of total net PnL."""
    total = float(result.equity.iloc[-1] - result.equity.iloc[0])
    if not total > 0:
        return math.nan, math.nan
    trade = float(result.episodes["pnl"].max()) / total if len(result.episodes) else 0.0
    monthly = result.equity.resample("ME").last().diff()
    monthly.iloc[0] = result.equity.resample("ME").last().iloc[0] - result.equity.iloc[0]
    return trade, float(monthly.max()) / total


def run_holdout(
    strategy: str,
    *,
    store: PITStore,
    quality_status: Path,
    locks_dir: Path,
    registry: Registry,
    protocol_path: Path,
    period: tuple[pd.Timestamp, pd.Timestamp] = HOLDOUT,
    is_committed: Callable[[Path], bool] = git_committed,
    limits: Limits | None = None,
) -> list[dict[str, Any]]:
    lock_path = Path(locks_dir) / f"{strategy}.json"
    if not lock_path.exists():
        raise PermissionError(f"no lock for {strategy}: run `agent research lock` first")
    lock = json.loads(lock_path.read_text())
    if not is_committed(lock_path):
        raise PermissionError(f"{lock_path} must be committed before the hold-out run")
    if _sha(protocol_path) != lock["protocol_sha256"]:
        raise PermissionError("the hold-out protocol changed after the lock was made")
    if _has_holdout(registry, strategy):
        raise PermissionError(f"{strategy} hold-out has already been run; it is run once")
    spec, params = REGISTRY[strategy], lock["params"]
    start, end = period
    records = []
    for universe, cost in RUNS:
        strat = spec.build(params, symbols=None if universe == "both" else (universe,))
        check_quality(strat, quality_status)
        ledger = RiskLedger(":memory:")
        gate = RiskGate(limits or Limits(), ledger)
        cfg = BacktestConfig(start=start, end=end, initial_equity=10_000.0, exec_interval=EXEC_INTERVAL)
        res = run_backtest(
            strat, store, cfg, sizer=VolTargetSizer(), gate=gate, costs=CostModel().scaled(cost)
        )
        locks = [e for e in ledger.events() if e["kind"] == "lock"]
        trade_share, month_share = _shares(res)
        records.append(
            {
                "strategy": strategy,
                "params": params,
                "segment": "holdout",
                "universe": universe,
                "cost_multiplier": cost,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "metrics": summarize(res, strat.decision_interval),
                "max_trade_share": trade_share,
                "max_month_share": month_share,
                "funding_periods_held": int(res.funding["time"].nunique()),
                "gate_locked_at": locks[0]["time"].isoformat() if locks else None,
                "gate_modes": res.decisions["verdict"].value_counts().to_dict(),
                "lock_file": str(lock_path),
                "lock_sha256": _sha(lock_path),
            }
        )
        ledger.close()
    for rec in records:
        registry.append_holdout(rec, lock=lock)
    return records


def verdict(strategy: str, records: list[dict[str, Any]], train_daily_srs: list[float]) -> dict[str, Any]:
    """Apply docs/06-HOLDOUT-PROTOCOL.md, in order. Returns the verdict and every check with its value."""
    by = {(r["universe"], r["cost_multiplier"]): r for r in records}
    main, m = by[("both", 1.0)], by[("both", 1.0)]["metrics"]
    checks: dict[str, tuple[bool, str]] = {}

    def check(name: str, ok: bool, detail: str) -> bool:
        checks[name] = (ok, detail)
        return ok

    a = check("sharpe_1x>0", m["sharpe_daily"] > 0, f"{m['sharpe_daily']:.2f}")
    b = check(
        "pnl_2x>0",
        by[("both", 2.0)]["metrics"]["net_pnl"] > 0,
        f"{by[('both', 2.0)]['metrics']['net_pnl']:,.0f}",
    )
    enough = m["trades"] >= MIN_TRADES or (
        strategy == "c1_carry" and main["funding_periods_held"] >= MIN_FUNDING_PERIODS
    )
    check("evidence", enough, f"{m['trades']} trades, {main['funding_periods_held']} funding periods held")
    try:
        dsr = deflated_sharpe_from_trials(
            m["sr_daily"],
            n_obs=int(m["n_days"]),
            skew=m["skew_daily"],
            kurt=m["kurt_daily"],
            trial_srs=train_daily_srs,
        )
    except (ValueError, TypeError):
        dsr = math.nan
    c = check("dsr>0.95", dsr > MIN_DSR, f"{dsr:.3f} (N={len(train_daily_srs)})")
    d = check(
        "both_assets",
        all(by[(s, 1.0)]["metrics"]["sharpe_daily"] > 0 for s in ("BTCUSDT", "ETHUSDT")),
        ", ".join(f"{s} {by[(s, 1.0)]['metrics']['sharpe_daily']:.2f}" for s in ("BTCUSDT", "ETHUSDT")),
    )
    e = check(
        "concentration<=25%",
        main["max_trade_share"] <= MAX_SHARE and main["max_month_share"] <= MAX_SHARE,
        f"trade {main['max_trade_share']:.0%}, month {main['max_month_share']:.0%}",
    )
    if not (a and b):
        result = "REJECT"
    elif not enough:
        result = "INSUFFICIENT EVIDENCE"
    elif not (c and d and e):
        result = "REJECT"
    else:
        result = "PASS"
    return {"verdict": result, "dsr": dsr, "checks": checks}
