# Phase 2 — ARCHITECTURE

**Status:** APPROVED 2026-10-18 (Gate 2 of 6)
**Inputs:** `docs/01-SPEC.md` (approved 2026-10-18: Option A Binance/Bybit, default limits, C1–C5)
**Date:** 2026-10-18

Facts about the venue that this design depends on, and that are marked `[UNVERIFIED]`, become integration tests
against Binance testnet in Phase 4. Nothing in the design should assume they're true until those tests pass.

---

## 1. Design principles

1. **One code path for backtest, paper and live.** Strategies, the RiskGate, sizing and order logic are the same objects in all three
   modes. Only the `Broker` and `Clock` implementations change (`SimBroker`/`ReplayClock`, `PaperBroker`/`WallClock`, `BinanceBroker`/`WallClock`).
   This removes the most common source of backtest-to-live drift.
2. **Strategies are pure.** `target(snapshot) -> targets`. No I/O, no clock, no exchange handle. They're trivially testable for look-ahead.
3. **The RiskGate is the only door to the exchange.** It's deterministic code with no learned components. It loads its limits once at startup
   from a read-only file whose hash is logged. Nothing at runtime, model or human, can raise a limit without a restart.
4. **Fail closed.** An unknown state (stale data, a reconciliation mismatch, an exception in the loop) means no new orders. If the state stays unknown, flatten.
5. **No money movement exists in code.** The exchange adapter is a whitelist wrapper. Withdraw, transfer and convert endpoints aren't reachable,
   and a test asserts this. API keys are created with withdrawals disabled and IP restriction on.

---

## 2. Component diagram

```
                ┌───────────────────────── research (offline) ─────────────────────────┐
 data.binance.  │  ingest ──▶ PIT store (parquet) ──▶ backtest engine ──▶ trial registry│
 vision / REST ─┼─▶ checksums, gap/outlier report     (same Strategy + RiskGate code)    │
                └──────────────────────────────────────────────────────────────────────┘

                ┌──────────────────────────── live process (asyncio) ────────────────────────────┐
 Binance WS ───▶│ MarketData ──▶ Snapshot ──▶ Strategy[C1..C5] ──▶ targets                         │
 Binance REST ─▶│   (staleness, seq-gap)          (pure)                │                          │
                │                                                       ▼                          │
                │ Portfolio ◀── Reconciler ◀── Broker ◀── OrderManager ◀── RiskGate ◀── Sizer       │
                │   state                    (Sim/Paper/Binance)  (diff→orders)  (hard limits)      │
                │      │                                                                           │
                │      └──▶ DecisionLog (JSONL) ── Metrics (Prometheus text) ── Alerts             │
                │ KillSwitch: flag file │ CLI │ localhost HTTP  ──▶ cancel all + flatten + lock     │
                └──────────────────────────────────────────────────────────────────────────────────┘
                         ▲ heartbeat (every 5 s)
 second host ──▶ Watchdog (separate trade-only key): primary silent > 60 s → cancel all orders,
                 reduce-only flatten perps, page owner.
```

---

## 3. Modules and interfaces

```python
# agent/core/types.py — frozen dataclasses, Decimal for prices/quantities
Instrument(venue, symbol, kind: "spot"|"perp", tick, step, min_notional)
Bar(open_time, close_time, o, h, l, c, volume)                # close_time is the availability time
FundingPrint(symbol, funding_time, rate)                       # credited at funding_time only
TopOfBook(ts_exchange, ts_received, bid, bid_qty, ask, ask_qty)
Snapshot(as_of, bars, funding, book, oi)                       # everything has ts <= as_of (enforced)
Target(strategy, instrument, qty, group_id=None, reason)       # group_id ties carry legs together
Decision(target, verdict: ACCEPT|CLIP|REJECT, rule, detail)
```

| Module | Responsibility | Key interface |
|---|---|---|
| `data/ingest` | Download Binance public archives (klines spot/perp, fundingRate, premiumIndexKlines, metrics/OI [UNVERIFIED availability and start date]), verify `.CHECKSUM`, write parquet partitioned by symbol/month | `ingest(dataset, symbol, start, end)` |
| `data/store` | Point-in-time reads. Every read takes an `as_of` and **can't return rows with availability time > as_of**. | `store.bars(sym, tf, as_of, lookback)` |
| `data/quality` | Gaps, duplicates, zero-volume bars, outlier returns (> 8σ), funding-print gaps. The backtest refuses to run on a failed report. | `quality_report(...) -> Report` |
| `marketdata` (live) | WS streams (book ticker, klines, mark price/funding), REST backfill, sequence-gap and staleness tracking | `latest_snapshot() -> Snapshot \| Stale` |
| `strategies/*` | C1 carry, C2 trend, C3 cascade MR, C4 x-exchange arb (research-only, never wired to live), C5 seasonality (control) | `target(snap, params) -> list[Target]` |
| `sizing` | Volatility targeting (10% ann. portfolio), ¼-Kelly cap, exchange step/min-notional rounding | `size(targets, vol_est, equity)` |
| `risk/gate` | All §4 limits from the spec, plus pair-integrity for carry groups. Pure function. | `check(targets, portfolio, health, limits) -> list[Decision]` |
| `risk/killswitch` | Monitors flag file, CLI, localhost HTTP. Once tripped it stays tripped until a manual `agent unlock`. | `tripped() -> bool` |
| `execution/order_manager` | Diff target vs actual → orders. Post-only limit first, then IOC-limit with a slippage cap. **Never a bare market order** except kill-switch flatten (reduce-only). Idempotent `clientOrderId`. | `rebalance(decisions) -> list[Order]` |
| `execution/brokers` | `SimBroker` (fills from bars + cost model), `PaperBroker` (fills against live book, no orders sent), `BinanceBroker` (ccxt, whitelisted methods) | `Broker` protocol: `place`, `cancel`, `cancel_all`, `positions`, `balances`, `open_orders` |
| `execution/reconciler` | Every loop: exchange positions/orders vs local. Any mismatch → halt + alert. | `reconcile() -> Ok \| Mismatch` |
| `research/engine` | Event-driven replay over the PIT store using the same live objects. Cost model, metrics, parameter sweeps, walk-forward, DSR. | `run(strategy, params, range, cost_mult) -> Result` |
| `research/registry` | Append-only trial log (parquet + git hash) | `record(result)` |
| `ops/log` `ops/metrics` `ops/watchdog` | JSONL decision log, metrics, heartbeat, watchdog | — |

