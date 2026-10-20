# Research protocol (pre-registered before any sweep was run)

**Committed:** 2026-10-20, before the first M3b backtest. Any later change to this file is visible in git history and
must be justified in the results report.

## Segments (chronological, never random)

| Segment | Range (UTC) | Used for |
|---|---|---|
| train | 2020-01-01 → 2024-01-01 | Parameter sweeps and selection |
| validation | 2024-01-01 → 2025-01-01 | One veto check of the selected parameters |
| **hold-out** | 2025-01-01 → 2026-10-01 | **Untouched until M4.** Run once per locked strategy. |

- The runner refuses any backtest whose end is after 2025-01-01 unless it is a hold-out run. The trial registry refuses hold-out records.
- Data before a segment's start may be used as indicator warm-up. That's history, not leakage.

## What runs

- Every pre-registered variant (C1 33, C2 16, C3 54, C5 144 = 247), on train and validation, at **1×, 2× and 3× costs**: 1,482 backtests. All of them are appended to `research/trials.jsonl`.
- **C4 (cross-exchange) is not run.** It needs a second venue's data, and Bybit is geo-blocked from this server. It stays "not evaluated, expected reject".
- **Engine settings:**
  - $10,000 starting equity
  - execution on 1h bars, fills at the next bar's open
  - VolTargetSizer at 10% annual volatility, max 25% per instrument
  - exposure gate at 100% gross
  - min trade 1% of equity
  - funding charged at settlement
  - VIP0 taker fees: 5 bps perp, 10 bps spot; slippage 2 bps BTC, 3 bps ETH. These are [ASSUMPTION]s.
- **Risk overlay:** sweeps use the exposure limits only, so each strategy's behaviour over the whole segment can be seen. A strategy locked flat by its first 8% drawdown would leave the rest of the segment untested. For every run, the report also states **whether and when the production 8% drawdown lock would have fired**. M4's hold-out runs use the full production RiskGate.

## Selection rule (train only, 1× costs, per strategy)

1. **Survives:** train net Sharpe > 0, **and** ≥ 70% of its grid neighbours (all cells within one step on every parameter) have train Sharpe > 0, **and** train net PnL > 0 at 2× costs.
2. **Choose:** among survivors, the cell with the **highest median neighbour Sharpe** (the centre of a plateau, not the peak). Ties go to the higher own Sharpe.
3. **Validation veto:** if the chosen cell's validation net Sharpe at 1× is ≤ 0, the strategy is marked **"fails validation"** and is not proposed for a lock.
4. No survivors means **REJECT at M3**. No re-gridding, no new variants.
5. The chosen cell's train **deflated Sharpe** is reported, using N = number of variants and their train Sharpe spread. The binding DSR test (> 0.95) is at M4, on the hold-out.

## What the owner decides at the checkpoint

For each strategy with a proposed cell: lock it for the hold-out, or drop it. The owner cannot pick a different cell than the
rule chose. Doing so would make the selection rule meaningless and is out of protocol.

## Expected outcomes, written down in advance

- **C5 (control)** may produce a train survivor by chance. If it also survives validation, that's a warning sign about the filters, and is reported as such. It must fail at M4.
- **C1** is expected to show positive carry in 2020–2021 and little after, and is likely to be regime-dependent.
- **C2** has few trades per variant (≈ 5–20 per asset per year), so its Sharpe estimates are noisy.
- **C3** has a small and clustered event count. "Insufficient evidence" is a likely M4 verdict.
