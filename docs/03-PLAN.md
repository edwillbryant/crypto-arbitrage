# Phase 3 — PLAN

**Status:** APPROVED 2026-10-18 (Gate 3 of 6)
**Inputs:** `01-SPEC.md` (approved), `02-ARCHITECTURE.md` (approved)
**Date:** 2026-10-18

Work proceeds test-first: for every module, the failing tests are written and committed **before** the implementation.
Each milestone ends with a short report (what was built, test results, anything that surprised us). Milestones M3 and
M7 are **owner checkpoints**: nothing after them starts without your OK.

---

## 1. Repo structure

The legacy scripts (`bittrex_*.py`, `exchange_compare.py`) move to `legacy/` untouched. Bittrex shut down in 2023, so they're kept only as history.

```
pyproject.toml            # pinned deps, ruff, mypy --strict on agent/risk and agent/execution
agent/
  __main__.py  cli.py      # `python -m agent ...`
  core/        types.py clock.py config.py errors.py
  data/        ingest.py store.py quality.py
  marketdata/  feed.py staleness.py
  strategies/  base.py c1_carry.py c2_trend.py c3_cascade.py c4_xarb.py c5_seasonality.py
  sizing/      voltarget.py
  risk/        gate.py limits.py killswitch.py
  execution/   order_manager.py reconciler.py brokers/{sim,paper,binance}.py exchange_whitelist.py
  research/    engine.py costs.py metrics.py sweep.py walkforward.py dsr.py registry.py report.py
  ops/         log.py metrics.py watchdog.py alerts.py
config/        limits.yaml paper.yaml research.yaml
research/      trials.jsonl (append-only, hash-chained)  holdout_locks/ (frozen params + git hash, one file per strategy)
data/          (gitignored) parquet store
tests/         unit/ property/ leakage/ integration/ fixtures/
legacy/        old Bittrex scripts
docs/          01-SPEC … 06-SHIP
```

---

## 2. Milestones

| # | Milestone | Done when (acceptance) | Needs network |
|---|---|---|---|
| **M0** | Scaffold | `pyproject`, CI-style `make check` (ruff + mypy + pytest) passes on an empty suite; legacy moved | No |
| **M1** | **Data layer** (first module) | Ingest BTCUSDT/ETHUSDT spot + perp 1m/1h/1d klines, funding, premium index, OI metrics (2020-01 → 2026-09) with checksum verification; PIT store; quality report on the full range committed to `docs/data_quality.md` | **Yes** |
| **M2** | Research engine + cost model | Engine replays the PIT store through Strategy → Sizer → RiskGate → SimBroker. Metrics module matches hand-computed values on fixtures. DSR matches the worked example in Bailey & López de Prado (2014). | No (fixtures) |
| **M3** | **Strategies + sweeps on train/validation** ⛳ owner checkpoint | C1–C5 implemented; parameter grids from the spec swept on 2020–2024 only; every run in `trials.jsonl`; neighbourhood heatmaps; cost 1×/2×/3×. You see the results and approve which parameters get **locked** for hold-out. | Yes |
| **M4** | Hold-out run | For each strategy: lock file committed (params + git hash) **before** a single hold-out run; results + DSR + bootstrap CIs; comparison table in the spec filled with real numbers; verdicts PASS / REJECT / INSUFFICIENT EVIDENCE | Yes |
| **M5** | RiskGate + kill switch | Full gate test suite green (see §3) before any broker code exists | No |
| **M6** | Execution: OrderManager, PaperBroker, reconciler, whitelist wrapper | Paper-mode loop runs against the live Binance feed and testnet; integration suite green on testnet | Yes (testnet) |
| **M7** | **Paper trading start** ⛳ owner checkpoint | Only strategies with a PASS verdict that you approved are enabled. 30-day minimum. | Yes |
| M8 | Review (Gate 5) | Adversarial review, plus paper-vs-backtest drift report | — |
| M9 | Ship (Gate 6) | Live at minimum size **only** with your explicit approval, a separate live config and a manual confirmation prompt | — |

If M4 produces no PASS, the plan stops at M5–M6. The infrastructure still gets built and paper-tested with the strategies
switched off. We don't loosen the filters to make something pass.

---

