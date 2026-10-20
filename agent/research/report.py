"""M3b sweep report: applies the pre-registered selection rule to recorded runs; writes CSVs + markdown."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from agent.research.campaign import results_frame
from agent.research.dsr import deflated_sharpe_from_trials
from agent.research.registry import Registry
from agent.research.runner import select
from agent.strategies.registry import REGISTRY

COLS = ["sharpe", "max_drawdown", "trades", "win_rate", "profit_factor", "net_pnl", "costs_over_gross"]


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return "n/a" if math.isnan(v) else (f"{v:,.0f}" if abs(v) >= 100 else f"{v:.2f}")
    return "n/a" if v is None else str(v)


def _lock(v: object) -> str:
    return "never" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)[:10]


def _dsr(train1: pd.DataFrame, row: pd.Series) -> float:
    try:
        return deflated_sharpe_from_trials(
            float(row["sr_per_period"]),
            n_obs=int(row["n_obs"]),
            skew=float(row["skew"]),
            kurt=float(row["kurt"]),
            trial_srs=train1["sr_per_period"].astype(float).tolist(),
        )
    except (ValueError, TypeError):
        return math.nan


def _section(reg: Registry, name: str, out_dir: Path) -> list[str]:
    df = results_frame(reg, name)
    lines = [f"## {name}", ""]
    if df.empty:
        return [*lines, "no runs recorded", ""]
    params = list(REGISTRY[name].grid)
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / f"{name}.csv", index=False)
    sel = select(df, params)
    train1 = df[(df["segment"] == "train") & (df["cost_multiplier"] == 1.0)]
    survivors = int(sel.table["survives"].sum()) if not sel.table.empty else 0
    lines += [
        f"**Verdict: {sel.verdict}**: {sel.reason}",
        "",
        f"- Variants: {len(train1)}; train Sharpe > 0: {int((train1['sharpe'] > 0).sum())}; "
        f"survivors of the robustness rule: {survivors}",
        f"- Train Sharpe across variants (1x costs): median {train1['sharpe'].median():.2f}, "
        f"best {train1['sharpe'].max():.2f}, worst {train1['sharpe'].min():.2f}",
    ]
    locked = df[(df["segment"] == "train") & (df["cost_multiplier"] == 1.0) & df["would_lock_at"].notna()]
    lines.append(
        f"- {len(locked)} of {len(train1)} variants would have locked (8% drawdown) at some point in train"
    )
    if sel.params is None:
        return [*lines, ""]
    mask = pd.Series(True, index=df.index)
    for p, v in sel.params.items():
        mask &= df[p] == v
    chosen = df[mask].sort_values(["segment", "cost_multiplier"], ascending=[False, True])
    row1 = chosen[(chosen["segment"] == "train") & (chosen["cost_multiplier"] == 1.0)].iloc[0]
    lines += [
        f"- **Chosen cell:** `{sel.params}`",
        f"- Deflated Sharpe (train, N = {len(train1)} trials): {_dsr(train1, row1):.3f} "
        "(the binding test is M4, on the hold-out)",
        f"- Production 8% drawdown lock in train: {_lock(row1['would_lock_at'])}",
        "",
        "| Segment | Costs | " + " | ".join(COLS) + " | would lock at |",
        "|---|---|" + "---|" * (len(COLS) + 1),
    ]
    for _, r in chosen.iterrows():
        lines.append(
            f"| {r['segment']} | {r['cost_multiplier']:.0f}x | "
            + " | ".join(_fmt(r[c]) for c in COLS)
            + f" | {_lock(r['would_lock_at'])} |"
        )
    return [*lines, ""]


def build_report(reg: Registry, strategies: list[str], *, out_dir: Path) -> str:
    lines = [
        "# M3b sweep results",
        "",
        "Generated from `research/trials.jsonl` by `python -m agent research report`. Selection follows",
        "`docs/04-RESEARCH-PROTOCOL.md` exactly. Hold-out (2025-01-01 → 2026-10-01) untouched.",
        "",
    ]
    for name in strategies:
        lines += _section(reg, name, out_dir)
    return "\n".join(lines) + "\n"
