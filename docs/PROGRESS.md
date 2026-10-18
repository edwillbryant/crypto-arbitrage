# Build progress

| Milestone | Status | Notes |
|---|---|---|
| Gate 1 Spec | ✅ approved 2026-10-18 | Option A (Binance), default limits, C1–C5 |
| Gate 2 Architecture | ✅ approved 2026-10-18 | Manual wallet-transfer workflow accepted |
| Gate 3 Plan | ✅ approved 2026-10-18 | |
| M0 Scaffold | ✅ done | `make check` = ruff + mypy + pytest; Bittrex scripts in `legacy/` |
| M1 Data layer | 🟡 code done, **real data not yet ingested** | Blocked: exchange hosts unreachable from the build session |

## M1 report (2026-10-18)

**Built (test-first; tests committed failing in 568c892 before the implementation):**
- `agent/data/datasets.py`: dataset definitions and availability rules (bars are usable at their close, funding at settlement, OI metrics at create_time + 5 min [ASSUMPTION]).
- `agent/data/parse.py`: data.binance.vision CSV parsers. Handles optional header rows and ms/µs epoch timestamps, and rejects empty required fields.
- `agent/data/store.py`: point-in-time parquet store. `read(as_of=...)` never returns a row with `available_at > as_of`. History is append-only: identical rewrites are no-ops, revised values raise `DataConflict`, and partitions are written atomically.
- `agent/data/quality.py`: report covering duplicates, ordering, gaps, missing fraction (> 0.1% fails), OHLC consistency, close-time vs interval, non-positive prices, negative or zero volume, robust outlier returns, funding gaps and extremes, and negative OI.
- `agent/data/ingest.py`: archive planner (monthly files, then a daily tail), SHA-256 `.CHECKSUM` verification (mismatch aborts), raw cache, and an ingest report listing missing and unverified files. Unverified files are never written.
- CLI: `python -m agent data ingest` and `python -m agent data quality`.

**Tests:** 60 offline tests pass, including leakage/property tests (hypothesis) and the future-poisoning test. There are 4 opt-in integration tests (`pytest -m integration`) that check the [UNVERIFIED] archive-format assumptions against the real data.binance.vision.

**Not done (needs network):**
1. Run `pytest -m integration`. If it fails, fix the parsers before anything else.
2. `python -m agent data ingest` for BTCUSDT and ETHUSDT, 2020-01-01 → 2026-09-30.
3. `python -m agent data quality`, then commit `docs/data_quality.md` and review every ERROR/WARN by hand.
4. REST top-up for funding in the current partial month. Funding archives are monthly-only, so this is deferred until it's needed for paper trading.

**Known limitations / open assumptions:**
- Metrics publication lag of 5 min is a guess. It will be measured during live collection.
- Which archive frequencies exist per dataset, header presence, and the 2025 switch to µs timestamps in spot data are all checked by the integration tests, not assumed.
- Binance's OI metrics history starts later than 2020 [UNVERIFIED], so C3 may have a shorter sample than the other strategies.
