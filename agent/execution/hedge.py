"""Hedge-group delta monitor: a carry pair whose legs drift apart is no longer delta-neutral."""

from __future__ import annotations

import pandas as pd


class HedgeMonitor:
    def __init__(
        self, *, max_net_delta: float = 0.02, grace: pd.Timedelta = pd.Timedelta(seconds=30)
    ) -> None:
        self.max_net_delta, self.grace = max_net_delta, grace
        self._since: dict[str, pd.Timestamp] = {}

    def update(
        self,
        now: pd.Timestamp,
        positions: dict[str, float],
        marks: dict[str, float],
        groups: dict[str, list[str]],
    ) -> frozenset[str]:
        """Groups whose |net| / gross notional has exceeded `max_net_delta` for longer than `grace`."""
        unhedged = set()
        for g, keys in groups.items():
            notionals = [positions.get(k, 0.0) * marks.get(k, 0.0) for k in keys]
            gross = sum(abs(n) for n in notionals)
            breach = gross > 0 and abs(sum(notionals)) / gross > self.max_net_delta
            if not breach:
                self._since.pop(g, None)
                continue
            first = self._since.setdefault(g, now)
            if now - first > self.grace:
                unhedged.add(g)
        return frozenset(unhedged)
