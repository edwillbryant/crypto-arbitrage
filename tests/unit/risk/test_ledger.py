from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from agent.risk.ledger import RiskLedger

T = pd.Timestamp("2024-03-01 10:00", tz="UTC")


def test_peak_and_day_start(tmp_path: Path) -> None:
    led = RiskLedger(tmp_path / "risk.db")
    led.on_equity(T, 1000.0)
    led.on_equity(T + pd.Timedelta(hours=1), 1100.0)
    led.on_equity(T + pd.Timedelta(hours=2), 1050.0)
    s = led.state(T + pd.Timedelta(hours=2), window_s=300)
    assert s.peak_equity == 1100.0
    assert s.day_start_equity == 1000.0
    nxt = T.normalize() + pd.Timedelta(days=1)
    led.on_equity(nxt, 990.0)
    assert led.state(nxt, window_s=300).day_start_equity == 990.0  # resets at 00:00 UTC


def test_state_survives_restart(tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    led = RiskLedger(db)
    led.on_equity(T, 1000.0)
    led.on_equity(T, 1200.0)
    led.lock(T, "max_drawdown", source="gate")
    led.record_api_error(T, "error")
    led.close()
    again = RiskLedger(db)
    s = again.state(T, window_s=300)
    assert s.peak_equity == 1200.0 and s.locked and s.lock_reason == "max_drawdown" and s.api_errors == 1


def test_api_error_window_rolls(tmp_path: Path) -> None:
    led = RiskLedger(tmp_path / "r.db")
    for i in range(4):
        led.record_api_error(T + pd.Timedelta(seconds=i), "error")
    led.record_api_error(T, "reject")
    assert led.state(T + pd.Timedelta(seconds=10), window_s=300).api_errors == 4
    assert led.state(T + pd.Timedelta(seconds=10), window_s=300).order_rejects == 1
    assert led.state(T + pd.Timedelta(seconds=400), window_s=300).api_errors == 0


def test_unlock_needs_a_reason_and_resets_the_peak(tmp_path: Path) -> None:
    led = RiskLedger(tmp_path / "r.db")
    led.on_equity(T, 1000.0)
    led.on_equity(T, 900.0)
    led.lock(T, "max_drawdown", source="gate")
    with pytest.raises(ValueError):
        led.unlock(T, reason="")
    led.unlock(T, reason="reviewed losses; restarting at minimum size")
    s = led.state(T, window_s=300)
    assert not s.locked
    assert s.peak_equity == 900.0  # otherwise the 10% drawdown would re-lock immediately


def test_audit_log(tmp_path: Path) -> None:
    led = RiskLedger(tmp_path / "r.db")
    led.on_equity(T, 1000.0)
    led.lock(T, "kill switch", source="cli")
    led.unlock(T, reason="test drill complete")
    kinds = [e["kind"] for e in led.events()]
    assert kinds == ["lock", "unlock"]
    assert led.events()[0]["detail"] == {"reason": "kill switch", "source": "cli"}


def test_unknown_error_kind_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RiskLedger(tmp_path / "r.db").record_api_error(T, "oops")  # type: ignore[arg-type]
