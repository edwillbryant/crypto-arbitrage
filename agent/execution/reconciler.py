"""Compares what the exchange says we hold with what our own fills say we should hold.

Any difference beyond half a quantity step, or any resting order (we only send IOC/market orders),
means something happened outside the agent's knowledge. The RiskGate then HALTs until a human looks.
On the first check after start-up there are no expectations yet, so the exchange's state is adopted.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.execution.types import SymbolRules


@dataclass(frozen=True)
class ReconResult:
    ok: bool
    detail: str = ""


class Reconciler:
    def __init__(self, rules: dict[str, SymbolRules]) -> None:
        self.rules = rules
        self._expected: dict[str, float] | None = None

    def expect(self, positions: dict[str, float]) -> None:
        self._expected = dict(positions)

    def apply(self, fills: list[tuple[str, float]]) -> None:
        if self._expected is None:
            self._expected = {}
        for key, qty in fills:
            self._expected[key] = self._expected.get(key, 0.0) + qty

    def check(self, actual: dict[str, float], *, open_orders: list[str]) -> ReconResult:
        if self._expected is None:
            self.expect(actual)
            return ReconResult(True, "adopted exchange state at start-up")
        if open_orders:
            return ReconResult(False, f"unexpected open orders: {open_orders[:5]}")
        diffs = []
        for key in sorted(set(actual) | set(self._expected)):
            a, e = actual.get(key, 0.0), self._expected.get(key, 0.0)
            step = self.rules[key].step if key in self.rules else 0.0
            if abs(a - e) > step / 2:
                diffs.append(f"{key}: exchange {a} vs expected {e}")
        return ReconResult(not diffs, "; ".join(diffs))
