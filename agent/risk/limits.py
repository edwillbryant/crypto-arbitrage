"""Risk limits. Loaded once at startup from a read-only file; immutable for the life of the process.

HARD_CEILINGS cannot be exceeded by any config file: raising them requires a code change and review.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

HARD_CEILINGS: dict[str, float] = {
    "max_weight": 0.5,  # no single instrument above 50% of equity, ever
    "max_gross": 1.0,  # v1: no leverage above 1x gross
    "max_daily_loss": 0.05,
    "max_drawdown": 0.25,
}


class Limits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # exposure (fractions of equity)
    max_weight: float = Field(default=0.25, gt=0)
    max_gross: float = Field(default=1.0, gt=0)
    # losses (fractions)
    max_daily_loss: float = Field(default=0.01, gt=0)  # vs start-of-day equity (00:00 UTC) -> reduce-only
    max_drawdown: float = Field(default=0.08, gt=0)  # vs peak equity -> flatten and lock
    # data freshness
    max_book_age_s: float = Field(default=5.0, gt=0)  # -> reduce-only
    stale_flatten_s: float = Field(default=60.0, gt=0)  # -> flatten
    max_bars_late: int = Field(default=1, ge=0)  # -> reduce-only
    # exchange/API health (rolling window)
    api_error_limit: int = Field(default=5, gt=0)  # -> halt
    order_reject_limit: int = Field(default=3, gt=0)  # -> halt
    api_window_s: float = Field(default=300.0, gt=0)
    max_clock_drift_ms: float = Field(default=500.0, gt=0)  # -> halt (signed requests get rejected anyway)
    # collateral and margin
    depeg_reduce: float = Field(default=0.005, gt=0)  # |1 - stablecoin price| -> reduce-only
    depeg_flatten: float = Field(default=0.02, gt=0)  # -> flatten
    max_margin_ratio: float = Field(default=0.30, gt=0, lt=1)  # [ASSUMPTION] buffer; -> cut perp exposure
    margin_reduce_factor: float = Field(
        default=0.75, gt=0, lt=1
    )  # perp groups shrink to this x current per decision

    @model_validator(mode="after")
    def _consistent(self) -> Limits:
        for name, ceiling in HARD_CEILINGS.items():
            if getattr(self, name) > ceiling:
                raise ValueError(f"{name}={getattr(self, name)} exceeds the hard ceiling {ceiling}")
        if self.max_weight > self.max_gross:
            raise ValueError("max_weight cannot exceed max_gross")
        if self.depeg_reduce >= self.depeg_flatten:
            raise ValueError("depeg_reduce must be below depeg_flatten")
        if self.max_book_age_s >= self.stale_flatten_s:
            raise ValueError("max_book_age_s must be below stale_flatten_s")
        return self


def load_limits(path: Path) -> tuple[Limits, str]:
    """Return the limits and the SHA-256 of the file, which is logged at startup."""
    raw = Path(path).read_bytes()
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    return Limits(**data), hashlib.sha256(raw).hexdigest()
