"""Properties that must hold for ANY input. These are the guarantees the live engine relies on."""

from __future__ import annotations

import math

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.core.types import Instrument, Target
from agent.risk.gate import GateContext, Health, LedgerState, Mode, evaluate
from agent.risk.limits import Limits

T = pd.Timestamp("2024-03-01 10:00", tz="UTC")
INSTRUMENTS = [Instrument("BTCUSDT", "perp"), Instrument("ETHUSDT", "perp"),
               Instrument("BTCUSDT", "spot"), Instrument("ETHUSDT", "spot")]  # fmt: skip
weights = st.floats(-2, 2, allow_nan=False, allow_infinity=False)

health_st = st.builds(
    Health,
    book_age_s=st.floats(0, 120),
    bars_late=st.integers(0, 3),
    clock_drift_ms=st.floats(0, 1000),
    reconciled=st.booleans(),
    depeg=st.floats(0, 0.05),
    margin_ratio=st.none() | st.floats(0, 1),
    unhedged_groups=st.frozensets(st.sampled_from(["g", "h"])),
)
ledger_st = st.builds(
    LedgerState,
    peak_equity=st.floats(1000, 1500),
    day_start_equity=st.floats(900, 1100),
    locked=st.booleans(),
    lock_reason=st.just("x"),
    api_errors=st.integers(0, 8),
    order_rejects=st.integers(0, 5),
)


@st.composite
def books(draw: st.DrawFn) -> tuple[list[Target], dict[str, float]]:
    chosen = draw(st.lists(st.sampled_from(INSTRUMENTS), min_size=1, max_size=4, unique=True))
    targets = [Target(i, draw(weights), group=draw(st.sampled_from([None, "g", "h"]))) for i in chosen]
    current = {i.key: draw(weights) for i in chosen if draw(st.booleans())}
    return targets, current


@settings(max_examples=400)
@given(book=books(), health=health_st, ledger=ledger_st, equity=st.floats(800, 1500))
def test_gate_invariants(
    book: tuple[list[Target], dict[str, float]], health: Health, ledger: LedgerState, equity: float
) -> None:
    targets, current = book
    lim = Limits()
    r = evaluate(targets, GateContext(T, equity, current), health, ledger, lim)
    out = [d.target.weight for d in r.decisions]
    assert len(out) == len(targets)
    assert [d.target.instrument for d in r.decisions] == [t.instrument for t in targets]

    if r.mode is Mode.HALT:
        assert not r.submit_orders
        assert out == [current.get(t.instrument.key, 0.0) for t in targets]
        return

    assert r.submit_orders
    eps = 1e-9
    assert all(abs(w) <= lim.max_weight + eps for w in out)
    assert sum(abs(w) for w in out) <= lim.max_gross + eps
    assert all(w >= -eps for w, t in zip(out, targets, strict=True) if t.instrument.market == "spot")
    for w, t in zip(out, targets, strict=True):
        assert abs(w) <= abs(t.weight) + eps, "the gate must never add risk the strategy did not ask for"

    if r.mode is Mode.FLATTEN:
        assert all(w == 0.0 for w in out) and r.cancel_all
    if r.mode is Mode.REDUCE_ONLY:
        for w, t in zip(out, targets, strict=True):
            cur = current.get(t.instrument.key, 0.0)
            assert abs(w) <= abs(cur) + eps
            assert w == 0.0 or math.copysign(1, w) == math.copysign(1, cur)

    # hedge-group legs that survive keep their relative proportions
    for g in ("g", "h"):
        legs = [(w, t.weight) for w, t in zip(out, targets, strict=True) if t.group == g and t.weight != 0]
        ratios = [w / tw for w, tw in legs]
        if ratios:
            assert max(ratios) - min(ratios) <= 1e-9
