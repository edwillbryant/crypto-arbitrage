# Phase 1 — SPEC: 24/7 Autonomous Crypto Trading Agent

**Status:** DRAFT, awaiting owner approval (Gate 1 of 6: spec → architecture → plan → test-first build → review → ship)
**Date:** 2026-10-18
**Hard rule for this document:** no backtest number appears here. Every Sharpe, drawdown and trade-count cell
says `NOT RUN` until the validation harness (Phase 4) produces it from real data. Assumptions are labelled `[ASSUMPTION]`,
unverified facts `[UNVERIFIED]`.

---

## 0. Constraints discovered while writing this spec

| Constraint | Impact |
|---|---|
| This build container's network policy blocks exchange hosts (Binance, Bybit, OKX, Coinbase, Kraken, Deribit, Hyperliquid, data.binance.vision → proxy 403 / DNS fail). | No historical download or live data from here. Backtests must run on a machine (or a cloud environment with those hosts allowed) that can reach the data. **No numbers are fabricated to fill the gap.** |
| agenkit.xyz was unreachable from this container. | Phases and gates are mirrored as `docs/0N-*.md` files with an explicit approval line per gate. If AgenKit has its own file conventions/CLI, tell me and I'll conform. |
| Jurisdiction unknown. | Determines venue and whether perpetual futures are legally available to you (see §1). **Blocking question.** |

---

## 1. Market and venue

**Asset class:** crypto — BTC and ETH only for v1 (deepest liquidity, longest clean history, least survivorship bias).
Spot + linear perpetual futures. Alts are explicitly out of scope for v1 (thin books, delistings, manipulation).

Venue is a **gate decision for you**, because it depends on where you live:

| | Option A — non-US person | Option B — US person |
|---|---|---|
| Venue | Binance USDⓈ-M perps + Binance spot (Bybit as hot standby) | Coinbase Advanced (spot) + Coinbase Financial Markets "nano" perpetual-style futures (0.01 BTC / 0.10 ETH contracts, CFTC-regulated, launched Jul 2025, up to 10x) |
| Hours | 24/7/365; scheduled maintenance windows | 24/7 for spot and nano perps [UNVERIFIED for CFM maintenance windows] |
| Fees (VIP0) | ~0.02% maker / ~0.05% taker perps; ~0.10% spot [UNVERIFIED — from prior knowledge; must be confirmed on the live fee page before backtests] | Not found in public sources during this spec; must be read from the official CFM schedule [UNVERIFIED] |
| Min order | ~100 USDT notional on BTCUSDT perp [UNVERIFIED] | 1 nano contract (0.01 BTC ≈ $815 at current price) |
| Funding | Every 8h, published history | Funding mechanism exists; formula/interval not confirmed [UNVERIFIED] |
| API limits | Weight-based REST limits + order-rate limits; WebSocket for book/trades | Per-endpoint REST limits; WebSocket feeds [UNVERIFIED] |
| Latency | Retail cloud VM → 20–200 ms. **We assume we are slow** and design nothing that needs to be fast. | Same |
| Historical data | Free, timestamped klines, aggTrades, funding, premium index at data.binance.vision | Shorter perp history (since Jul 2025) → **too short to validate funding carry on its own**; would need Binance data as a proxy, which is a known weakness |

**Design consequence:** we are a slow, small, retail-latency participant. Any edge that depends on speed
(cross-exchange spot arbitrage, order-book microstructure) is presumed already captured by faster firms
and must prove otherwise.

### Current regime (as of 2026-10-17, from news/aggregator sources; not yet verified against exchange data)
- **Price/trend:** BTC ≈ $81.5k after dipping below $80k on Oct 15 following a hot CPI print; broke above $80k in late Aug on a short squeeze; crossed $86.5k in early Oct before the flush → **choppy, range-bound, no clean trend at daily scale.**
- **Leverage:** ~$1B liquidated on Oct 15, ~$315M/24h by Oct 17. Aggregate BTC futures OI hit its 2026 low (619.7k BTC) on Oct 12, then 645.7k BTC (~$52.5B) on Oct 15 — well below the September peak → **leverage flushed, partially rebuilding.**
- **Funding:** oscillating around zero (−0.0009%/8h to +0.0100%/8h, i.e. roughly −1% to +11% annualised) → **carry is currently thin; the carry strategy should mostly be flat right now.**
- **Volatility:** realised vol elevated around macro prints; no current implied-vol (DVOL) reading I could verify.
- **Macro:** price reacting to CPI/inflation data; spot ETF flows cited as the marginal driver → **macro-event risk is live.**

