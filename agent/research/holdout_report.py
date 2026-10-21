"""M4 hold-out report: verdicts per docs/06-HOLDOUT-PROTOCOL.md, every check shown with its value."""

from __future__ import annotations

import math
from typing import Any

from agent.research.dsr import deflated_sharpe_from_trials
from agent.research.holdout import verdict
from agent.research.registry import Registry
from agent.research.runner import SEGMENTS

TOTAL_VARIANTS = 247  # all pre-registered variants of all strategies (C1 33, C2 16, C3 54, C5 144)


def train_daily_srs(reg: Registry, strategy: str) -> list[float]:
    """Daily SRs of the train 1x variants, from the one-off `backfill-daily` diagnostic runs."""
    start, end = (t.isoformat() for t in SEGMENTS["train"])
    return [
        r["metrics"]["sr_daily"]
        for r in reg.trials(strategy)
        if r["segment"] == "diagnostic"
        and r["start"] == start
        and r["end"] == end
        and r["cost_multiplier"] == 1.0
    ]


def _f(v: Any, fmt: str = "{:.2f}") -> str:
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else fmt.format(v)


def section(reg: Registry, strategy: str) -> list[str]:
    recs = [r for r in reg.trials(strategy) if r["segment"] == "holdout"]
    if not recs:
        return [f"## {strategy}", "", "hold-out not run", ""]
    srs = train_daily_srs(reg, strategy)
    v = verdict(strategy, recs, srs)
    m = next(r for r in recs if r["universe"] == "both" and r["cost_multiplier"] == 1.0)["metrics"]
    all_srs = srs  # only C1/C2 train daily SRs exist; N=247 below uses their spread: an approximation
    try:
        dsr_all = deflated_sharpe_from_trials(
            m["sr_daily"],
            n_obs=int(m["n_days"]),
            skew=m["skew_daily"],
            kurt=m["kurt_daily"],
            trial_srs=all_srs * (TOTAL_VARIANTS // max(len(all_srs), 1)),
        )
    except (ValueError, TypeError):
        dsr_all = math.nan
    lines = [
        f"## {strategy}: **{v['verdict']}**",
        "",
        f"Locked params: `{recs[0]['params']}`",
        "",
        "| Check | Result | Value |",
        "|---|---|---|",
    ]
    lines += [f"| {k} | {'✅' if ok else '❌'} | {d} |" for k, (ok, d) in v["checks"].items()]
    lines += [
        "",
        f"Not binding: DSR with N={TOTAL_VARIANTS} (spread approximated from this strategy's variants): "
        f"{_f(dsr_all, '{:.3f}')}",
        "",
        "| Universe | Costs | Sharpe (daily) | Max DD | Trades | Net PnL | Funding | Fees | "
        "Max trade share | Max month share | Gate lock |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in recs:
        mm = r["metrics"]
        lines.append(
            f"| {r['universe']} | {r['cost_multiplier']:g}x | {_f(mm['sharpe_daily'])} | "
            f"{mm['max_drawdown']:.1%} | "
            f"{mm['trades']} | {mm['net_pnl']:,.0f} | {mm['funding']:,.0f} | {mm['fees']:,.0f} | "
            f"{_f(r['max_trade_share'], '{:.0%}')} | {_f(r['max_month_share'], '{:.0%}')} | "
            f"{(r['gate_locked_at'] or 'never')[:10]} |"
        )
    return [*lines, ""]


def build(reg: Registry, strategies: list[str]) -> str:
    out = [
        "# M4 hold-out results (2025-01-01 → 2026-10-01)",
        "",
        "Run once per locked strategy under the production RiskGate, per `docs/06-HOLDOUT-PROTOCOL.md`.",
        "Sharpe ratios are on daily returns. Backtests on $10,000; not forecasts.",
        "",
    ]
    for s in strategies:
        out += section(reg, s)
    return "\n".join(out) + "\n"
