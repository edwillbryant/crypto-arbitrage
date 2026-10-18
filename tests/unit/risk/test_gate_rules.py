"""Each RiskGate trigger in isolation, and the precedence between them."""

from __future__ import annotations

import pandas as pd
import pytest

from agent.core.types import Instrument, Target
from agent.risk.exposure import Verdict
from agent.risk.gate import HEALTHY, GateContext, Health, LedgerState, Mode, assess, evaluate
from agent.risk.limits import Limits

T = pd.Timestamp("2024-03-01 10:00", tz="UTC")
LIM = Limits()
BTC = Instrument("BTCUSDT", "perp")
SPOT = Instrument("BTCUSDT", "spot")
ETH = Instrument("ETHUSDT", "perp")


def _ledger(**kw: object) -> LedgerState:
    base: dict[str, object] = {"peak_equity": 1000.0, "day_start_equity": 1000.0}
    return LedgerState(**{**base, **kw})  # type: ignore[arg-type]


def _mode(equity: float = 1000.0, health: Health = HEALTHY, **ledger: object) -> tuple[Mode, tuple[str, ...]]:
    mode, triggers, _ = assess(GateContext(T, equity), health, _ledger(**ledger), LIM)
    return mode, triggers


def test_healthy_is_normal() -> None:
    assert _mode() == (Mode.NORMAL, ())


@pytest.mark.parametrize(
    ("equity", "expected"), [(990.1, Mode.NORMAL), (990.0, Mode.REDUCE_ONLY), (950.0, Mode.REDUCE_ONLY)]
)
def test_daily_loss(equity: float, expected: Mode) -> None:
    assert _mode(equity, peak_equity=1000.0, day_start_equity=1000.0)[0] is expected


def test_drawdown_flattens_and_locks() -> None:
    mode, triggers, lock = assess(
        GateContext(T, 919.0), HEALTHY, _ledger(peak_equity=1000.0, day_start_equity=919.0), LIM
    )
    assert mode is Mode.FLATTEN and "max_drawdown" in triggers and lock
    assert _mode(921.0, peak_equity=1000.0, day_start_equity=921.0)[0] is Mode.NORMAL


def test_locked_ledger_flattens() -> None:
    assert _mode(locked=True, lock_reason="kill switch") == (Mode.FLATTEN, ("locked: kill switch",))


@pytest.mark.parametrize(
    ("health", "expected", "trigger"),
    [
        (Health(book_age_s=5.0), Mode.NORMAL, None),
        (Health(book_age_s=6.0), Mode.REDUCE_ONLY, "stale_book"),
        (Health(book_age_s=61.0), Mode.FLATTEN, "stale_book_flatten"),
        (Health(bars_late=2), Mode.REDUCE_ONLY, "bars_late"),
        (Health(clock_drift_ms=600.0), Mode.HALT, "clock_drift"),
        (Health(reconciled=False), Mode.HALT, "unreconciled"),
        (Health(depeg=0.006), Mode.REDUCE_ONLY, "depeg"),
        (Health(depeg=0.021), Mode.FLATTEN, "depeg_flatten"),
        (Health(margin_ratio=0.31), Mode.REDUCE_ONLY, "margin_ratio"),
    ],
)
def test_health_rules(health: Health, expected: Mode, trigger: str | None) -> None:
    mode, triggers = _mode(health=health)
    assert mode is expected
    if trigger:
        assert trigger in triggers


@pytest.mark.parametrize(
    ("errors", "rejects", "expected"), [(4, 2, Mode.NORMAL), (5, 0, Mode.HALT), (0, 3, Mode.HALT)]
)
def test_api_error_limits(errors: int, rejects: int, expected: Mode) -> None:
    assert _mode(api_errors=errors, order_rejects=rejects)[0] is expected


def test_most_severe_mode_wins_and_all_triggers_are_reported() -> None:
    mode, triggers = _mode(
        990.0, health=Health(reconciled=False, book_age_s=6.0), locked=True, lock_reason="kill"
    )
    assert mode is Mode.FLATTEN  # reduce-only flatten orders are safe even when local state is suspect
    assert {"unreconciled", "stale_book", "max_daily_loss"} <= set(triggers)


def _ev(
    targets: list[Target],
    current: dict[str, float],
    health: Health = HEALTHY,
    equity: float = 1000.0,
    **led: object,
):  # type: ignore[no-untyped-def]
    return evaluate(targets, GateContext(T, equity, current), health, _ledger(**led), LIM)


def test_flatten_zeros_everything_and_cancels() -> None:
    r = _ev([Target(BTC, 0.2), Target(ETH, -0.1)], {BTC.key: 0.1}, locked=True, lock_reason="kill")
    assert [d.target.weight for d in r.decisions] == [0.0, 0.0]
    assert all(d.verdict is Verdict.FLATTEN for d in r.decisions)
    assert r.cancel_all and r.submit_orders


def test_halt_holds_current_positions_and_sends_nothing() -> None:
    r = _ev(
        [Target(BTC, 0.2), Target(ETH, 0.0)], {BTC.key: 0.1, ETH.key: -0.05}, health=Health(reconciled=False)
    )
    assert [d.target.weight for d in r.decisions] == [0.1, -0.05]
    assert all(d.verdict is Verdict.HOLD for d in r.decisions)
    assert not r.submit_orders and r.cancel_all


def test_reduce_only_never_adds_or_flips() -> None:
    r = _ev(
        [Target(BTC, 0.2), Target(ETH, 0.1), Target(SPOT, 0.05)],
        {BTC.key: 0.1, ETH.key: -0.1, SPOT.key: 0.1},
        equity=990.0,
    )
    assert r.mode is Mode.REDUCE_ONLY
    assert [d.target.weight for d in r.decisions] == pytest.approx([0.1, 0.0, 0.05])
    assert [d.verdict for d in r.decisions] == [Verdict.REDUCE, Verdict.REDUCE, Verdict.ACCEPT]


def test_reduce_only_scales_hedge_groups_together() -> None:
    r = _ev(
        [Target(SPOT, 0.2, group="c"), Target(BTC, -0.2, group="c")],
        {SPOT.key: 0.1, BTC.key: -0.05},
        equity=990.0,
    )
    assert [d.target.weight for d in r.decisions] == pytest.approx([0.05, -0.05])


def test_margin_breach_cuts_perp_exposure_including_its_hedge() -> None:
    r = _ev(
        [Target(SPOT, 0.2, group="c"), Target(BTC, -0.2, group="c"), Target(ETH, 0.0)],
        {SPOT.key: 0.2, BTC.key: -0.2},
        health=Health(margin_ratio=0.35),
    )
    assert [d.target.weight for d in r.decisions] == pytest.approx([0.15, -0.15, 0.0])


def test_unhedged_group_is_flattened_alone() -> None:
    r = _ev(
        [Target(SPOT, 0.2, group="c"), Target(BTC, -0.2, group="c"), Target(ETH, 0.1)],
        {SPOT.key: 0.2, BTC.key: -0.1},
        health=Health(unhedged_groups=frozenset({"c"})),
    )
    assert [d.target.weight for d in r.decisions] == pytest.approx([0.0, 0.0, 0.1])
    assert r.mode is Mode.NORMAL and "unhedged:c" in r.triggers


def test_normal_mode_still_applies_exposure_limits() -> None:
    r = _ev([Target(BTC, 0.6), Target(SPOT, -0.1)], {})
    assert [d.target.weight for d in r.decisions] == pytest.approx([0.25, 0.0])
