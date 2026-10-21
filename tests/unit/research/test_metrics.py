"""Metrics against values computed by hand."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from agent.research.metrics import episode_stats, max_drawdown, returns_from_equity, sharpe, summarize


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


def test_summarize_reports_every_spec_metric() -> None:
    from agent.research.engine import BacktestConfig, BacktestResult  # noqa: PLC0415

    t = pd.date_range("2024-01-01", periods=4, freq="1D", tz="UTC")
    eq = pd.Series([100.0, 110.0, 99.0, 120.0], index=t)
    fills = pd.DataFrame(
        {"time": t[:2], "instrument": ["perp:BTCUSDT"] * 2, "qty": [1.0, -1.0], "price": [100.0, 110.0],
         "reference_price": [100.0, 110.0], "fee": [0.5, 0.5], "slippage_cost": [0.25, 0.25]}
    )  # fmt: skip
    res = BacktestResult(
        strategy="x",
        config=BacktestConfig(start=t[0], end=t[-1]),
        equity=eq,
        gross_exposure=pd.Series([0.0, 1.0, 1.0, 0.0], index=t),
        fills=fills,
        funding=pd.DataFrame({"amount": [-0.1]}),
        episodes=pd.DataFrame({"pnl": [10.0, -2.0], "is_open": [False, False]}),
        decisions=pd.DataFrame(),
    )
    s = summarize(res, pd.Timedelta(days=1))
    for key in ("sharpe", "max_drawdown", "win_rate", "profit_factor", "trades", "sr_per_period", "n_obs",
                "skew", "kurt", "costs_over_gross", "turnover_per_year"):  # fmt: skip
        assert key in s
    assert s["max_drawdown"] == pytest.approx(0.1)
    assert s["trades"] == 2 and s["profit_factor"] == pytest.approx(5.0)
    assert s["fees"] == pytest.approx(1.0) and s["slippage"] == pytest.approx(0.5)
    assert s["net_pnl"] == pytest.approx(20.0)
    assert s["costs_over_gross"] == pytest.approx(1.5 / 21.5)
    assert s["exposure_time"] == pytest.approx(0.5)
    assert s["sr_per_period"] * math.sqrt(365) == pytest.approx(s["sharpe"])


def test_daily_metrics_resample_to_utc_day_closes() -> None:
    from agent.research.metrics import daily_returns  # noqa: PLC0415

    t = pd.date_range("2024-01-01", periods=72, freq="1h", tz="UTC")
    eq = pd.Series(100.0, index=t)
    eq[t >= pd.Timestamp("2024-01-02", tz="UTC")] = 110.0
    eq[t >= pd.Timestamp("2024-01-03", tz="UTC")] = 99.0
    r = daily_returns(eq)
    assert list(r.round(10)) == [0.1, -0.1]  # day closes 100 -> 110 -> 99
