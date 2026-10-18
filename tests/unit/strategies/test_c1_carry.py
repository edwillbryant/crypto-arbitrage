from __future__ import annotations

import pandas as pd
import pytest

from agent.strategies.c1_carry import CarryParams, FundingCarry, annualized, hysteresis
from tests.unit.strategies.helpers import FUNDING, H1_PERP, H1_SPOT, bars, funding, snapshot

T = pd.Timestamp("2024-03-01 09:00", tz="UTC")
HIGH = 0.0003  # per 8h -> 32.9% APR
MID = 0.0001  # 10.95% APR
LOW = 0.00002  # 2.2% APR


def _snap(rates: list[float], spot: float = 100.0, perp: float = 100.05, interval_hours: int = 8):  # type: ignore[no-untyped-def]
    return snapshot(T, {
        (FUNDING, "BTCUSDT"): funding(T - pd.Timedelta(hours=1), rates, interval_hours),
        (H1_SPOT, "BTCUSDT"): bars(H1_SPOT, T, [spot] * 3),
        (H1_PERP, "BTCUSDT"): bars(H1_PERP, T, [perp] * 3),
    })  # fmt: skip


def _carry(**kw: float) -> FundingCarry:
    return FundingCarry(CarryParams(**kw), symbols=("BTCUSDT",))


def test_annualisation_uses_the_funding_interval() -> None:
    assert annualized(pd.Series([0.0001]), pd.Series([8])).iloc[0] == pytest.approx(0.0001 * 3 * 365)
    assert annualized(pd.Series([0.0001]), pd.Series([4])).iloc[0] == pytest.approx(0.0001 * 6 * 365)


def test_hysteresis_by_hand() -> None:
    assert hysteresis(pd.Series([0.1, 0.2, 0.12, 0.04, 0.12, float("nan")]), entry=0.15, exit=0.05) == [
        False, True, True, False, False, False]  # fmt: skip


def test_high_funding_enters_a_hedged_pair() -> None:
    out = _carry().decide(_snap([HIGH] * 5))
    by = {t.instrument.key: t for t in out}
    assert by["spot:BTCUSDT"].weight == 0.25 and by["perp:BTCUSDT"].weight == -0.25
    assert by["spot:BTCUSDT"].group == by["perp:BTCUSDT"].group == "carry:BTCUSDT"


def test_stays_in_between_exit_and_entry_then_leaves_below_exit() -> None:
    held = _carry().decide(_snap([HIGH] * 4 + [MID] * 3))
    assert {t.weight for t in held} == {0.25, -0.25}
    gone = _carry().decide(_snap([HIGH] * 4 + [LOW] * 3))
    assert {t.weight for t in gone} == {0.0}


def test_mid_funding_from_flat_does_not_enter() -> None:
    assert {t.weight for t in _carry().decide(_snap([MID] * 6))} == {0.0}


def test_negative_funding_never_reverses_the_trade() -> None:
    """Reverse carry needs a spot short, which v1 does not do."""
    assert {t.weight for t in _carry().decide(_snap([-HIGH] * 6))} == {0.0}


def test_basis_guard_unwinds() -> None:
    assert {t.weight for t in _carry(max_basis=0.01).decide(_snap([HIGH] * 5, spot=100.0, perp=101.5))} == {
        0.0
    }


def test_missing_or_stale_prices_mean_flat() -> None:
    s = _snap([HIGH] * 5)
    stale = dict(s.data)
    stale[(H1_SPOT.name, "BTCUSDT")] = bars(H1_SPOT, T - pd.Timedelta(hours=3), [100.0] * 3)
    assert {t.weight for t in _carry().decide(type(s)(T, stale))} == {0.0}
    assert {t.weight for t in _carry(n_periods=9).decide(_snap([HIGH] * 5))} == {0.0}  # too little history


def test_invalid_params_rejected() -> None:
    with pytest.raises(ValueError):
        CarryParams(entry_apr=0.05, exit_apr=0.10)
