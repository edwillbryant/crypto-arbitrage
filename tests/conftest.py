from __future__ import annotations

from pathlib import Path

import pytest

from agent.data.store import PITStore


@pytest.fixture
def store(tmp_path: Path) -> PITStore:
    return PITStore(tmp_path / "store")
