from __future__ import annotations

from pathlib import Path

import pydantic
import pytest

from agent.risk.limits import HARD_CEILINGS, Limits, load_limits

REPO = Path(__file__).resolve().parents[3]


def test_defaults_match_the_approved_spec() -> None:
    lim = Limits()
    assert (lim.max_weight, lim.max_gross, lim.max_daily_loss, lim.max_drawdown) == (0.25, 1.0, 0.01, 0.08)
    assert (lim.max_book_age_s, lim.stale_flatten_s, lim.max_bars_late) == (5.0, 60.0, 1)
    assert (lim.api_error_limit, lim.order_reject_limit, lim.api_window_s) == (5, 3, 300.0)
    assert lim.max_clock_drift_ms == 500.0
    assert (lim.depeg_reduce, lim.depeg_flatten) == (0.005, 0.02)


def test_repo_config_loads_and_equals_defaults() -> None:
    lim, digest = load_limits(REPO / "config" / "limits.yaml")
    assert lim == Limits()
    assert len(digest) == 64


def test_limits_are_immutable() -> None:
    lim = Limits()
    with pytest.raises(pydantic.ValidationError):
        lim.max_drawdown = 0.5  # type: ignore[misc]


def test_unknown_keys_are_an_error(tmp_path: Path) -> None:
    p = tmp_path / "l.yaml"
    p.write_text("max_drawdwn: 0.1\n")  # typo must not be silently ignored
    with pytest.raises(pydantic.ValidationError):
        load_limits(p)


@pytest.mark.parametrize("field", sorted(HARD_CEILINGS))
def test_hard_ceilings_cannot_be_raised_by_config(field: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        Limits(**{field: HARD_CEILINGS[field] * 1.01})


@pytest.mark.parametrize(
    "kwargs",
    [{"max_weight": 0.0}, {"max_daily_loss": -0.01}, {"depeg_reduce": 0.03, "depeg_flatten": 0.02},
     {"max_book_age_s": 90.0, "stale_flatten_s": 60.0}, {"margin_reduce_factor": 1.0}],
)  # fmt: skip
def test_inconsistent_limits_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(pydantic.ValidationError):
        Limits(**kwargs)


def test_digest_changes_with_content(tmp_path: Path) -> None:
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("max_drawdown: 0.08\n")
    b.write_text("max_drawdown: 0.07\n")
    assert load_limits(a)[1] != load_limits(b)[1]


def test_max_weight_cannot_exceed_max_gross() -> None:
    with pytest.raises(pydantic.ValidationError):
        Limits(max_weight=0.3, max_gross=0.2)


def test_non_mapping_config_rejected(tmp_path: Path) -> None:
    p = tmp_path / "l.yaml"
    p.write_text("- 1\n- 2\n")
    with pytest.raises(ValueError, match="mapping"):
        load_limits(p)
