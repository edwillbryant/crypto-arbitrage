# Build progress

| Milestone | Status | Notes |
|---|---|---|
| Gate 1 Spec | ✅ approved 2026-10-18 | Option A (Binance), default limits, C1–C5 |
| Gate 2 Architecture | ✅ approved 2026-10-18 | Manual wallet-transfer workflow accepted |
| Gate 3 Plan | ✅ approved 2026-10-18 | |
| M0 Scaffold | ✅ done | `make check` = ruff + mypy + pytest; Bittrex scripts in `legacy/` |
| M1 Data layer | 🟡 code done, **real data not yet ingested** | Blocked: exchange hosts unreachable from the build session |
| M2 Research engine | ✅ done (on synthetic fixtures) | No strategy has been backtested on real data |
| M5 RiskGate + kill switch | ✅ done | Moved ahead of M4 (owner decision) so the single hold-out run uses the production gate |

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

## M5 report (2026-10-18)

**Built (test-first; failing tests committed in affd7f0 before the implementation):**
- `agent/risk/limits.py` + `config/limits.yaml`:
  - Spec §4 limits as a frozen, `extra="forbid"` model: a typo'd key is an error, not a silently ignored setting.
  - Consistency checks between related limits.
  - **Hard ceilings** that no config can exceed: gross 1×, 50% per instrument, 5% daily loss, 25% drawdown. Raising them needs a code change.
  - The file's SHA-256 is returned for startup logging.
- `agent/risk/ledger.py`: SQLite (WAL, `synchronous=FULL`) state that survives restarts.
  - Contents: peak equity, start-of-day equity, the rolling API-error and order-reject window, the lock, and an append-only audit log.
  - Atomicity: updates are transactional, so a lock can't exist without its audit record (tested by injecting a crash).
  - Concurrency: thread-safe, because the kill-switch HTTP thread shares the connection.
  - Start-of-day equity is the last value seen *before* midnight, so losses made while the agent was down still count.
- `agent/risk/gate.py`:
  - `assess` is a declarative rule table, one line per spec rule.
  - `evaluate` is pure.
  - `RiskGate` is the stateful wrapper: it updates the ledger, locks on a drawdown breach, picks up the kill-switch flag, and audits mode changes.
- **Modes** (most severe wins):

  | Mode | Triggered by |
  |---|---|
  | FLATTEN, and lock | locked, max drawdown 8%, invalid equity |
  | FLATTEN | book stale > 60 s, stablecoin depeg ≥ 2% |
  | HALT: send nothing, hold, cancel open orders | clock drift > 500 ms, unreconciled state, ≥ 5 API errors / 5 min, ≥ 3 order rejects / 5 min |
  | REDUCE_ONLY | daily loss ≥ 1%, book stale > 5 s, bars late, depeg ≥ 0.5%, margin ratio > 30% |
  | NORMAL | exposure caps only |

  FLATTEN beats HALT because flatten orders are reduce-only and can't open positions even when local state is wrong.
- **Group handling:**
  - Hedge groups are always scaled by one factor, so a carry pair never becomes a naked leg.
  - An unhedged group (net delta breached) is flattened on its own.
  - A margin breach shrinks perp-containing groups to 0.75× their current size per decision.
- `agent/risk/killswitch.py`:
  - Trip it by flag file, `python -m agent kill`, or `POST http://127.0.0.1:<port>/kill` (bound to localhost only).
  - Unlock **only** with `python -m agent unlock --confirm --reason "..."`. It needs a reason of at least 10 characters and resets the drawdown peak. Deleting the flag file does not unlock, and there is no HTTP unlock route.
- The backtest engine now passes the gate its context (time, equity, current weights) and sends nothing on HOLD.

**Tests:** 169 offline tests pass.
- Property tests over random targets, positions, health and ledger states check that:
  - limits always hold
  - the gate never adds risk
  - reduce-only never grows or flips a position
  - HALT sends nothing
  - FLATTEN is all zeros
  - hedge legs keep their proportions

  These also passed a soak run of 10 seeds × 400 cases.
- **`agent/risk` has 100% line and branch coverage, enforced by `make check`.**
- The engine was run under the full gate:
  - a falling market triggers the drawdown flatten, and the agent stays flat after the price recovers
  - a −1.25% day turns every later decision into REDUCE

**Deviations from the spec wording (both tighter, flagged for review):**
1. Book stale > 60 s flattens **everything**, not just perp exposure. A partial reduction based on data we can't see seemed worse than going flat.
2. The daily-loss breach makes **all** strategies reduce-only. The spec says "flatten intraday strategies". That needs to know which strategy owns a target, so it's deferred to M3, where strategies exist and can flatten themselves on that signal.

**Carried to M6 (execution):**
- The OrderManager must honour `cancel_all`, send FLATTEN orders as reduce-only, and flatten exchange positions not covered by any target (orphans).
- It must also build `Health` from the live feed, the reconciler, the clock check and the hedge-delta monitor.
- The watchdog runs on a second host.
