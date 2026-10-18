# Data review: M1 real-data ingest (2026-10-18)

Source: data.binance.vision public archives, BTCUSDT + ETHUSDT, 2020-01-01 → 2026-09-30. Every file was
checksum-verified. The full machine report is in `docs/data_quality.md` (regenerate with `python -m agent data quality`).

**Result: 16/18 datasets PASS. The two FAILs (premium index) aren't used by any strategy.**

## Owner review needed: one classifier change made after seeing data

Spot 1m/1h initially FAILED on `close_time_mismatch`: 8 bars per series close *early*.

- They line up with known Binance spot halts: 2020-02-19, 2020-03-04, 2020-12-21, 2021-02-11, 2021-04-25, 2021-08-13, 2021-12-24 and 2023-03-24 (the spot trading suspension).
- In each case the close time is the moment trading stopped, and the gap follows.
- One (2020-12-21 14:00) is a zero-volume placeholder whose close time is *before* its open.

I reclassified bars that close early as a **WARN** (`truncated_bar`). Bars that claim to outlast their interval remain an **ERROR**. The reasoning: a truncated bar's prices are real trades, and no strategy decision depends on a bar's duration.

Since the change was made after seeing a failure, please confirm or reject it. Rejecting means the spot series fail, and C1 (which needs spot 1h) can't be researched until those bars are handled differently.

## Bugs found by the real data (all fixed test-first, with failing tests committed before the fix)

| Issue | Impact if missed | Fix |
|---|---|---|
| **45% of funding prints stamped 1–47 ms after the hour** (e.g. 16:00:00.002) | The engine charged the 16:00 settlement to a position opened *by* the 16:00 decision, and let one closed at 16:00 skip it. C1 enters when funding is high, so it would have booked one unearned payment per trade: an optimistic bias. | Funding timestamps are rounded to the settlement minute. Store rebuilt from the cached archives; engine test added. |
| BTCUSDT metrics files from 2020-09 list every row twice, verbatim (75,255 rows) | The ingest aborted | Identical repeats are dropped and counted; conflicting repeats still abort |
| `RemoteDisconnected` wasn't retried | The ingest aborted mid-run | Dropped connections and resets are now retried with backoff |

## Coverage

| Data | Coverage | Notes |
|---|---|---|
| Perp 1m/1h/1d candles | 100% | No gaps at all |
| Spot 1m/1h candles | 99.93–99.95% | 15 halt gaps (WARN). C1 goes flat when spot data is stale, which is also what happens live during a halt. |
| Funding | 100% | 8 h interval for the whole period on both symbols |
| OI metrics (C3) | BTC from **2020-09-01**, ETH from **2021-12-01** | Earlier than the late-2021 I'd feared for BTC. ETH's C3 sample is about 15 months shorter. 156 / 10 short gaps (WARN). |
| Premium index 1h | **FAIL**, 0.33% missing | Whole days absent from Binance's archive (e.g. 2021-07-24 → 07-27). No strategy uses it. If one ever does, it must be backfilled from REST first. |

## Known noise (WARN, no action)

- `outlier_returns` on 1-minute data flags thousands of bars. Many 1-minute returns are exactly zero, which makes the robust σ tiny. No strategy uses 1m bars (execution is on 1h), so I left the check as is.

## Carried into M3b

- The research runner must **refuse datasets whose latest quality report is FAIL**, as the architecture specifies. That isn't enforced in code yet; the sweep CLI will add it.
