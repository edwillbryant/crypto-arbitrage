"""One trading cycle, start to finish. Same Strategy, Sizer and RiskGate objects as the backtest.

  feed -> snapshot -> strategy -> sizer -> health -> RiskGate -> OrderManager -> broker -> reconcile -> log

Fail-safe defaults: a strategy exception means flat targets; an unreachable broker means unreconciled
state (HALT); every exchange error is counted in the persistent ledger (5 in 5 min -> HALT).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from agent.core.types import Instrument, Target
from agent.data.datasets import Market
from agent.execution.hedge import HedgeMonitor
from agent.execution.order_manager import ExecutionReport, OrderManager
from agent.execution.reconciler import Reconciler, ReconResult
from agent.execution.types import Broker
from agent.live.feed import MarketFeed
from agent.ops.log import DecisionLog
from agent.risk.gate import GateContext, GateResult, Health, Mode, RiskGate
from agent.risk.ledger import RiskLedger
from agent.sizing.voltarget import Sizer
from agent.strategies.base import Strategy


@dataclass
class CycleReport:
    time: pd.Timestamp
    mode: Mode
    triggers: tuple[str, ...]
    equity: float
    events: list[str] = field(default_factory=list)


class LiveLoop:
    def __init__(
        self,
        *,
        strategy: Strategy,
        feed: MarketFeed,
        broker: Broker,
        gate: RiskGate,
        sizer: Sizer,
        orders: OrderManager,
        reconciler: Reconciler,
        hedge: HedgeMonitor,
        ledger: RiskLedger,
        log: DecisionLog,
        heartbeat_path: Path,
        vol_lookback: pd.Timedelta = pd.Timedelta(days=60),
        paper_funding: bool = True,
    ) -> None:
        self.strategy, self.feed, self.broker, self.gate, self.sizer = strategy, feed, broker, gate, sizer
        self.orders, self.reconciler, self.hedge, self.ledger, self.log = (
            orders,
            reconciler,
            hedge,
            ledger,
            log,
        )
        self.heartbeat_path, self.vol_lookback, self.paper_funding = (
            heartbeat_path,
            vol_lookback,
            paper_funding,
        )
        self.orders.on_error = self._on_error
        self._now: pd.Timestamp | None = None
        self._prev: pd.Timestamp | None = None
        self._events: list[str] = []
        self._last_equity = math.nan

    def _on_error(self, kind: str, detail: str) -> None:
        assert self._now is not None
        self.ledger.record_api_error(self._now, "reject" if kind == "reject" else "error")
        self._events.append(f"{kind}: {detail}")

    def _safely(self, what: str, fn: Any) -> Any:
        try:
            return fn()
        except Exception as e:  # broad on purpose - every broker failure is an API error, never a crash
            self._on_error("error", f"{what}: {e}")
            return None

    def _settle_paper_funding(
        self,
        instruments: list[Instrument],
        positions: dict[str, float],
        marks: dict[str, float],
        now: pd.Timestamp,
    ) -> None:
        """Paper only: real exchanges settle funding themselves."""
        apply = getattr(self.broker, "apply_funding", None)
        if apply is None or not self.paper_funding or self._prev is None:
            return
        for i in instruments:
            if i.market is Market.PERP and positions.get(i.key) and i.key in marks:
                for _, rate in self.feed.funding_settlements(i, self._prev, now):
                    apply(i, rate=rate, mark=marks[i.key])

    def step(self, now: pd.Timestamp) -> CycleReport:
        self._now, self._events = now, []
        self.feed.advance(now)
        for err in self.feed.drain_errors():
            self._on_error("error", f"feed: {err}")
        instruments = self.strategy.instruments()
        marks = {i.key: m for i in instruments if (m := self.feed.mark(i)) is not None}

        positions = self._safely("positions", self.broker.positions)
        open_orders = self._safely("open_orders", self.broker.open_orders)
        if positions is not None:
            self._settle_paper_funding(instruments, positions, marks, now)
        equity = self._safely("equity", lambda: self.broker.equity(marks)) if positions is not None else None
        equity = self._last_equity if equity is None else equity
        self._last_equity = equity
        if positions is None or open_orders is None:
            recon = ReconResult(False, "broker unavailable")
        else:
            recon = self.reconciler.check(positions, open_orders=open_orders)

        try:
            raw = self.strategy.decide(self.feed.snapshot(self.strategy.requests(), now))
        except Exception as e:  # broad on purpose - a strategy bug must not stop risk management
            self._events.append(f"strategy error: {e!r}; targets default to flat")
            raw = []
        seen = {t.instrument.key for t in raw}
        targets = raw + [Target(i, 0.0) for i in instruments if i.key not in seen]
        sized = self.sizer.size(
            targets, {i.key: self.feed.closes(i, self.vol_lookback, now) for i in instruments}
        )

        groups: dict[str, list[str]] = {}
        for t in sized:
            if t.group is not None:
                groups.setdefault(t.group, []).append(t.instrument.key)
        health = Health(
            book_age_s=self.feed.book_age_s(now),
            bars_late=self.feed.bars_late(now),
            clock_drift_ms=self.feed.clock_drift_ms(),
            reconciled=recon.ok,
            depeg=self.feed.depeg(),
            margin_ratio=None,  # [M6b] from the exchange account endpoint; paper has no margin model
            unhedged_groups=self.hedge.update(now, positions or {}, marks, groups),
        )
        if not recon.ok:
            self._events.append(f"reconciliation: {recon.detail}")
        self.gate.health = lambda t: health
        current = ({k: q * marks.get(k, 0.0) / equity for k, q in (positions or {}).items()}
                   if equity and math.isfinite(equity) else {})  # fmt: skip
        self.gate.check(sized, GateContext(now, equity, current))
        result = self.gate.last
        assert result is not None
        rep = self.orders.execute(result, now=now, equity=equity, marks=marks, cycle_id=now.isoformat())
        self.reconciler.apply(
            [(o.instrument.key, a.filled_qty) for o, a in zip(rep.orders, rep.acks, strict=True)]
        )

        events = self._events + rep.events
        self.log.write(
            _record(now, result=result, equity=equity, marks=marks, health=health, targets=targets,
                    sized=sized, rep=rep, events=events)
        )  # fmt: skip
        tmp = self.heartbeat_path.with_suffix(".tmp")
        tmp.write_text(now.isoformat())
        tmp.replace(self.heartbeat_path)
        self._prev = now
        return CycleReport(now, result.mode, result.triggers, equity, events)


def _record(
    now: pd.Timestamp,
    *,
    result: GateResult,
    equity: float,
    marks: dict[str, float],
    health: Health,
    targets: list[Target],
    sized: list[Target],
    rep: ExecutionReport,
    events: list[str],
) -> dict[str, Any]:
    decisions = [
        {"instrument": d.target.instrument.key, "raw": t.weight, "sized": s.weight, "final": d.target.weight,
         "verdict": str(d.verdict), "rule": d.rule}
        for d, t, s in zip(result.decisions, targets, sized, strict=True)
    ]  # fmt: skip
    orders = [
        {"id": o.client_id, "instrument": o.instrument.key, "qty": o.qty, "type": str(o.type),
         "limit": o.limit_price, "reduce_only": o.reduce_only, "status": a.status, "filled": a.filled_qty,
         "price": a.avg_price, "fee": a.fee, "reason": a.reason}
        for o, a in zip(rep.orders, rep.acks, strict=True)
    ]  # fmt: skip
    return {
        "time": now, "mode": result.mode.name, "triggers": list(result.triggers), "equity": equity,
        "marks": marks,
        "health": health.__dict__ | {"unhedged_groups": sorted(health.unhedged_groups)},
        "decisions": decisions, "orders": orders, "events": events,
    }  # fmt: skip
