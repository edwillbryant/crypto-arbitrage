"""Deflated Sharpe Ratio (Bailey & López de Prado, 2014)."""

from __future__ import annotations

import math

import pytest

from agent.research.dsr import deflated_sharpe, expected_max_sharpe, probabilistic_sharpe


def test_paper_worked_example() -> None:
    """Daily strategy, annualised SR 2.5, N=100 trials, V[SR]=0.5 annualised, T=1250, skew -3, kurt 10.

    Reference values: SR0 = 0.1132 per day, DSR = 0.9004 (the paper reports ~90%). Annualisation uses
    250 days, which reproduces both published figures.
    """
    ppy = 250
    sr = 2.5 / math.sqrt(ppy)
    sr0 = expected_max_sharpe(n_trials=100, var_sr=0.5 / ppy)
    assert sr0 == pytest.approx(0.1132, abs=5e-5)
    dsr = deflated_sharpe(sr, n_obs=1250, skew=-3.0, kurt=10.0, n_trials=100, var_sr=0.5 / ppy)
    assert dsr == pytest.approx(0.9004, abs=5e-5)


def test_psr_is_half_at_the_benchmark() -> None:
    assert probabilistic_sharpe(0.1, 0.1, n_obs=500, skew=0.0, kurt=3.0) == pytest.approx(0.5)


def test_psr_increases_with_sample_and_penalises_fat_tails() -> None:
    a = probabilistic_sharpe(0.05, 0.0, n_obs=250, skew=0.0, kurt=3.0)
    b = probabilistic_sharpe(0.05, 0.0, n_obs=2500, skew=0.0, kurt=3.0)
    c = probabilistic_sharpe(0.05, 0.0, n_obs=2500, skew=-2.0, kurt=15.0)
    assert a < b
    assert c < b


def test_one_trial_has_no_selection_penalty() -> None:
    assert expected_max_sharpe(n_trials=1, var_sr=0.01) == 0.0


def test_more_trials_raise_the_bar() -> None:
    assert expected_max_sharpe(1000, 0.01) > expected_max_sharpe(10, 0.01) > 0


def test_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        expected_max_sharpe(0, 0.01)
    with pytest.raises(ValueError):
        probabilistic_sharpe(0.1, 0.0, n_obs=1, skew=0.0, kurt=3.0)
