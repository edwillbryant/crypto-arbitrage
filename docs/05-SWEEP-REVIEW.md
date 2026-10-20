# M3b sweep review: owner checkpoint (parameter lock)

Inputs: `docs/05-SWEEP-RESULTS.md` (generated) and `research/trials.jsonl`, which holds 1,482 sweep runs plus 6 diagnostic
runs, all hash-chain verified. Selection followed `docs/04-RESEARCH-PROTOCOL.md`, pre-registered before any run. **Hold-out untouched.**
All numbers are backtests on $10,000 with the assumed costs. They are not forecasts.

## Verdicts from the pre-registered rule

| Strategy | Verdict | Cell (rule's choice) | Train Sharpe 1× / 2× / 3× | Validation Sharpe 1× | Train max DD | Train trades |
|---|---|---|---|---|---|---|
| C1 funding carry | **PROPOSE** | entry 20% APR, exit 0%, n=3 | 3.52 / 2.54 / 1.59 | 6.17 | 1.3% | 31 |
| C2 trend | **PROPOSE** | n=200, k_atr=2.0 | 0.99 / 0.96 / 0.94 | 1.15 | 5.0% | 23 |
| C3 cascade fade | **REJECT** | none: 0 of 54 cells robust (best 0.42, median −0.26) | — | — | — | — |
| C4 x-exchange | not evaluated | second venue geo-blocked from this server | — | — | — | — |
| C5 control | **REJECT** (as it must) | 1 of 144 cells positive | — | — | — | — |

## The skeptic's notes: read these before locking

### C1: real edge, regime-dependent, statistics overstated

- **Profit source checks out.** Train net is $2,513, made of $3,147 funding received, −$168 price and basis moves, and $466 fees. The edge really is funding, not an accounting artefact (and the millisecond funding-timestamp bug found during ingest is fixed).
- **It's regime-dependent.** Excluding 2020–21 (diagnostic run, 2022–2023):

  | Costs | Sharpe | Net profit | Notes |
  |---|---|---|---|
  | 1× | 1.29 | $214 over two years (~1%/yr) | 6 trades |
  | 3× | 0.26 | ≈ $48 | fees ≈ funding |

  Most of the train profit came from the 2020–21 funding boom. Late-2026 funding is near zero (spec §1), so expect C1 to sit flat most of the time.
- **The DSR of 1.000 is not credible.** Hourly carry returns are extremely non-normal (skew 7.8, kurtosis 848: funding lands in three lumps a day), and 35,000 hourly observations make any positive mean look certain. **Proposal: compute M4's DSR on daily returns for every strategy.** This makes the test stricter, and it's being fixed before the hold-out is touched.
- **Tail risks the backtest can't show:** exchange insolvency or withdrawal freeze, auto-deleveraging of the short perp, a USDT depeg, and spot/perp basis blowouts. Note that FTX (Nov 2022) is in-sample and didn't hurt *Binance* carry. That says nothing about a Binance-specific event.
- **Sensitivity:** 32 of 33 cells survive, so the result doesn't hinge on the exact thresholds. Risk shows up only beyond 3× costs.

### C2: consistent but tiny, and statistically thin

- **Profit source:** price $2,907, minus $533 funding paid (longs pay) and $41 fees. It's genuinely insensitive to costs: Sharpe 0.94 at 3×.
- **Consistent across regimes:** the 2022–2023 diagnostic gives Sharpe 0.91 (7 trades, max DD 3.8%), similar to the full train and to 2024.
- **The chosen cell sits on the edge of the grid** (n=200 is the largest value tried). Edge cells have 5 neighbours instead of 8, so the robustness check is weaker there, and the best value may lie beyond the grid. The rule allowed it. Not extending the grid now is deliberate: it would add trials after seeing results.
- **It's small.** Average gross exposure is 4% of equity and it's in the market 28% of the time. Net was ~$580/yr on $10k in train. The sizer seems to undershoot the 10% vol target, probably because hourly-return volatility overstates daily volatility in crypto. A sizing change wouldn't change the Sharpe much, but it's a known calibration gap.
- **DSR on train is 0.848**, below the 0.95 bar.
- **It will very probably be "INSUFFICIENT EVIDENCE" at M4.** About 3.5 trades a year across both assets means roughly 6–7 trades in the 21-month hold-out, against the spec's minimum of 30. That's an honest likely outcome, not a failure of the process.
- 11 of 16 trend variants would have hit the 8% drawdown lock in train. The chosen cell didn't.

### C3 and C5

- **C3 is rejected cleanly:** 17 of 54 cells were positive, but none had positive neighbours *and* profit at 2× costs. Per protocol it's not re-gridded.
- **The C5 control failed, as it must,** but mostly on **costs** (median train fees $1,693 vs price −$82). It's a weaker test of the overfitting filters than intended. A better control for v2 would be a low-turnover random-signal strategy.

## Decisions needed (owner)

1. **Lock C1** at `{entry_apr: 0.2, exit_apr: 0.0, n_periods: 3}` for the hold-out? (Lock or drop. Picking a different cell isn't allowed by the protocol.)
2. **Lock C2** at `{n: 200, k_atr: 2.0}`? (Lock or drop.)
3. **Amend the M4 protocol before the hold-out:** compute the deflated Sharpe on **daily** returns. This is stricter, and it's proposed now, before any hold-out data is seen.
4. **Acknowledge the expected M4 outcomes:** C1 will likely be mostly flat in 2025–26 (low funding), and C2 is likely "insufficient evidence" on trade count. If both end without a PASS, paper trading (M7) runs with strategies off. That still tests the infrastructure.
