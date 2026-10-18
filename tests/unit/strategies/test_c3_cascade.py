from __future__ import annotations

import numpy as np
import pandas as pd

from agent.strategies.c3_cascade import Cascade, CascadeParams
from tests.unit.strategies.helpers import FUNDING, H1_PERP, METRICS, bars, funding, metrics, snapshot

T = pd.Timestamp("2024-03-10 12:00", tz="UTC")
N = 200
rng = np.random.default_rng(1)
CALM = list(100 * np.exp(np.cumsum(rng.normal(0, 0.002, N))))
OI = [1000.0] * N


def _run(closes: list[float], oi: list[float], rate: float = 0.0001, **kw: object) -> float:
    s = snapshot(T, {
        (H1_PERP, "BTCUSDT"): bars(H1_PERP, T, closes),
        (METRICS, "BTCUSDT"): metrics(T, oi),
        (FUNDING, "BTCUSDT"): funding(T - pd.Timedelta(hours=4), [rate] * 3),
    })  # fmt: skip
    params = CascadeParams(**{"oi_drop": 0.05, "z": 3.0, "window_h": 4, "max_hold_h": 24, **kw})  # type: ignore[arg-type]
    return Cascade(params, symbols=("BTCUSDT",)).decide(s)[0].weight


def _crash(prices: list[float], drop: float = 0.06) -> list[float]:
    """Last 4 hours fall `drop` in total."""
    p = list(prices)
    base = p[-5]
    for i, f in enumerate([0.25, 0.5, 0.75, 1.0]):
        p[-4 + i] = base * (1 - drop * f)
    return p


def _oi_flush(oi: list[float], drop: float = 0.10) -> list[float]:
    o = list(oi)
    o[-4:] = [o[-5] * (1 - drop)] * 4
    return o


def test_price_crash_with_oi_flush_buys() -> None:
    assert _run(_crash(CALM), _oi_flush(OI)) == 1.0


def test_crash_without_oi_flush_is_not_a_cascade() -> None:
    assert _run(_crash(CALM), OI) == 0.0


def test_oi_flush_without_a_price_move_is_ignored() -> None:
    assert _run(CALM, _oi_flush(OI)) == 0.0


def test_deeply_negative_funding_blocks_entry() -> None:
    assert _run(_crash(CALM), _oi_flush(OI), rate=-0.001) == 0.0


def test_exits_on_reversion_target() -> None:
    p = _crash(CALM[:-3]) + [0.0] * 3
    p[-3:] = [p[-4] * 1.01, p[-4] * 1.02, p[-5] * 1.0]  # recovers most of the drop
    o = _oi_flush(OI[:-3]) + [900.0] * 3
    assert _run(p, o) == 0.0


def test_time_stop() -> None:
    crash = _crash(CALM[:-30])
    p = crash + [crash[-1]] * 30  # no recovery, no further fall
    o = _oi_flush(OI[:-30]) + [900.0] * 30
    assert _run(p, o, max_hold_h=24) == 0.0
    assert _run(p, o, max_hold_h=48) == 1.0


def test_hard_stop() -> None:
    crash = _crash(CALM[:-3])
    p = [*crash, crash[-1] * 0.95, crash[-1] * 0.9, crash[-1] * 0.85]  # keeps falling
    o = _oi_flush(OI[:-3]) + [880.0] * 3
    assert _run(p, o) == 0.0


def test_missing_oi_data_means_flat() -> None:
    s = snapshot(T, {
        (H1_PERP, "BTCUSDT"): bars(H1_PERP, T, _crash(CALM)),
        (METRICS, "BTCUSDT"): metrics(T, OI).iloc[0:0],
        (FUNDING, "BTCUSDT"): funding(T - pd.Timedelta(hours=4), [0.0001] * 3),
    })  # fmt: skip
    assert Cascade(CascadeParams(), symbols=("BTCUSDT",)).decide(s)[0].weight == 0.0
