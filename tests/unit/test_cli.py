from __future__ import annotations

from pathlib import Path

import pytest

from agent.cli import DEFAULT_DATASETS, main
from agent.data.datasets import klines
from agent.data.parse import parse_csv
from agent.data.store import PITStore
from tests.fixtures.factories import kline_csv


def test_quality_on_empty_store_fails(tmp_path: Path) -> None:
    assert main(["data", "quality", "--store", str(tmp_path), "--out", str(tmp_path / "q.md")]) == 1


def test_quality_writes_report(tmp_path: Path) -> None:
    h1 = klines("perp", "1h")
    PITStore(tmp_path / "s").write(h1, "BTCUSDT", parse_csv(h1, kline_csv("2024-01-01", 100)))
    out = tmp_path / "q.md"
    assert main(["data", "quality", "--store", str(tmp_path / "s"), "--out", str(out)]) == 0
    assert "perp/klines/1h | BTCUSDT" in out.read_text()


def test_unknown_command_exits() -> None:
    with pytest.raises(SystemExit):
        main(["trade", "now"])


def test_default_datasets_cover_the_spec() -> None:
    assert {"perp/fundingRate", "perp/metrics", "perp/premiumIndexKlines/1h", "spot/klines/1d"} <= set(
        DEFAULT_DATASETS
    )
