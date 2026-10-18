"""The RiskGate: deterministic code that every target passes through on its way to the exchange.

`evaluate` is a pure function of (targets, context, health, ledger state, limits). `RiskGate` wraps it with
the stateful parts: updating the persistent ledger and locking after a drawdown breach.

Modes, least to most severe; the most severe triggered mode wins:
  NORMAL       exposure caps only
  REDUCE_ONLY  positions may only shrink towards flat, never grow or flip
  HALT         state can't be trusted (API errors, reconciliation, clock): send nothing, hold positions
  FLATTEN      everything to zero. Beats HALT: flatten orders are reduce-only, so they can't open a
               position even if local state is wrong
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Protocol

import pandas as pd

from agent.core.types import Target
from agent.data.datasets import Market
from agent.risk.exposure import Decision, ExposureGate, Verdict
from agent.risk.ledger import LedgerState, RiskLedger
from agent.risk.limits import Limits

EPS = 1e-12

__all__ = [
    "HEALTHY",
    "GateContext",
    "GateResult",
    "Health",
    "LedgerState",
    "Mode",
    "RiskGate",
    "assess",
    "evaluate",
]


class Mode(IntEnum):
    NORMAL = 0
    REDUCE_ONLY = 1
    HALT = 2
    FLATTEN = 3


@dataclass(frozen=True)
class Health:
    """Live-system health inputs. Backtests use HEALTHY."""

    book_age_s: float = 0.0  # worst top-of-book age across traded instruments
    bars_late: int = 0  # worst bar lateness across required feeds
    clock_drift_ms: float = 0.0  # |local clock - exchange server time|
    reconciled: bool = True  # local positions/orders match the exchange
    depeg: float = 0.0  # |1 - collateral stablecoin price in USD|
    margin_ratio: float | None = None  # maintenance margin / margin balance; None without perps
    unhedged_groups: frozenset[str] = frozenset()  # hedge groups whose net delta breached tolerance


HEALTHY = Health()


@dataclass(frozen=True)
class GateContext:
    now: pd.Timestamp
    equity: float
    current: Mapping[str, float] = field(default_factory=dict)  # signed weights of current positions


@dataclass(frozen=True)
class GateResult:
    mode: Mode
    triggers: tuple[str, ...]
    decisions: list[Decision]
    cancel_all: bool
    submit_orders: bool
    lock: bool


@dataclass(frozen=True)
class _Rule:
    mode: Mode
    name: str
    fires: Callable[[GateContext, Health, LedgerState, Limits], bool]


def _loss(equity: float, reference: float) -> float:
    return 1 - equity / reference if reference > 0 else 0.0


# spec §4, one line per rule. Order only affects how triggers are listed; the most severe mode wins.
RULES: tuple[_Rule, ...] = (
    _Rule(Mode.FLATTEN, "invalid_equity",
          lambda c, h, led, lim: not (math.isfinite(c.equity) and c.equity > 0)),
    _Rule(Mode.FLATTEN, "max_drawdown",
          lambda c, h, led, lim: _loss(c.equity, led.peak_equity) >= lim.max_drawdown - EPS),
    _Rule(Mode.REDUCE_ONLY, "max_daily_loss",
          lambda c, h, led, lim: _loss(c.equity, led.day_start_equity) >= lim.max_daily_loss - EPS),
    _Rule(Mode.FLATTEN, "stale_book_flatten", lambda c, h, led, lim: h.book_age_s > lim.stale_flatten_s),
    _Rule(Mode.REDUCE_ONLY, "stale_book",
          lambda c, h, led, lim: lim.max_book_age_s < h.book_age_s <= lim.stale_flatten_s),
    _Rule(Mode.REDUCE_ONLY, "bars_late", lambda c, h, led, lim: h.bars_late > lim.max_bars_late),
    _Rule(Mode.HALT, "clock_drift", lambda c, h, led, lim: h.clock_drift_ms > lim.max_clock_drift_ms),
    _Rule(Mode.HALT, "unreconciled", lambda c, h, led, lim: not h.reconciled),
    _Rule(Mode.HALT, "api_errors", lambda c, h, led, lim: led.api_errors >= lim.api_error_limit),
    _Rule(Mode.HALT, "order_rejects", lambda c, h, led, lim: led.order_rejects >= lim.order_reject_limit),
    _Rule(Mode.FLATTEN, "depeg_flatten", lambda c, h, led, lim: h.depeg >= lim.depeg_flatten),
    _Rule(Mode.REDUCE_ONLY, "depeg", lambda c, h, led, lim: lim.depeg_reduce <= h.depeg < lim.depeg_flatten),
    _Rule(Mode.REDUCE_ONLY, "margin_ratio",
          lambda c, h, led, lim: h.margin_ratio is not None and h.margin_ratio > lim.max_margin_ratio),
)  # fmt: skip


def assess(
    ctx: GateContext, health: Health, ledger: LedgerState, limits: Limits
) -> tuple[Mode, tuple[str, ...], bool]:
    """Which mode applies, every rule that fired, and whether to lock (drawdown breach)."""
    fired = [(Mode.FLATTEN, f"locked: {ledger.lock_reason}")] if ledger.locked else []
    valid = math.isfinite(ctx.equity) and ctx.equity > 0
    for r in RULES:
        needs_equity = r.name in ("max_drawdown", "max_daily_loss")
        if (valid or not needs_equity) and r.fires(ctx, health, ledger, limits):
            fired.append((r.mode, r.name))
    names = tuple(n for _, n in fired)
    return max((m for m, _ in fired), default=Mode.NORMAL), names, "max_drawdown" in names


def _groups(targets: list[Target]) -> list[list[int]]:
    g: dict[str, list[int]] = {}
    for i, t in enumerate(targets):
        g.setdefault(t.group if t.group is not None else f"\0{i}", []).append(i)
    return list(g.values())


def _reduce_group(
    targets: list[Target],
    members: list[int],
    weights: list[float],
    *,
    ctx: GateContext,
    limits: Limits,
    margin: bool,
) -> bool:
    """Scale a group (or a lone target) so no leg grows or flips; under a margin breach also shrink perp
    groups to `margin_reduce_factor` x current. One factor per group keeps hedges balanced."""
    has_perp = any(targets[i].instrument.market is Market.PERP for i in members)
    factor = 1.0
    for i in members:
        w, c = weights[i], ctx.current.get(targets[i].instrument.key, 0.0)
        if w == 0:
            continue
        allowed = abs(c) if c != 0 and (w > 0) == (c > 0) else 0.0
        if margin and has_perp:
            allowed *= limits.margin_reduce_factor
        factor = min(factor, allowed / abs(w))
    if factor >= 1.0:
        return False
    for i in members:
        weights[i] *= factor
    return True


def _verdict(why: str, exposure: Decision) -> tuple[Verdict, str]:
    if exposure.verdict is Verdict.REJECT:
        return Verdict.REJECT, exposure.rule
    if why:
        return (Verdict.FLATTEN if why == "unhedged" else Verdict.REDUCE), why
    if exposure.verdict is Verdict.CLIP:
        return Verdict.CLIP, exposure.rule
    return Verdict.ACCEPT, ""


def evaluate(
    targets: list[Target], ctx: GateContext, health: Health, ledger: LedgerState, limits: Limits
) -> GateResult:
    mode, triggers, lock = assess(ctx, health, ledger, limits)
    trig = list(triggers)

    def rule(extra: str = "") -> str:
        return ",".join([*trig, extra] if extra else trig)

    if mode is Mode.HALT:
        decisions = [Decision(t, t.with_weight(ctx.current.get(t.instrument.key, 0.0)), Verdict.HOLD, rule())
                     for t in targets]  # fmt: skip
        return GateResult(mode, triggers, decisions, cancel_all=True, submit_orders=False, lock=lock)
    if mode is Mode.FLATTEN:
        decisions = [Decision(t, t.with_weight(0.0), Verdict.FLATTEN, rule()) for t in targets]
        return GateResult(mode, triggers, decisions, cancel_all=True, submit_orders=True, lock=lock)

    weights = [t.weight for t in targets]
    why = [""] * len(targets)
    for members in _groups(targets):
        group = targets[members[0]].group
        if group is not None and group in health.unhedged_groups:
            trig.append(f"unhedged:{group}")
            for i in members:
                weights[i], why[i] = 0.0, "unhedged"
        elif mode is Mode.REDUCE_ONLY and _reduce_group(
            targets, members, weights, ctx=ctx, limits=limits, margin="margin_ratio" in triggers
        ):
            for i in members:
                why[i] = "reduce_only" if targets[i].weight else ""

    capped = ExposureGate(max_weight=limits.max_weight, max_gross=limits.max_gross).check(
        [t.with_weight(w) for t, w in zip(targets, weights, strict=True)]
    )
    decisions = []
    for t, w, d in zip(targets, why, capped, strict=True):
        verdict, extra = _verdict(w, d)
        decisions.append(Decision(t, d.target, verdict, rule(extra)))
    return GateResult(mode, tuple(trig), decisions, cancel_all=False, submit_orders=True, lock=lock)


class _KillSwitchLike(Protocol):
    def tripped(self, now: pd.Timestamp) -> bool: ...


class RiskGate:
    """Stateful wrapper: records equity in the persistent ledger, evaluates, and locks on drawdown."""

    def __init__(
        self,
        limits: Limits,
        ledger: RiskLedger,
        *,
        health: Callable[[pd.Timestamp], Health] = lambda t: HEALTHY,
        killswitch: _KillSwitchLike | None = None,
    ) -> None:
        self.limits, self.ledger, self.health, self.killswitch = limits, ledger, health, killswitch
        self.last: GateResult | None = None

    def check(self, targets: list[Target], ctx: GateContext | None = None) -> list[Decision]:
        if ctx is None:
            raise ValueError("RiskGate needs a GateContext (time, equity, current positions)")
        self.ledger.on_equity(ctx.now, ctx.equity)
        if self.killswitch is not None:
            self.killswitch.tripped(ctx.now)  # turns an operator's flag file into a persistent lock
        state = self.ledger.state(ctx.now, window_s=self.limits.api_window_s)
        result = evaluate(targets, ctx, self.health(ctx.now), state, self.limits)
        if result.lock:
            self.ledger.lock(ctx.now, "max_drawdown", source="gate")
        if self.last is None or result.mode is not self.last.mode:
            self.ledger.record(ctx.now, "mode", mode=result.mode.name, triggers=list(result.triggers))
        self.last = result
        return result.decisions
