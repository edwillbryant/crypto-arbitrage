# M4 hold-out review (2026-10-21)

Generated results: `docs/07-HOLDOUT-RESULTS.md`. Protocol: `docs/06-HOLDOUT-PROTOCOL.md`, pre-registered and committed
(6a983a3) before any hold-out data was read. Locks were committed before each run (cb5567f). Each hold-out ran exactly once.

## Outcome: no strategy passes

| Strategy | Verdict | Why |
|---|---|---|
| C1 funding carry | **REJECT** | Only 2 trades in 21 months. Funding stayed below the 20% APR entry threshold almost all the time, as M3b's regime split predicted (2022–23 was already ~1%/yr). Net −$11 on $10k: $10 of funding against $16 of fees. |
| C2 trend | **REJECT** | Sharpe −0.27, net −$297, negative on BTC (−0.26) and ETH (−0.11). With 11 trades it would also have been insufficient evidence. At 3× costs, the production 8% drawdown lock would have fired on 2026-09-03. |

## What this tells us

1. **The process worked.** Both strategies looked fine on 2020–2024 (C1's train Sharpe was 3.5) and failed on data they hadn't seen. This is the outcome the validation exists to catch, and catching it on paper costs nothing.
2. **C1's edge is real but episodic.** It pays in leverage booms (2020–21, early 2024) and sits idle otherwise. A lower entry threshold would trade more, but tuning it now would use the hold-out to select parameters. That's exactly what the protocol forbids.
3. **C2's 2020–2024 profit didn't persist into 2025–26**, a choppy, range-bound market (spec §1 regime notes). Its trade count was always going to be too low to prove anything.

## What must NOT happen next

- Re-running, re-tuning or re-gridding C1/C2 on the hold-out. **The 2025–26 data has now been seen for these designs.** Any new variant tested on it would be in-sample.
- Relaxing the verdict rules after the fact.

## Options (owner decision)

1. **Stop strategy research here and finish the infrastructure.** Build M6b (live Binance feed and broker, testnet suite) on a server in an eligible jurisdiction, and paper trade with **all strategies off**. That tests the plumbing, risk gate and kill switch end to end. This was the plan's stated fallback.
2. **Start a new research round** with new hypotheses, a new pre-registration, and **forward data as the hold-out**. The 2025–26 period is no longer clean for anything derived from what was learned here. In practice that means paper-trading new candidates for months before any verdict.
3. **Shelve the project.** A legitimate outcome: the system did its job by refusing to trade without an edge.