### Carry pair integrity (C1)
Spot and perp legs share a `group_id`. The gate accepts a group or rejects it, never one leg alone.
The order manager places the less-liquid leg first. If the second leg fails, it unwinds the first within N seconds.
A net delta above 2% of group notional for more than 30 s → halt the group and alert.

### Wallet separation (a direct consequence of "no money movement")
On Binance, the spot wallet and the USDⓈ-M futures wallet are separate. Moving collateral between them is a **transfer**, and the agent can't do one.
So the agent **computes** the rebalancing needed (e.g. "move 1,200 USDT spot → futures to keep margin ratio < 30%") and **asks you**.
If you don't act and the margin buffer hits its floor, the gate reduces the carry position instead. Unified/Portfolio Margin accounts would remove
this, but they have eligibility requirements [UNVERIFIED], so they're out of scope for v1.

---

## 4. Process model and timing

| Item | Choice |
|---|---|
| Runtime | Python 3.12+, single asyncio process, a systemd unit with `Restart=on-failure` and a cooldown. After any restart it begins with reconcile-before-trade. |
| Loop | Event-driven on bar close plus a 1 s housekeeping tick (staleness, killswitch, heartbeat) |
| Strategy cadence | C2: daily bar close 00:00 UTC (act at +2 min). C3: hourly close. C1: hourly evaluation; entries/exits avoid the ±5 min around funding times (00/08/16 UTC). |
| State | SQLite (WAL) for orders, fills, positions, risk counters (daily PnL, peak equity). Survives restarts, so the daily-loss and drawdown counters can't be reset by crashing. |
| Clock | NTP-synced host. Drift > 500 ms vs exchange server time → halt (Binance rejects signed requests outside `recvWindow`). |
| Rate limits | Token bucket per endpoint weight, kept at ≤ 50% of the published limit. HTTP 429/418 → back off, count toward API-error halt. |
| Secrets | Env or file with 0600 permissions, never in the repo or logs. Separate keys for the primary and the watchdog. |
| Deploy | One small VM in a region close to Binance's matching engine [ASSUMPTION: AWS ap-northeast-1]. Watchdog on a different provider. |

---

## 5. Libraries (pinned in Phase 3)

| Need | Choice | Why / risk |
|---|---|---|
| Exchange API | `ccxt` (REST + WebSocket) behind our whitelist wrapper | Documented, maintained, Bybit standby for free. Risk: abstraction bugs → covered by the testnet integration suite. Pin the exact version. |
| Data | `pandas`, `numpy`, `pyarrow` | Standard |
| Config | `pydantic` (frozen models) + YAML | Limits validated at load time. Unknown keys are an error. |
| Tests | `pytest`, `hypothesis` (property tests for gate/sizer), `freezegun`-style injected clock | — |
| Stats | Own implementation of DSR / bootstrap CI (tiny, tested against published examples) | Avoids a heavy dependency for ~50 lines |

---

## 6. Failure modes the architecture handles

| Failure | Detection | Response |
|---|---|---|
| WS silently stops | No message > 5 s / seq gap | Stale → no new orders. > 60 s → reduce perp exposure via REST. |
| REST errors / 429 | Error counter | ≥ 5 in 5 min → halt |
| Partial fill on one carry leg | Group delta monitor | Complete or unwind within N s, else halt |
| Process crash | systemd + watchdog heartbeat | Restart → reconcile before trading. Watchdog cancels orders if silent > 60 s. |
| Local/exchange state mismatch | Reconciler each loop | Halt, alert, human resolves |
| Exchange outage / maintenance | Status endpoint + errors | Halt new orders. Positions stay hedged (carry) or protected by resting exchange-side stop orders (C2/C3). |
| Collateral stablecoin depeg | USDT/USDC price feed | > 0.5% → halt entries. > 2% → flatten. |
| Bad config | pydantic validation | Refuse to start |
| Kill switch | Any of 3 inputs | Cancel all → reduce-only flatten → lock |

Exchange-side protective stops are placed for directional strategies (C2/C3) so that a dead bot or a dead host still has its losses capped.

---

## 7. What is explicitly NOT in v1
- Alts, options, cross-exchange live execution (C4 is research-only), leverage > 1× net, maker-rebate strategies, any ML signal.
- Any code path for withdrawals, transfers, conversions, sub-account moves or API-key management.

---

## Gate 2 approval needed
1. Approve the architecture as written, or note changes.
2. **Wallet-transfer workflow:** OK that the agent *requests* spot↔futures transfers and you do them by hand (with automatic position reduction if you don't)?
3. **Data access (still open from Gate 1):** allow `data.binance.vision`, `api.binance.com`, `fapi.binance.com`, `testnet.binancefuture.com`
   (+ `api.bybit.com`) in this cloud environment's network settings, or run research on your own machine?

`[x] Approved by owner — 2026-10-18`: architecture as written; manual transfer workflow accepted; owner is opening network access to the data hosts.
