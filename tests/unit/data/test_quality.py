from __future__ import annotations

import pandas as pd

from agent.data.datasets import FUNDING, METRICS, PREMIUM_1H, klines
from agent.data.parse import parse_csv
from agent.data.quality import Severity, quality_report, render_markdown
from tests.fixtures.factories import funding_csv, kline_csv, metrics_csv

H1 = klines("perp", "1h")


def _bars(n: int = 200) -> pd.DataFrame:
    return parse_csv(H1, kline_csv("2024-01-01", n))


def _checks(report, severity: Severity) -> set[str]:  # type: ignore[no-untyped-def]
    return {i.check for i in report.issues if i.severity is severity}


def test_clean_bars_pass() -> None:
    r = quality_report(H1, "BTCUSDT", _bars())
    assert r.passed, r.issues
    assert r.rows == 200
    assert r.missing_fraction == 0.0


def test_duplicates_fail() -> None:
    b = _bars()
    r = quality_report(H1, "BTCUSDT", pd.concat([b, b.iloc[[5]]]).sort_values("key_time"))
    assert not r.passed
    assert "duplicates" in _checks(r, Severity.ERROR)


def test_ohlc_inconsistency_fails() -> None:
    b = _bars()
    b.loc[10, "high"] = b.loc[10, "low"] * 0.5
    r = quality_report(H1, "BTCUSDT", b)
    assert "ohlc_consistency" in _checks(r, Severity.ERROR)


def test_non_positive_price_fails() -> None:
    b = _bars()
    b.loc[10, ["open", "low"]] = 0.0
    assert "non_positive_price" in _checks(quality_report(H1, "BTCUSDT", b), Severity.ERROR)


def test_small_gap_warns_large_gap_fails() -> None:
    b = _bars(2000)
    small = quality_report(H1, "BTCUSDT", b.drop(index=[100]).reset_index(drop=True))
    assert small.passed
    assert "gaps" in _checks(small, Severity.WARN)
    assert small.missing_fraction > 0

    large = quality_report(H1, "BTCUSDT", b.drop(index=range(100, 200)).reset_index(drop=True))
    assert not large.passed
    assert "missing_fraction" in _checks(large, Severity.ERROR)


def test_outlier_return_warns() -> None:
    b = _bars()
    b.loc[150, ["close", "high"]] = b.loc[150, "close"] * 3
    assert "outlier_returns" in _checks(quality_report(H1, "BTCUSDT", b), Severity.WARN)


def test_premium_index_may_be_negative() -> None:
    b = parse_csv(PREMIUM_1H, kline_csv("2024-01-01", 50))
    b[["open", "high", "low", "close"]] = b[["open", "high", "low", "close"]] - 1000
    assert quality_report(PREMIUM_1H, "BTCUSDT", b).passed


def test_funding_gap_warns() -> None:
    f = parse_csv(FUNDING, funding_csv("2024-01-01", 30)).drop(index=[10]).reset_index(drop=True)
    r = quality_report(FUNDING, "BTCUSDT", f)
    assert "gaps" in _checks(r, Severity.WARN)


def test_extreme_funding_warns() -> None:
    f = parse_csv(FUNDING, funding_csv("2024-01-01", 30))
    f.loc[5, "rate"] = 0.03
    assert "extreme_funding" in _checks(quality_report(FUNDING, "BTCUSDT", f), Severity.WARN)


def test_negative_open_interest_fails() -> None:
    m = parse_csv(METRICS, metrics_csv("2024-01-01", 20))
    m.loc[3, "sum_open_interest"] = -1
    assert "negative_open_interest" in _checks(quality_report(METRICS, "BTCUSDT", m), Severity.ERROR)


def test_empty_dataset_fails() -> None:
    r = quality_report(H1, "BTCUSDT", _bars().iloc[0:0])
    assert not r.passed


def test_markdown_render_lists_every_report() -> None:
    reports = [quality_report(H1, "BTCUSDT", _bars()), quality_report(H1, "ETHUSDT", _bars().iloc[0:0])]
    md = render_markdown(reports)
    assert "BTCUSDT" in md
    assert "ETHUSDT" in md
    assert "FAIL" in md
