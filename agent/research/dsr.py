"""Probabilistic and Deflated Sharpe Ratios (Bailey & López de Prado, 2012 / 2014).

All Sharpe ratios here are per period (not annualised). `kurt` is non-excess kurtosis (normal = 3).
The DSR asks: given N trials whose Sharpe ratios have variance V, how likely is it that the selected
strategy's true SR is above the best SR we'd expect from N skill-less trials?
"""

from __future__ import annotations

import math
from statistics import NormalDist

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def probabilistic_sharpe(sr: float, sr_benchmark: float, *, n_obs: int, skew: float, kurt: float) -> float:
    if n_obs < 2:
        raise ValueError("need at least 2 observations")
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denom <= 0:
        raise ValueError("non-positive variance term; check skew/kurtosis inputs")
    return _N.cdf((sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom))


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """Expected maximum SR across `n_trials` independent skill-less trials with SR variance `var_sr`."""
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if n_trials == 1:
        return 0.0
    z = _N.inv_cdf
    return math.sqrt(var_sr) * (
        (1 - EULER_GAMMA) * z(1 - 1 / n_trials) + EULER_GAMMA * z(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(
    sr: float, *, n_obs: int, skew: float, kurt: float, n_trials: int, var_sr: float
) -> float:
    return probabilistic_sharpe(sr, expected_max_sharpe(n_trials, var_sr), n_obs=n_obs, skew=skew, kurt=kurt)


def deflated_sharpe_from_trials(
    sr: float, n_obs: int, skew: float, kurt: float, trial_srs: list[float]
) -> float:
    """DSR using every recorded trial's per-period SR. Counting correlated variants as independent
    overstates N, which makes the test stricter, never looser."""
    finite = [s for s in trial_srs if math.isfinite(s)]
    if len(finite) < 2:
        return probabilistic_sharpe(sr, 0.0, n_obs=n_obs, skew=skew, kurt=kurt)
    mean = sum(finite) / len(finite)
    var = sum((s - mean) ** 2 for s in finite) / (len(finite) - 1)
    return deflated_sharpe(sr, n_obs=n_obs, skew=skew, kurt=kurt, n_trials=len(finite), var_sr=var)