## 3. Test suite

| Suite | Examples (not exhaustive) |
|---|---|
| **leakage/** | Store never returns `close_time > as_of` (property test over random `as_of`); bar at `t` only fills at `t+1` open; funding credited only if the position was held at `funding_time`; rolling indicators unchanged if future rows are appended ("future-poisoning" test: replace all data after `t` with NaN/garbage → signals at ≤ `t` identical) |
| **unit/data** | Checksum mismatch rejects a file; gap and duplicate detection; timezone is always UTC; monthly + daily archive overlap deduped |
| **unit/research** | Fees and slippage applied on every fill; 2× cost doubles the cost line exactly; Sharpe, max DD, profit factor and win rate against hand-computed fixtures; DSR reference values; registry is append-only |
| **unit/strategies** | Each strategy: known input → known target; flat when data is stale; C1 hysteresis; C1 never emits an unpaired leg |
| **property/risk** (hypothesis) | For any random target/portfolio: output never exceeds the per-symbol or gross limits; daily-loss breach → only reducing orders pass; drawdown breach → only flatten, then locked; carry groups all-or-nothing; limits object is immutable |
| **unit/risk** | Kill switch via file, CLI and HTTP; it survives a restart (persisted); unlock requires the explicit command; risk counters persisted in SQLite and not reset by a restart |
| **unit/execution** | Target diff → orders rounded to step/tick/min-notional; no bare market orders except reduce-only flatten; idempotent clientOrderId; reconciler mismatch → halt |
| **security/** | Exchange wrapper exposes no withdraw/transfer/convert/sub-account methods; calling one through the raw ccxt object raises; secrets never appear in log output (scan test) |
| **integration/** (testnet, opt-in marker) | Place/cancel/fill on Binance futures testnet; WS disconnect → stale → halt; 429 handling; clock-drift halt; full kill-switch flatten |

Target coverage: 100% branch coverage on `agent/risk/`, ≥ 90% on `agent/execution/` and `agent/data/store.py`.

---

## 4. First module: `agent/data`

Why it comes first: every claim this system makes rests on the data being clean and free of look-ahead.

Order of work:
1. `tests/leakage/test_store_pit.py` and `tests/unit/data/test_quality.py`, written and failing.
2. `core/types.py`, `core/clock.py`.
3. `data/store.py`, the point-in-time reader over parquet.
4. `data/ingest.py`, the data.binance.vision downloader with `.CHECKSUM` verification and a REST top-up for the latest days.
5. `data/quality.py`, then run it over the full range and commit the report.

Steps 1–3 can be done now on synthetic fixtures. Steps 4–5 need the network to be open. The fixtures exist only to test code; no results are ever computed on them.

---

## 5. Pinned dependencies (exact versions confirmed at M0)
`python>=3.12`, `pandas`, `numpy`, `pyarrow`, `pydantic>=2`, `pyyaml`, `ccxt`, `pytest`, `hypothesis`, `ruff`, `mypy`.
Versions are pinned to releases at least two weeks old; `uv.lock` or `requirements.lock` is committed.

---

## 6. Commands (target interface)

```bash
make check                                            # lint + types + tests
python -m agent data ingest --symbols BTCUSDT,ETHUSDT --from 2020-01-01 --to 2026-09-30
python -m agent data quality                          # writes docs/data_quality.md
python -m agent research sweep --strategy c2_trend --range train
python -m agent research lock --strategy c2_trend --params <file>   # writes holdout lock + git hash
python -m agent research holdout --strategy c2_trend                # refuses without a lock file
python -m agent run --mode paper --config config/paper.yaml         # paper trading
python -m agent kill                                                # flatten + lock
python -m agent unlock --confirm
```

`--mode live` doesn't exist until Gate 6.

---

## Gate 3 approval needed
1. Approve the plan and milestone order, including the two owner checkpoints (M3 parameter lock, M7 paper start).
2. Network: the data hosts **still aren't reachable from this session** (checked just now). Changes to the environment may only apply to a new session.
   I can start M0 and M1 steps 1–3 now, and M1 steps 4–5 once the hosts respond.

`[x] Approved by owner — 2026-10-18`: plan and both owner checkpoints approved; start M0 + M1.
