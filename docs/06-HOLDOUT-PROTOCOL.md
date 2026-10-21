# M4 hold-out protocol (pre-registered before any hold-out data is read)

**Committed:** 2026-10-21. The owner locked C1 and C2 at the cells chosen by `docs/04-RESEARCH-PROTOCOL.md` and approved
switching the deflated Sharpe to daily returns (`docs/05-SWEEP-REVIEW.md`, decisions 1–3).

## Locked cells

| Strategy | Parameters |
|---|---|
| c1_carry | `{entry_apr: 0.2, exit_apr: 0.0, n_periods: 3}` |
| c2_trend | `{n: 200, k_atr: 2.0}` |

Each lock is a JSON file in `research/holdout_locks/`, holding the parameters, git commit, timestamp and the SHA-256 of this
protocol. It must be **committed before** its hold-out run. The runner checks this.

## The run (once per strategy, never repeated)

- **Period:** 2025-01-01 → 2026-10-01 (21 months). Earlier data is used only as indicator warm-up.
- **Risk:** the **production RiskGate** (spec §4 limits from `config/limits.yaml`). That includes the 1% daily-loss reduce-only rule and the 8% drawdown flatten-and-lock. A lock in the hold-out stays locked; nobody unlocks it mid-run.
- **Everything else as in M3b:** sizer, costs, execution.
- **Runs:**
  - costs: 1×, 2×, 3×, on both assets together (the binding runs)
  - plus BTC-only and ETH-only at 1×, for the "works on both assets" check

  That's 5 runs per strategy, all recorded in the trial registry under segment `holdout`. A second hold-out invocation for the same strategy is refused.

## Verdict rules (spec §3), evaluated in this order

All Sharpe ratios here are computed on **daily** returns (equity at each UTC day's close), annualised with √365.

1. **REJECT** if the 1× net Sharpe is ≤ 0, or the 2× net PnL is ≤ 0.
2. **INSUFFICIENT EVIDENCE** if there are fewer than 30 closed round trips, unless the strategy is C1 and it held a position through ≥ 100 funding settlements.
3. **REJECT** if any of these fail:
   - **Deflated Sharpe > 0.95.** Uses the hold-out daily SR and its skew and kurtosis. N = the strategy's number of pre-registered variants (C1 33, C2 16). V = the variance of those variants' **train daily** Sharpe ratios. C1's train runs predate the daily metrics, so its 33 train 1× variants are re-run once to obtain them. Those re-runs reuse identical parameters and are recorded as `diagnostic`.
   - **Both assets:** BTC-only and ETH-only 1× net Sharpe both > 0.
   - **No concentration:** no single round trip and no single calendar month contributes more than 25% of total net PnL.
4. Otherwise **PASS**.

Also reported (not binding): the deflated Sharpe with N = 247 (all variants of all strategies); whether and when the drawdown lock fired; and the PnL breakdown into funding, fees and price.

## After the run

- The results go into the spec's comparison table with real numbers.
- PASS strategies are candidates for paper trading (M7), subject to owner approval.
- Nothing is re-run, re-tuned or re-gridded on account of hold-out results. A REJECT or INSUFFICIENT EVIDENCE stands.
