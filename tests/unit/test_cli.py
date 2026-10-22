from __future__ import annotations

from pathlib import Path

import pytest

from agent.cli import DEFAULT_DATASETS, main
from agent.data.datasets import klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from tests.fixtures.factories import kline_csv


def test_quality_on_empty_store_fails(tmp_path: Path) -> None:
    out = ["--out", str(tmp_path / "q.md"), "--status", str(tmp_path / "status.json")]
    assert main(["data", "quality", "--store", str(tmp_path), *out]) == 1


def test_quality_writes_report(tmp_path: Path) -> None:
    h1 = klines("perp", "1h")
    PITStore(tmp_path / "s").write(h1, "BTCUSDT", parse_csv(h1, kline_csv("2024-01-01", 100)))
    out = tmp_path / "q.md"
    status = tmp_path / "status.json"
    assert (
        main(["data", "quality", "--store", str(tmp_path / "s"), "--out", str(out), "--status", str(status)])
        == 0
    )
    assert '"perp/klines/1h:BTCUSDT": "PASS"' in status.read_text()
    assert "perp/klines/1h | BTCUSDT" in out.read_text()


def test_unknown_command_exits() -> None:
    with pytest.raises(SystemExit):
        main(["trade", "now"])


def test_default_datasets_cover_the_spec() -> None:
    assert {"perp/fundingRate", "perp/metrics", "perp/premiumIndexKlines/1h", "spot/klines/1d"} <= set(
        DEFAULT_DATASETS
    )


@pytest.mark.parametrize(
    "cmd", ["sweep", "diagnose", "report", "backfill-daily", "lock", "holdout", "verdict"]
)
def test_every_research_command_is_registered(cmd: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        main(["research", cmd, "--help"])
    assert e.value.code == 0 and "usage" in capsys.readouterr().out


def test_paper_run_refuses_a_config_that_enables_nothing(tmp_path: Path) -> None:
    cfg = tmp_path / "paper.yaml"
    cfg.write_text(f"strategies: []\nmonitor_only: false\nstate_dir: {tmp_path / 'state'}\n")
    assert main(["run", "--mode", "paper", "--config", str(cfg)]) == 2


def test_shipped_paper_config_is_monitor_only() -> None:
    from agent.live.config import load_paper_config  # noqa: PLC0415

    cfg = load_paper_config(Path("config/paper.yaml"))
    assert cfg.monitor_only and not cfg.strategies


def test_watchdog_refuses_without_a_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_WATCHDOG_SECRET", raising=False)
    assert main(["watchdog", "--alerts", str(tmp_path / "a.jsonl")]) == 2
