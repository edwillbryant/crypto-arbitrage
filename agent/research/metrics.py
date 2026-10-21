"""Performance metrics. All returns are per decision interval; annualisation assumes 365-day trading."""

from __future__ import annotations

import math
from typing import Any, cast

import pandas as pd

from agent.research.engine import BacktestResult
from agent.sizing.voltarget import YEAR


def returns_from_equity(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna().reset_index(drop=True)


def daily_returns(equity: pd.Series) -> pd.Series:
    """Returns between UTC day closes (last equity value of each day)."""
    closes = equity.resample("1D").last().dropna()
    return closes.pct_change().dropna().reset_index(drop=True)


def sharpe(returns: pd.Series, periods_per_year: float) -> float:
    if len(returns) < 2:
        return math.nan
    sd = float(returns.std(ddof=1))
    if not sd > 1e-15:
        return math.nan
    return float(returns.mean()) / sd * math.sqrt(periods_per_year)


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough loss as a positive fraction."""
    return float((1 - equity / equity.cummax()).max())


def episode_stats(episodes: pd.DataFrame) -> dict[str, float]:
    closed = episodes.loc[~episodes["is_open"].astype(bool), "pnl"].astype("float64")
    n = len(closed)
    if n == 0:
        return {"trades": 0, "win_rate": math.nan, "profit_factor": math.nan}
    wins, losses = closed[closed > 0].sum(), -closed[closed < 0].sum()
    pf = math.inf if losses == 0 and wins > 0 else (float(wins / losses) if losses > 0 else math.nan)
    return {"trades": n, "win_rate": float((closed > 0).mean()), "profit_factor": pf}


def summarize(result: BacktestResult, decision_interval: pd.Timedelta) -> dict[str, Any]:
    """Every metric the spec requires, plus what the DSR needs (per-period SR, n_obs, skew, kurtosis)."""
    eq = result.equity
    r = returns_from_equity(eq)
    ppy = YEAR / decision_interval
    years = (eq.index[-1] - eq.index[0]) / YEAR if len(eq) > 1 else math.nan
    fees = float(result.fills["fee"].sum())
    slippage = float(result.fills["slippage_cost"].sum())
    funding = float(result.funding["amount"].sum())
    net = float(eq.iloc[-1] - eq.iloc[0])
    gross = net + fees + slippage
    traded = float((result.fills["qty"].abs() * result.fills["reference_price"]).sum())
    sr_pp = sharpe(r, 1.0)
    return {
        "sharpe": sharpe(r, ppy),
        "max_drawdown": max_drawdown(eq),
        "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1),
        "cagr": float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1)
        if years and years > 0 and eq.iloc[-1] > 0
        else math.nan,
        "ann_vol": float(r.std(ddof=1) * math.sqrt(ppy)) if len(r) > 1 else math.nan,
        **episode_stats(result.episodes),
        "open_episodes": int(result.episodes["is_open"].astype(bool).sum()),
        "exposure_time": float((result.gross_exposure > 0).mean()),
        "avg_gross_exposure": float(result.gross_exposure.mean()),
        "turnover_per_year": traded / float(eq.mean()) / years if years and years > 0 else math.nan,
        "fees": fees,
        "slippage": slippage,
        "funding": funding,
        "net_pnl": net,
        "costs_over_gross": (fees + slippage) / gross if gross > 0 else math.nan,
        "n_obs": len(r),
        "sr_per_period": sr_pp,
        "skew": cast(float, r.skew()) if len(r) > 2 else math.nan,
        "kurt": cast(float, r.kurt()) + 3 if len(r) > 3 else math.nan,  # non-excess, as the DSR expects
        "events": len(result.events),
        **_daily(eq),
    }


def _daily(eq: pd.Series) -> dict[str, Any]:
    """Daily-return statistics: the basis for the M4 deflated Sharpe (hourly returns are too lumpy)."""
    d = daily_returns(eq)
    return {
        "sharpe_daily": sharpe(d, 365.0),
        "sr_daily": sharpe(d, 1.0),
        "n_days": len(d),
        "skew_daily": cast(float, d.skew()) if len(d) > 2 else math.nan,
        "kurt_daily": cast(float, d.kurt()) + 3 if len(d) > 3 else math.nan,
    }
