# Build progress

| Milestone | Status | Notes |
|---|---|---|
| Gate 1 Spec | ✅ approved 2026-10-18 | Option A (Binance), default limits, C1–C5 |
| Gate 2 Architecture | ✅ approved 2026-10-18 | Manual wallet-transfer workflow accepted |
| Gate 3 Plan | ✅ approved 2026-10-18 | |
| M0 Scaffold | ✅ done | `make check` = ruff + mypy + pytest; Bittrex scripts in `legacy/` |
| M1 Data layer | 🟡 code done, **real data not yet ingested** | Blocked: exchange hosts unreachable from the build session |
| M2 Research engine | ✅ done (on synthetic fixtures) | No strategy has been backtested on real data |

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

## M2 report (2026-10-18)

**Built (test-first; failing tests committed in 8018dc8 before the implementation):**
- `agent/research/engine.py`: event-driven backtester. At each decision time t:
  - charge funding settled in (prev, t] on the position held over that interval (longs pay when the rate is positive)
  - mark at the last close available at t
  - hand the strategy a point-in-time snapshot
  - run sizer, then gate
  - fill at the open of the bar starting at t, plus costs. That open is the only field read from a bar not yet closed, and the docstring says so.
  - Refusals:
    - a perp backtest with no funding data (it would be silently optimistic)
    - spot shorts
    - unaligned start or end
    - a sizer whose bar differs from the execution bar
  - Missing fill bars and stale marks become logged events, not fills.
  - Every decision (raw → sized → final weight, verdict and rule) is kept in `result.decisions`.
- `agent/research/costs.py`: taker fee plus slippage on every fill, with an exact cost multiplier for the 2×/3× stress tests. Default fees are [ASSUMPTION] until checked against Binance's live fee page.
- `agent/sizing/voltarget.py`: volatility targeting at 10% annualised. The budget is split across active directional positions, which is conservative under correlation, and capped at 25% per instrument. Hedged groups pass through. No history means no position.
- `agent/risk/exposure.py`: the exposure part of the RiskGate. Per-instrument cap, gross cap, spot-short refusal, and hedge groups scaled as one unit (never one leg). A property test checks that limits always hold and that the gate never increases risk.
- `agent/research/metrics.py`:
  - Sharpe, max drawdown, CAGR, volatility, win rate, profit factor, trade count (round-trip episodes, with hedge groups counted as one trade), exposure, turnover, fees, slippage, funding, and cost share of gross PnL
  - plus the DSR inputs: per-period SR, n_obs, skew, kurtosis
- `agent/research/dsr.py`: PSR and DSR, pinned to the Bailey & López de Prado (2014) worked example (SR₀ = 0.1132/day, DSR = 0.9004, matching with 250 days/yr).
- `agent/research/registry.py`: append-only, hash-chained JSONL trial registry recording git commit and dirty flag. Edits, reordering and deletions are detected; hold-out records are refused (they go through the M4 lock workflow).
- `agent/research/sweep.py`: parameter grids and neighbour statistics for the "no lone peaks" filter.

**Tests:** 105 offline tests pass (`make check` clean). Engine accounting is checked against hand-computed equity paths, fees and slippage, funding sign and timing, round-trip episodes, and sign flips. At engine level, leakage tests check that snapshots only hold available rows, that future-poisoning changes nothing before the cut, and that a strategy can't mutate the data feed.

**Performance:** about 8.6 s per simulated year of hourly decisions (synthetic data), so about 1 min for the 2020–2026 range. Daily strategies take 1/24 of the steps.

**Recommendation for the plan:** run M5 (full RiskGate) **before** M4 (hold-out). The hold-out result should then include the production daily-loss and drawdown halts, not just exposure limits. Doing it the other way means re-running the hold-out, and the hold-out may only be run once.
