"""Exposure subset of the RiskGate needed for backtests. The full gate is built test-first in M5."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agent.core.types import Instrument, Target
from agent.risk.exposure import ExposureGate, Verdict

BTC = Instrument("BTCUSDT", "perp")
ETH = Instrument("ETHUSDT", "perp")
SPOT = Instrument("BTCUSDT", "spot")
ESPOT = Instrument("ETHUSDT", "spot")


def test_within_limits_accepted() -> None:
    d = ExposureGate(max_weight=0.25, max_gross=1.0).check([Target(BTC, 0.2)])
    assert d[0].verdict is Verdict.ACCEPT and d[0].target.weight == 0.2


def test_per_instrument_cap_clips() -> None:
    d = ExposureGate(max_weight=0.25, max_gross=1.0).check([Target(BTC, -0.6)])
    assert d[0].verdict is Verdict.CLIP and d[0].target.weight == pytest.approx(-0.25)


def test_spot_short_rejected() -> None:
    d = ExposureGate().check([Target(SPOT, -0.1)])
    assert d[0].verdict is Verdict.REJECT and d[0].target.weight == 0.0


def test_groups_are_scaled_together() -> None:
    """Clipping one leg of a hedge would create naked exposure; both legs scale by the same factor."""
    d = ExposureGate(max_weight=0.25, max_gross=1.0).check(
        [Target(SPOT, 0.5, group="g"), Target(BTC, -0.4, group="g")]
    )
    assert [x.target.weight for x in d] == pytest.approx([0.25, -0.2])


def test_rejected_leg_rejects_whole_group() -> None:
    d = ExposureGate().check([Target(SPOT, -0.2, group="g"), Target(BTC, 0.2, group="g")])
    assert all(x.verdict is Verdict.REJECT and x.target.weight == 0.0 for x in d)


@given(
    st.lists(
        st.tuples(
            st.sampled_from([BTC, ETH, SPOT, ESPOT]),
            st.floats(-3, 3, allow_nan=False),
            st.sampled_from([None, "g"]),
        ),
        min_size=1,
        max_size=6,
        unique_by=lambda t: t[0],
    )
)
def test_limits_hold_for_any_input(raw: list[tuple[Instrument, float, str | None]]) -> None:
    gate = ExposureGate(max_weight=0.25, max_gross=1.0)
    out = [d.target for d in gate.check([Target(i, w, group=g) for i, w, g in raw])]
    assert all(abs(t.weight) <= 0.25 + 1e-12 for t in out)
    assert sum(abs(t.weight) for t in out) <= 1.0 + 1e-12
    assert all(t.weight >= 0 for t in out if t.instrument.market == "spot")
    for t_in, t_out in zip(raw, out, strict=True):
        assert abs(t_out.weight) <= abs(t_in[1]) + 1e-12  # the gate never increases risk


def test_gross_cap_scales_everything_proportionally() -> None:
    """With more instruments than 1/max_weight, the gross cap binds; hedge legs stay balanced."""
    gate = ExposureGate(max_weight=0.25, max_gross=0.3)
    d = gate.check([Target(SPOT, 0.2, group="g"), Target(BTC, -0.2, group="g"), Target(ETH, 0.2)])
    out = [x.target.weight for x in d]
    assert sum(abs(w) for w in out) == pytest.approx(0.3)
    assert out == pytest.approx([0.1, -0.1, 0.1])
    assert all(x.verdict is Verdict.CLIP and x.rule == "max_gross" for x in d)


def test_gross_cap_leaves_flat_targets_flat() -> None:
    d = ExposureGate(max_weight=0.25, max_gross=0.3).check(
        [Target(BTC, 0.25), Target(ETH, 0.25), Target(SPOT, 0.0)]
    )
    assert d[2].target.weight == 0.0 and d[2].verdict is Verdict.ACCEPT
