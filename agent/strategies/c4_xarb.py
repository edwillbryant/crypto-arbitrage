"""C4: cross-exchange spot arbitrage, as an ANALYZER only. Never wired to the engine or an exchange.

Edge claimed: price gaps between venues. Expected verdict: dead for a retail-latency bot. Faster firms
close gaps in milliseconds, and moving inventory between venues takes minutes to hours and costs fees.
This counts how often bar-close gaps would have beaten all-in costs. Bar closes on two venues aren't
synchronous and transfers aren't instant, so the count is an UPPER BOUND on real opportunity.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

CAVEAT = ("upper bound: non-synchronous bar closes overstate gaps; ignores transfer latency, withdrawal "
          "limits, inventory risk and queue position")  # fmt: skip


def analyze(
    a: pd.Series, b: pd.Series, *, fee_bps: float, slippage_bps: float, transfer_bps: float
) -> dict[str, Any]:
    """`a`, `b`: closes indexed by time on two venues. Costs: per leg (fee, slippage) plus transfer."""
    joined = pd.concat({"a": a, "b": b}, axis=1, join="inner").dropna()
    gap_bps = (joined["a"] / joined["b"] - 1) * 1e4
    hurdle = 2 * (fee_bps + slippage_bps) + transfer_bps
    hits = gap_bps.abs() > hurdle
    net = gap_bps.abs()[hits] - hurdle
    return {
        "observations": len(joined),
        "opportunities": int(hits.sum()),
        "fraction": float(hits.mean()) if len(joined) else float("nan"),
        "hurdle_bps": hurdle,
        "mean_net_edge_bps": float(net.mean()) if len(net) else float("nan"),
        "caveat": CAVEAT,
    }
