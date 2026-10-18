from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from agent.cli import main
from agent.risk.killswitch import KillSwitch, serve_http
from agent.risk.ledger import RiskLedger

T = pd.Timestamp("2024-03-01 10:00", tz="UTC")


def _ks(tmp_path: Path) -> KillSwitch:
    return KillSwitch(RiskLedger(tmp_path / "risk.db"), tmp_path / "KILL")


def test_not_tripped_by_default(tmp_path: Path) -> None:
    assert not _ks(tmp_path).tripped(T)


def test_flag_file_trips_and_persists(tmp_path: Path) -> None:
    ks = _ks(tmp_path)
    (tmp_path / "KILL").write_text("operator: smoke detected\n")
    assert ks.tripped(T)
    (tmp_path / "KILL").unlink()  # deleting the file is NOT an unlock
    assert ks.tripped(T)
    assert _ks(tmp_path).tripped(T)  # survives a restart


def test_trip_and_reset(tmp_path: Path) -> None:
    ks = _ks(tmp_path)
    ks.trip(T, source="test", reason="drill")
    assert ks.tripped(T) and (tmp_path / "KILL").exists()
    ks.reset(T, reason="drill finished, positions verified flat")
    assert not ks.tripped(T) and not (tmp_path / "KILL").exists()


def test_http_can_trip_but_never_unlock(tmp_path: Path) -> None:
    ks = _ks(tmp_path)
    server = serve_http(ks, port=0)
    try:
        host, port = server.server_address[:2]
        assert host == "127.0.0.1"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(
                urllib.request.Request(f"http://{host}:{port}/unlock", method="POST"), timeout=5
            )
        assert e.value.code == 404
        assert not ks.tripped(T)
        with urllib.request.urlopen(
            urllib.request.Request(f"http://{host}:{port}/kill", method="POST"), timeout=5
        ) as r:
            assert r.status == 200
        assert ks.tripped(T)
    finally:
        server.shutdown()
        server.server_close()


def test_cli_kill_and_unlock(tmp_path: Path) -> None:
    args = ["--state", str(tmp_path / "risk.db"), "--flag", str(tmp_path / "KILL")]
    assert main(["kill", *args, "--reason", "drill"]) == 0
    assert _ks(tmp_path).tripped(T)
    assert main(["unlock", *args]) == 2  # refuses without --confirm and a reason
    assert _ks(tmp_path).tripped(T)
    assert main(["unlock", *args, "--confirm", "--reason", "drill complete, flat verified"]) == 0
    assert not _ks(tmp_path).tripped(T)
