from __future__ import annotations

import numpy as np
import pandas as pd

from agent.strategies.c3_cascade import Cascade, CascadeParams
from tests.unit.strategies.helpers import FUNDING, H1_PERP, METRICS, bars, funding, metrics, snapshot

T = pd.Timestamp("2024-03-10 12:00", tz="UTC")
rng = np.random.default_rng(1)
CALM = list(100 * np.exp(np.cumsum(rng.normal(0, 0.002, 195))))
BASE = CALM[-1]
CRASH = [BASE * (1 - 0.06 * f) for f in (0.25, 0.5, 0.75, 1.0)]  # -6% over 4 hours
BOTTOM = CRASH[-1]
PAUSE = BOTTOM * 1.002  # first bar closing above the previous one: entry at ~0.94 x BASE
# Entry at PAUSE; drop = BASE - PAUSE; target = PAUSE + 0.5 drop (~0.97 BASE); stop = PAUSE - 0.5 drop (~0.91)


def _prices(*after: float, crash: bool = True) -> list[float]:
    return CALM + (CRASH if crash else [BASE] * 4) + [PAUSE if crash else BASE, *after]


def _oi(n: int, flush: bool = True) -> list[float]:
    before = [1000.0] * (len(CALM) + 1)  # OI at the close of each calm bar and the last pre-crash one
    return (before + [900.0] * (n - len(before)))[:n] if flush else [1000.0] * n


def _run(prices: list[float], *, flush: bool = True, rate: float = 0.0001, **kw: object) -> float:
    s = snapshot(T, {
        (H1_PERP, "BTCUSDT"): bars(H1_PERP, T, prices),
        (METRICS, "BTCUSDT"): metrics(T, _oi(len(prices), flush)),
        (FUNDING, "BTCUSDT"): funding(T - pd.Timedelta(hours=4), [rate] * 30),  # 10 days of 8h prints
    })  # fmt: skip
    params = CascadeParams(**{"oi_drop": 0.05, "z": 3.0, "window_h": 4, "max_hold_h": 24, **kw})  # type: ignore[arg-type]
    return Cascade(params, symbols=("BTCUSDT",)).decide(s)[0].weight


def test_buys_on_the_first_up_bar_after_a_cascade() -> None:
    assert _run(_prices()) == 1.0


def test_does_not_catch_the_falling_knife() -> None:
    assert _run(CALM + CRASH) == 0.0  # still falling: no confirmation yet


def test_crash_without_oi_flush_is_not_a_cascade() -> None:
    assert _run(_prices(), flush=False) == 0.0


def test_oi_flush_without_a_price_move_is_ignored() -> None:
    assert _run(_prices(crash=False)) == 0.0


def test_deeply_negative_funding_blocks_entry() -> None:
    assert _run(_prices(), rate=-0.001) == 0.0


def test_exits_on_reversion_target() -> None:
    assert _run(_prices(BASE * 0.98)) == 0.0
    assert _run(_prices(BASE * 0.96)) == 1.0  # between stop and target: still holding


def test_time_stop() -> None:
    p = _prices(*[PAUSE] * 30)  # no recovery, no further fall
    assert _run(p, max_hold_h=24) == 0.0
    assert _run(p, max_hold_h=48) == 1.0


def test_hard_stop_scaled_to_the_move() -> None:
    assert _run(_prices(BASE * 0.92)) == 1.0  # above the ~0.91 stop
    assert _run(_prices(BASE * 0.90)) == 0.0


def test_missing_oi_data_means_flat() -> None:
    s = snapshot(T, {
        (H1_PERP, "BTCUSDT"): bars(H1_PERP, T, _prices()),
        (METRICS, "BTCUSDT"): metrics(T, _oi(len(_prices()))).iloc[0:0],
        (FUNDING, "BTCUSDT"): funding(T - pd.Timedelta(hours=4), [0.0001] * 30),
    })  # fmt: skip
    assert Cascade(CascadeParams(), symbols=("BTCUSDT",)).decide(s)[0].weight == 0.0


def test_asof_matches_pandas_merge_asof() -> None:
    from agent.strategies.c3_cascade import _asof  # noqa: PLC0415

    src = metrics(T, list(np.arange(50.0)))
    times = pd.Series(pd.date_range(T - pd.Timedelta(hours=60), T, freq="37min", tz="UTC")).astype(
        src["key_time"].dtype
    )
    ours = _asof(times, src, "sum_open_interest")
    ref = pd.merge_asof(pd.DataFrame({"t": times}), src.rename(columns={"key_time": "t"}), on="t")[
        "sum_open_interest"
    ]
    np.testing.assert_array_equal(ours, ref.to_numpy("float64"))
    assert np.isnan(ours[0])  # before the first observation
