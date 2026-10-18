"""Exposure limits: the part of the RiskGate that shapes backtests.

M5 adds the stateful rules (daily loss, drawdown, stale data, API errors, kill switch). This module is
pure: it never increases risk, it scales hedge groups as a unit, and it refuses spot shorts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent.core.types import Target
from agent.data.datasets import Market


class Verdict(StrEnum):
    ACCEPT = "ACCEPT"
    CLIP = "CLIP"
    REJECT = "REJECT"


@dataclass(frozen=True)
class Decision:
    original: Target
    target: Target
    verdict: Verdict
    rule: str = ""


@dataclass(frozen=True)
class ExposureGate:
    max_weight: float = 0.25  # per instrument, fraction of equity (spec §4)
    max_gross: float = 1.0  # sum of |weights| (spec §4: no net leverage > 1x in v1)
    allow_spot_short: bool = False

    def check(self, targets: list[Target]) -> list[Decision]:
        weights = [t.weight for t in targets]
        rules = [""] * len(targets)

        def groups() -> dict[str, list[int]]:
            g: dict[str, list[int]] = {}
            for i, t in enumerate(targets):
                g.setdefault(t.group if t.group is not None else f"\0{i}", []).append(i)
            return g

        rejected: set[int] = set()
        for members in groups().values():
            if not self.allow_spot_short and any(
                targets[i].instrument.market is Market.SPOT and weights[i] < 0 for i in members
            ):
                for i in members:
                    weights[i], rules[i] = 0.0, "spot_short"
                    rejected.add(i)
                continue
            biggest = max(abs(weights[i]) for i in members)
            if biggest > self.max_weight:
                f = self.max_weight / biggest
                for i in members:
                    weights[i] *= f
                    rules[i] = "max_weight"

        gross = sum(abs(w) for w in weights)
        if gross > self.max_gross:
            f = self.max_gross / gross
            for i in range(len(weights)):
                if weights[i]:
                    weights[i] *= f
                    rules[i] = rules[i] or "max_gross"

        out = []
        for i, t in enumerate(targets):
            if i in rejected:
                verdict = Verdict.REJECT
            elif weights[i] != t.weight:
                verdict = Verdict.CLIP
            else:
                verdict = Verdict.ACCEPT
            out.append(Decision(t, t.with_weight(weights[i]), verdict, rules[i]))
        return out
