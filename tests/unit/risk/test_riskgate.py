"""The stateful RiskGate wrapper: ledger updates, drawdown lock, kill-switch flag, audit trail."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from agent.core.types import Instrument, Target
from agent.risk.exposure import Verdict
from agent.risk.gate import GateContext, Health, Mode, RiskGate
from agent.risk.killswitch import KillSwitch
from agent.risk.ledger import RiskLedger
from agent.risk.limits import Limits

T = pd.Timestamp("2024-03-01 10:00", tz="UTC")
BTC = Instrument("BTCUSDT", "perp")


def test_requires_context(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="GateContext"):
        RiskGate(Limits(), RiskLedger(tmp_path / "r.db")).check([Target(BTC, 0.1)])


def test_drawdown_locks_persistently(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    gate = RiskGate(Limits(), RiskLedger(db))
    gate.check([Target(BTC, 0.1)], GateContext(T, 1000.0))
    d = gate.check([Target(BTC, 0.1)], GateContext(T + pd.Timedelta(hours=1), 915.0))
    assert d[0].verdict is Verdict.FLATTEN
    # equity recovers and the process restarts: still locked until a human unlocks
    again = RiskGate(Limits(), RiskLedger(db))
    d2 = again.check([Target(BTC, 0.1)], GateContext(T + pd.Timedelta(days=2), 2000.0))
    assert d2[0].verdict is Verdict.FLATTEN and again.last is not None and again.last.mode is Mode.FLATTEN
    assert "locked: max_drawdown" in again.last.triggers


def test_flag_file_flattens_via_the_gate(tmp_path: Path) -> None:
    led = RiskLedger(tmp_path / "r.db")
    ks = KillSwitch(led, tmp_path / "KILL")
    gate = RiskGate(Limits(), led, killswitch=ks)
    assert gate.check([Target(BTC, 0.1)], GateContext(T, 1000.0))[0].verdict is Verdict.ACCEPT
    (tmp_path / "KILL").write_text("operator")
    assert gate.check([Target(BTC, 0.1)], GateContext(T, 1000.0))[0].verdict is Verdict.FLATTEN


def test_health_provider_and_mode_changes_are_audited(tmp_path: Path) -> None:
    led = RiskLedger(tmp_path / "r.db")
    sick = {"now": False}
    gate = RiskGate(Limits(), led, health=lambda t: Health(reconciled=not sick["now"]))
    gate.check([Target(BTC, 0.1)], GateContext(T, 1000.0))
    gate.check([Target(BTC, 0.1)], GateContext(T, 1000.0))  # same mode: not re-audited
    sick["now"] = True
    d = gate.check([Target(BTC, 0.1)], GateContext(T, 1000.0, {BTC.key: 0.05}))
    assert d[0].verdict is Verdict.HOLD and d[0].target.weight == 0.05
    modes = [e["detail"]["mode"] for e in led.events() if e["kind"] == "mode"]
    assert modes == ["NORMAL", "HALT"]
