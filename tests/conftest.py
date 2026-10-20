from __future__ import annotations

from pathlib import Path

import pytest

from agent.data.store import PITStore

REPO_DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(autouse=True)
def _never_touch_real_data():  # type: ignore[no-untyped-def]
    """A test once overwrote data/quality_status.json via a CLI default. Fail any test that changes data/."""

    def snapshot() -> dict[str, float]:
        if not REPO_DATA.exists():
            return {}
        return {str(p): p.stat().st_mtime for p in REPO_DATA.glob("*") if p.is_file()}

    before = snapshot()
    yield
    assert snapshot() == before, "a test wrote into the repository's data/ directory"


@pytest.fixture
def store(tmp_path: Path) -> PITStore:
    return PITStore(tmp_path / "store")