### Trade / avoid conditions (enforced in code, per strategy)
| Trade when | Avoid / stand down when |
|---|---|
| Spread ≤ 2 bps (BTC) / 3 bps (ETH) and top-of-book depth ≥ 20× our order | Spread or depth outside limits → no new orders |
| Data fresh (< 5 s for book, last bar closed on time) | Any stale feed → no new orders; derivatives positions reduced if stale > 60 s |
| Carry: expected funding net of all costs > hurdle | Funding below hurdle or flipping sign repeatedly |
| Trend: realised-vol regime within historical band | ±30 min around scheduled CPI / FOMC / NFP for any intraday strategy [ASSUMPTION: event calendar must be sourced] |
| Exchange status normal | Exchange maintenance, withdrawal suspensions, depeg of collateral stablecoin > 0.5% |

---

## 2. Candidate strategies (not a favourite in sight)

Signal generation is a pure function `signal(data_up_to_t) → target_position`; execution is a separate module that
turns target positions into orders. No strategy talks to the exchange.

| # | Strategy | Edge (one sentence) | Who's on the other side | Entry | Exit | Stop | Holding | Est. frequency [ASSUMPTION] | Why it might persist |
|---|---|---|---|---|---|---|---|---|---|
| C1 | **Funding carry (delta-neutral)** | Leveraged longs pay a persistent premium to hold perp exposure. | Retail/levered longs who prefer perps to spot | Long spot + short perp when trailing-3-period funding annualised > hurdle (fees + basis risk buffer) | Funding falls below exit threshold (hysteresis) | Basis divergence > X% or margin ratio limit → unwind both legs | Days–weeks | 5–30 round trips / yr | Structural demand for leverage; capital/custody limits stop arbs from fully closing it. **Bear:** Ethena-style vaults at scale have compressed it. |
| C2 | **Time-series trend (BTC/ETH, daily)** | Crypto trends persist over weeks due to slow diffusion and herding. | Mean-reverting discretionary traders, late sellers | Close > N-day breakout or MA-crossover (N swept 20–200), position ∝ vol target | Opposite signal | ATR-based trailing stop (k swept) | Weeks | 5–20 trades / yr / asset | Well-documented across asset classes; behavioural. **Bear:** few independent trades → weak statistics. |
| C3 | **Post-liquidation-cascade mean reversion (1h)** | Forced liquidations overshoot fair price; liquidity providers are paid to absorb them. | Forced sellers (liquidation engine) | OI drop > X% and return < −Y σ within 1–4h, funding not deeply negative | Revert to pre-cascade VWAP fraction or time-stop | Hard stop at −Z σ further | Hours–2 days | 10–40 / yr | Forced flow is price-insensitive. **Bear:** catching knives; sample is small and regime-clustered. |
| C4 | **Cross-exchange spot arbitrage** (legacy of this repo) | Price gaps between venues. | Slower venue's stale quotes | Net gap > fees + transfer cost | Convergence | n/a | Seconds–minutes | ? | **Presumed dead for a retail-latency bot**: HFT firms, transfer times, withdrawal fees. Tested only to document why it's rejected. |
| C5 | **Time-of-day / weekend seasonality** | — | — | — | — | — | Hours | High | **Control / data-mining canary.** If this passes our filters, our filters are too loose. |
| C6 | **Options volatility risk premium (Deribit)** | Option buyers overpay for crash insurance. | Hedgers | — | — | — | — | — | **Deferred to v2**: short-vol tail risk is incompatible with "survival first" at small size; needs margin modelling. |

---

## 3. Data and validation protocol

