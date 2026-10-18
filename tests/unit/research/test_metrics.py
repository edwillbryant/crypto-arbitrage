"""Metrics against values computed by hand."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from agent.research.metrics import episode_stats, max_drawdown, returns_from_equity, sharpe


def test_returns_and_drawdown_by_hand() -> None:
    eq = pd.Series([100.0, 110.0, 99.0, 120.0, 108.0])
    r = returns_from_equity(eq)
    assert list(r.round(10)) == [0.1, -0.1, round(120 / 99 - 1, 10), -0.1]
    # peak 110 -> trough 99 is 10%; peak 120 -> 108 is also 10%
    assert max_drawdown(eq) == pytest.approx(0.1)
    assert max_drawdown(pd.Series([100.0, 50.0, 200.0, 100.0])) == pytest.approx(0.5)
    assert max_drawdown(pd.Series([1.0, 2.0, 3.0])) == 0.0


def test_sharpe_by_hand() -> None:
    r = pd.Series([0.01, -0.02, 0.03, 0.0])
    mean = 0.005
    sd = math.sqrt(((0.005) ** 2 + (0.025) ** 2 + (0.025) ** 2 + (0.005) ** 2) / 3)
    assert sharpe(r, periods_per_year=365) == pytest.approx(mean / sd * math.sqrt(365))
    assert math.isnan(sharpe(pd.Series([0.01, 0.01, 0.01]), 365))
    assert math.isnan(sharpe(pd.Series([0.01]), 365))


def test_episode_stats_by_hand() -> None:
    ep = pd.DataFrame({"pnl": [10.0, -5.0, 20.0, -5.0, 3.0], "is_open": [False, False, False, False, True]})
    s = episode_stats(ep)
    assert s["trades"] == 4  # the open episode is not a completed trade
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["profit_factor"] == pytest.approx(30.0 / 10.0)


def test_episode_stats_edge_cases() -> None:
    assert episode_stats(pd.DataFrame({"pnl": [], "is_open": []}))["trades"] == 0
    all_wins = episode_stats(pd.DataFrame({"pnl": [1.0, 2.0], "is_open": [False, False]}))
    assert all_wins["profit_factor"] == math.inf
