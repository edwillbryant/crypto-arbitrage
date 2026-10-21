# M4 hold-out results (2025-01-01 → 2026-10-01)

Run once per locked strategy under the production RiskGate, per `docs/06-HOLDOUT-PROTOCOL.md`.
Sharpe ratios are on daily returns. Backtests on $10,000; not forecasts.

## c1_carry: **REJECT**

Locked params: `{'entry_apr': 0.2, 'exit_apr': 0.0, 'n_periods': 3}`

| Check | Result | Value |
|---|---|---|
| sharpe_1x>0 | ❌ | -0.26 |
| pnl_2x>0 | ❌ | -32 |
| evidence | ❌ | 2 trades, 21 funding periods held |
| dsr>0.95 | ❌ | 0.027 (N=33) |
| both_assets | ❌ | BTCUSDT -0.14, ETHUSDT -0.22 |
| concentration<=25% | ❌ | trade nan%, month nan% |

Not binding: DSR with N=247 (spread approximated from this strategy's variants): 0.008

| Universe | Costs | Sharpe (daily) | Max DD | Trades | Net PnL | Funding | Fees | Max trade share | Max month share | Gate lock |
|---|---|---|---|---|---|---|---|---|---|---|
| both | 1x | -0.26 | 0.1% | 2 | -11 | 10 | 16 | n/a | n/a | never |
| both | 2x | -0.74 | 0.3% | 2 | -32 | 10 | 32 | n/a | n/a | never |
| both | 3x | -0.89 | 0.5% | 2 | -54 | 10 | 48 | n/a | n/a | never |
| BTCUSDT | 1x | -0.14 | 0.1% | 1 | -5 | 5 | 8 | n/a | n/a | never |
| ETHUSDT | 1x | -0.22 | 0.1% | 1 | -6 | 5 | 8 | n/a | n/a | never |

## c2_trend: **REJECT**

Locked params: `{'k_atr': 2.0, 'n': 200}`

| Check | Result | Value |
|---|---|---|
| sharpe_1x>0 | ❌ | -0.27 |
| pnl_2x>0 | ❌ | -330 |
| evidence | ❌ | 11 trades, 581 funding periods held |
| dsr>0.95 | ❌ | 0.161 (N=16) |
| both_assets | ❌ | BTCUSDT -0.26, ETHUSDT -0.11 |
| concentration<=25% | ❌ | trade nan%, month nan% |

Not binding: DSR with N=247 (spread approximated from this strategy's variants): 0.093

| Universe | Costs | Sharpe (daily) | Max DD | Trades | Net PnL | Funding | Fees | Max trade share | Max month share | Gate lock |
|---|---|---|---|---|---|---|---|---|---|---|
| both | 1x | -0.27 | 7.6% | 11 | -297 | -32 | 23 | n/a | n/a | never |
| both | 2x | -0.30 | 7.9% | 11 | -330 | -32 | 45 | n/a | n/a | never |
| both | 3x | -0.57 | 8.2% | 12 | -554 | -26 | 65 | n/a | n/a | 2026-09-03 |
| BTCUSDT | 1x | -0.26 | 3.7% | 5 | -202 | -20 | 13 | n/a | n/a | never |
| ETHUSDT | 1x | -0.11 | 6.1% | 6 | -124 | -24 | 12 | n/a | n/a | never |