**Data (point-in-time, UTC, ms timestamps):** 1m/1h/1d klines (spot + perp), funding rate history, premium/mark index,
OI (where historical OI exists — Binance's public OI history is limited; this is a known gap for C3), and
top-of-book snapshots collected live from day one for slippage calibration.

**Leakage rules (enforced by tests):**
1. A signal at bar `t` may only read data with `close_time ≤ t`; fills happen at `t+1` open + slippage.
2. Funding is credited at its actual settlement timestamp, only if the position was held at that instant.
3. Universe is fixed ex-ante (BTC, ETH) → no survivorship selection; any later universe expansion uses
   point-in-time listings including delisted symbols.
4. No resampling, normalisation or z-scoring that uses future data (rolling windows only).

**Splits (chronological, never random):**
| Segment | Range | Use |
|---|---|---|
| Train / research | 2020-01-01 → 2023-12-31 | Parameter sweeps |
| Validation | 2024-01-01 → 2024-12-31 | Walk-forward choice |
| **Hold-out** | 2025-01-01 → 2026-09-30 | **Touched once per strategy, after parameters are frozen and committed** (commit hash recorded) |

**Costs:** taker fee on every fill unless the fill model proves maker execution; slippage = half-spread + impact
(calibrated from recorded book data, conservative default 2 bps BTC / 3 bps ETH per side [ASSUMPTION]);
funding paid/received; borrow costs where applicable. Every result also reported at **2× and 3× costs**.

**Robustness filters — a strategy is REJECTED if any fail:**
- Parameter neighbourhood: ≥ 70% of neighbouring parameter cells have positive net Sharpe, and the chosen cell is not a lone peak.
- Hold-out net Sharpe > 0 at 1× costs and the strategy still has positive expectancy at 2× costs.
- ≥ 30 independent trades (or ≥ 100 funding periods for C1) in hold-out; otherwise verdict = "insufficient evidence", not "pass".
- Deflated Sharpe Ratio (Bailey & López de Prado) > 0.95 given the **total number of variants tried** (from the trial registry).
- Works on both BTC and ETH with the same parameters (or is explicitly single-asset with a reason).
- No single trade/month contributes > 25% of total PnL.

**Trial registry:** every backtest run is appended to `research/trials.parquet` (strategy, params, data range,
cost multiplier, git hash, metrics). It is append-only and feeds the DSR correction. Metrics reported per run:
Sharpe (annualised, net), max drawdown, win rate, profit factor, trade count, exposure time, turnover, cost share of gross PnL.

---

## 4. Execution and risk (rules no model can override)

Architecture principle: `strategy → target position → RiskGate (pure deterministic code) → OrderManager → Exchange`.
The RiskGate is the only path to the exchange. No LLM, optimiser or strategy can call the exchange or modify limits at runtime;
limits load from a read-only config at startup and changes require a restart plus a signed config commit.

| Limit | Default (proposed — your call) | Action on breach |
|---|---|---|
| Max position notional per symbol | 25% of equity (carry legs counted gross) | Order clipped |
| Max gross exposure | 100% of equity (no net leverage > 1× in v1) | Order rejected |
| Max daily loss | 1.0% of start-of-day equity | Halt new orders, flatten intraday strategies, alert |
| Max drawdown from peak | 8% | **Flatten all, halt, require human restart** |
| Stale data | Book > 5 s or bar late > 1 bar | No new orders; reduce perp exposure if > 60 s |
| API errors | ≥ 5 errors or ≥ 3 order rejects in 5 min | Halt; flatten if position state is unknown → reconcile first |
| Position reconciliation | Exchange vs local state diff any > 0 | Halt until reconciled |
| Sizing | Volatility targeting at 10% annualised portfolio vol, capped by ¼-Kelly; never full Kelly | — |
| Kill switch | CLI command + file flag + Telegram/HTTP endpoint → cancel all orders, market-flatten all positions, lock trading | Manual reset only |
| Money movement | **API keys are trade-only: withdrawal permission OFF, IP-whitelisted.** The agent has no code path to transfer or withdraw funds. | — |

Every decision is logged as one JSON line: inputs snapshot hash, signal, risk-gate verdict + reason, order, fill, PnL attribution.

---

## 5. Bear case and live kill criteria

| Strategy | Most likely way it's fake | Live result that proves it's broken → automatic stop |
|---|---|---|
| C1 carry | Edge compressed by large basis-trade vaults; tail event = exchange insolvency or perp/spot decoupling | Realised net carry < 0 over 30 days while signal said "on"; basis gap > 2% intraday; any exchange withdrawal halt → unwind |
| C2 trend | Few trades → backtest Sharpe is mostly luck; whipsaw regime (like now) | Live drawdown > 1.5× worst hold-out drawdown, or 12-month live Sharpe below hold-out 5th percentile (bootstrap) |
| C3 cascade MR | Backtest fills at prices you couldn't get during a cascade | Live slippage > 2× modelled over 10 trades; 4 consecutive stop-outs |
| All | Costs higher than modelled | Rolling 20-trade realised cost > 1.5× model → halt strategy |
| All | Infra: single VM, single exchange, single feed | Heartbeat watchdog on a second host; if primary is silent > 60 s, the watchdog cancels all orders using a separate key |

---

## 6. Comparison table (results intentionally empty)

| Strategy | Edge | Market | Timeframe | Sharpe | Max DD | Trades | Cost sensitivity | Verdict |
|---|---|---|---|---|---|---|---|---|
| C1 Funding carry | Leverage premium | BTC/ETH spot+perp | 8h funding / days | NOT RUN | NOT RUN | NOT RUN | Low turnover, expected low | Pending |
| C2 TS trend | Persistence | BTC/ETH perp | 1d | NOT RUN | NOT RUN | NOT RUN | Expected low–medium | Pending |
| C3 Cascade MR | Liquidity provision to forced sellers | BTC/ETH perp | 1h | NOT RUN | NOT RUN | NOT RUN | Expected **high** | Pending |
| C4 X-exchange arb | Price gaps | Spot, multi-venue | seconds | NOT RUN | NOT RUN | NOT RUN | Extreme | Expected reject (documented) |
| C5 Seasonality | Control | BTC | 1h | NOT RUN | NOT RUN | NOT RUN | High | Control — must fail |
| C6 Short vol | VRP | Deribit options | days | — | — | — | — | Deferred v2 |

Nothing is deployed until it has a filled row and a PASS verdict that you approve.

---

## 7. Build plan preview (detailed in Phase 3)

```
agent/
  data/        ingest (Binance public archives + REST), point-in-time store (parquet), integrity checks
  research/    backtest engine, cost model, sweeps, walk-forward, DSR, trial registry
  strategies/  c1_carry.py, c2_trend.py, c3_cascade.py, c5_seasonality.py  (pure functions)
  risk/        RiskGate, limits config, kill switch
  execution/   OrderManager, paper broker, exchange adapter (ccxt), reconciliation
  ops/         logging, metrics, watchdog, alerts
  cli.py
tests/         unit + property + leakage + risk-gate + paper-broker integration
config/        paper.yaml, limits.yaml (read-only at runtime)
docs/          01-SPEC.md … 06-SHIP.md (one per gate)
```

**First module built:** `agent/data` + `tests/test_leakage.py`. Clean point-in-time data is a prerequisite for
every validation claim. The RiskGate is built test-first **before** any execution code exists.

**Paper-trading command (target; it doesn't exist yet):**
`python -m agent run --mode paper --config config/paper.yaml`
Live mode will require `--mode live`, a separate config, a non-zero `--max-notional` and an interactive confirmation.
It ships only after ≥ 30 days of paper trading within tolerance of the backtest, then starts at the minimum order size.

---

## 8. HOW COULD THIS SYSTEM FAIL?

Ranked by how likely each failure is to cost money:

1. **No strategy survives honest validation.** This is the most likely outcome and an acceptable one. The system then stays flat. That counts as a result.
2. **Weakest assumption: fill and cost realism.** Taker fees plus slippage can erase C2's small edge, and C3 lives or dies on fills during a cascade. That's why the 2×/3× cost stress and live-cost kill criteria exist.
3. **Carry has been arbitraged.** Large delta-neutral vaults now take the other side of the leverage premium. Today's funding is around zero, so C1 would be flat right now. Its value may come only in episodic euphoric periods.
4. **Too few independent trades.** C2 makes about 10 trades a year, so a 5-year Sharpe has wide error bars. We report confidence intervals, not point estimates.
5. **Exchange or counterparty failure** (insolvency, withdrawal freeze, auto-deleveraging hitting the short perp leg, a stablecoin depeg). Mitigations: small allocation per venue, a depeg monitor, and survival over yield.
6. **Infrastructure.** A single VM or feed, clock drift, a WebSocket that silently stops sending, a desync after a partial fill, or rate-limit bans during volatility. Mitigations: a watchdog on a second host, reconciliation on every loop, NTP monitoring, a sequence-gap check, and behaviour that stops trading when state is unknown.
7. **Regime dependence.** 2020–21 trends dominate any crypto trend backtest. Results are also reported excluding 2020–21.
8. **Data errors.** Bad ticks, missing funding prints, or archive files revised after the fact. Ingest checksums and a gap/outlier report run before any backtest.
9. **Human override.** The fastest way to break a risk system is to "temporarily" raise a limit. Changing a limit requires a config commit and a restart, and it's logged.
10. **Legal and tax.** Trading perps from a restricted jurisdiction can get the account frozen with funds inside. This is the reason for the §1 venue question.

---

## Gate 1 approval needed

Please decide:
1. **Jurisdiction → venue:** Option A (Binance/Bybit) or Option B (Coinbase/CFM), or another.
2. **Risk limits in §4:** accept the defaults or change them. Paper capital assumption: $10,000 notional [ASSUMPTION].
3. **Candidate set:** keep C1–C5 in, C6 deferred?
4. **Data access:** allow the exchange data hosts in this cloud environment's network settings, or run research on your own machine.

`[ ] Approved by owner — date: ____`
