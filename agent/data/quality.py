"""Data-quality report. A dataset with any ERROR issue must not be backtested."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import pandas as pd

from agent.data.datasets import Dataset, Kind

MAX_MISSING_FRACTION = 0.001  # more than 0.1% of expected rows missing fails the dataset
OUTLIER_SIGMAS = 10.0  # robust z-score of a log return that gets flagged for manual review
OUTLIER_WINDOW = 500
EXTREME_FUNDING = 0.01  # |rate| per funding period
FUNDING_TOLERANCE = pd.Timedelta(minutes=1)
MAX_EXAMPLES = 10


class Severity(StrEnum):
    ERROR = "ERROR"
    WARN = "WARN"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    check: str
    count: int
    examples: tuple[str, ...] = ()


@dataclass
class QualityReport:
    dataset: str
    symbol: str
    rows: int
    first: pd.Timestamp | None
    last: pd.Timestamp | None
    missing_fraction: float
    issues: list[Issue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)


def _examples(times: pd.Series) -> tuple[str, ...]:
    return tuple(str(t) for t in times.head(MAX_EXAMPLES))


def _flag(issues: list[Issue], severity: Severity, check: str, mask: pd.Series, times: pd.Series) -> None:
    n = int(mask.sum())
    if n:
        issues.append(Issue(severity, check, n, _examples(times[mask])))


def _gap_issue(key: pd.Series, expected: pd.Series | pd.Timedelta) -> Issue | None:
    diff = key.diff()
    gaps = diff > expected
    if not gaps.any():
        return None
    ex = tuple(
        f"{a} → {b}"
        for a, b in zip(key.shift()[gaps].head(MAX_EXAMPLES), key[gaps].head(MAX_EXAMPLES), strict=True)
    )
    return Issue(Severity.WARN, "gaps", int(gaps.sum()), ex)


def quality_report(
    dataset: Dataset, symbol: str, df: pd.DataFrame, *, max_missing: float = MAX_MISSING_FRACTION
) -> QualityReport:
    issues: list[Issue] = []
    if df.empty:
        issues.append(Issue(Severity.ERROR, "empty", 0))
        return QualityReport(dataset.name, symbol, 0, None, None, 1.0, issues)

    key = df["key_time"].reset_index(drop=True)
    df = df.reset_index(drop=True)
    _flag(issues, Severity.ERROR, "duplicates", key.duplicated(), key)
    if not key.is_monotonic_increasing:
        issues.append(Issue(Severity.ERROR, "not_sorted", 1))
    ukey = key.drop_duplicates().sort_values().reset_index(drop=True)

    missing = 0.0
    cadence = dataset.cadence
    if cadence is not None:
        expected_rows = int((ukey.iloc[-1] - ukey.iloc[0]) / cadence) + 1
        missing = max(0.0, 1 - len(ukey) / expected_rows)
        if (gap := _gap_issue(ukey, cadence)) is not None:
            issues.append(gap)
        if missing > max_missing:
            issues.append(
                Issue(Severity.ERROR, "missing_fraction", expected_rows - len(ukey), (f"{missing:.4%}",))
            )

    if dataset.is_bars:
        o, h, lo, c = (df[x] for x in ("open", "high", "low", "close"))
        bad = (h < np.maximum(o, c)) | (lo > np.minimum(o, c)) | (h < lo)
        _flag(issues, Severity.ERROR, "ohlc_consistency", bad, key)
        # Binance stamps close_time 1 ms (1 µs in microsecond-era files) before the next bar opens. A bar that
        # closes EARLY was cut short by an exchange halt (or is a zero-volume placeholder after one): its prices
        # are real but cover less time, so it is a warning. A bar claiming to outlast its interval is corrupt.
        span = df["close_time"] - key
        length = dataset.bar_length
        _flag(issues, Severity.ERROR, "close_time_mismatch", span >= length, key)
        _flag(issues, Severity.WARN, "truncated_bar", span < length - pd.Timedelta(milliseconds=1), key)
        if dataset.kind is Kind.KLINES:  # premium index is a spread and may be zero or negative
            _flag(
                issues,
                Severity.ERROR,
                "non_positive_price",
                (df[["open", "high", "low", "close"]] <= 0).any(axis=1),
                key,
            )
            _flag(issues, Severity.ERROR, "negative_volume", df["volume"] < 0, key)
            _flag(issues, Severity.WARN, "zero_volume", df["volume"] == 0, key)
            r = np.log(c.where(c > 0)).diff()
            med = r.rolling(OUTLIER_WINDOW, min_periods=50).median().shift()
            mad = (r - med).abs().rolling(OUTLIER_WINDOW, min_periods=50).median().shift() * 1.4826
            _flag(issues, Severity.WARN, "outlier_returns", ((r - med).abs() > OUTLIER_SIGMAS * mad), key)
    elif dataset.kind is Kind.FUNDING:
        expected = pd.to_timedelta(df["interval_hours"], unit="h").shift() + FUNDING_TOLERANCE
        if (gap := _gap_issue(key, expected)) is not None:
            issues.append(gap)
        _flag(issues, Severity.WARN, "extreme_funding", df["rate"].abs() > EXTREME_FUNDING, key)
    elif dataset.kind is Kind.METRICS:
        _flag(issues, Severity.ERROR, "negative_open_interest", df["sum_open_interest"] < 0, key)

    return QualityReport(dataset.name, symbol, len(df), ukey.iloc[0], ukey.iloc[-1], missing, issues)


def render_markdown(reports: list[QualityReport]) -> str:
    lines = [
        "# Data quality report",
        "",
        "| Dataset | Symbol | Rows | First | Last | Missing | Result |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        lines.append(
            f"| {r.dataset} | {r.symbol} | {r.rows:,} | {r.first} | {r.last} | "
            f"{r.missing_fraction:.4%} | {'PASS' if r.passed else 'FAIL'} |"
        )
    for r in reports:
        if not r.issues:
            continue
        lines += ["", f"## {r.dataset} {r.symbol}", ""]
        for i in r.issues:
            lines.append(
                f"- **{i.severity}** `{i.check}` x{i.count}"
                + (f": {', '.join(i.examples)}" if i.examples else "")
            )
    return "\n".join(lines) + "\n"
