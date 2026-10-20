# M3b sweep results

Generated from `research/trials.jsonl` by `python -m agent research report`. Selection follows
`docs/04-RESEARCH-PROTOCOL.md` exactly. Hold-out (2025-01-01 → 2026-10-01) untouched.

## c1_carry

**Verdict: PROPOSE**: plateau median 3.56, validation 6.17

- Variants: 33; train Sharpe > 0: 33; survivors of the robustness rule: 32
- Train Sharpe across variants (1x costs): median 3.43, best 3.60, worst 1.62
- 0 of 33 variants would have locked (8% drawdown) at some point in train
- **Chosen cell:** `{'entry_apr': 0.2, 'exit_apr': 0.0, 'n_periods': 3}`
- Deflated Sharpe (train, N = 33 trials): 1.000 (the binding test is M4, on the hold-out)
- Production lock: would have locked at nan in train

| Segment | Costs | sharpe | max_drawdown | trades | win_rate | profit_factor | net_pnl | costs_over_gross | would lock at |
|---|---|---|---|---|---|---|---|---|---|
| validation | 1x | 6.17 | 0.00 | 5 | 1.00 | inf | 343 | 0.22 | nan |
| validation | 2x | 3.71 | 0.01 | 5 | 0.80 | 22.99 | 245 | 0.44 | nan |
| validation | 3x | 1.75 | 0.01 | 5 | 0.40 | 2.91 | 141 | 0.67 | nan |
| train | 1x | 3.52 | 0.01 | 31 | 0.84 | 19.12 | 2,513 | 0.20 | nan |
| train | 2x | 2.54 | 0.01 | 31 | 0.68 | 8.39 | 1,805 | 0.40 | nan |
| train | 3x | 1.59 | 0.02 | 31 | 0.58 | 3.88 | 1,155 | 0.61 | nan |

## c2_trend

**Verdict: PROPOSE**: plateau median 1.16, validation 1.15

- Variants: 16; train Sharpe > 0: 16; survivors of the robustness rule: 16
- Train Sharpe across variants (1x costs): median 0.89, best 1.31, worst 0.28
- 11 of 16 variants would have locked (8% drawdown) at some point in train
- **Chosen cell:** `{'n': 200, 'k_atr': 2.0}`
- Deflated Sharpe (train, N = 16 trials): 0.848 (the binding test is M4, on the hold-out)
- Production lock: would have locked at nan in train

| Segment | Costs | sharpe | max_drawdown | trades | win_rate | profit_factor | net_pnl | costs_over_gross | would lock at |
|---|---|---|---|---|---|---|---|---|---|
| validation | 1x | 1.15 | 0.05 | 7 | 0.43 | 2.87 | 722 | 0.03 | nan |
| validation | 2x | 1.11 | 0.05 | 7 | 0.43 | 2.76 | 699 | 0.06 | nan |
| validation | 3x | 1.08 | 0.05 | 7 | 0.43 | 2.66 | 677 | 0.09 | nan |
| train | 1x | 0.99 | 0.05 | 23 | 0.48 | 2.63 | 2,334 | 0.03 | nan |
| train | 2x | 0.96 | 0.05 | 23 | 0.48 | 2.56 | 2,269 | 0.05 | nan |
| train | 3x | 0.94 | 0.06 | 23 | 0.48 | 2.49 | 2,203 | 0.08 | nan |

## c3_cascade

**Verdict: REJECT**: no cell has Sharpe > 0, >= 70% positive neighbours and profit at 2x costs

- Variants: 54; train Sharpe > 0: 17; survivors of the robustness rule: 0
- Train Sharpe across variants (1x costs): median -0.26, best 0.42, worst -1.17
- 4 of 54 variants would have locked (8% drawdown) at some point in train

## c5_seasonality

**Verdict: REJECT**: no cell has Sharpe > 0, >= 70% positive neighbours and profit at 2x costs

- Variants: 144; train Sharpe > 0: 1; survivors of the robustness rule: 0
- Train Sharpe across variants (1x costs): median -1.30, best 0.08, worst -3.28
- 130 of 144 variants would have locked (8% drawdown) at some point in train

