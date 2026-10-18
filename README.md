# crypto-arbitrage → autonomous trading agent

A 24/7 crypto trading agent (Binance USDⓈ-M perps + spot, BTC/ETH) that trades only strategies that pass
chronological hold-out validation, with hard risk limits enforced in code and no ability to move funds.

Status: **under construction**. Nothing here trades yet, and no strategy has been validated.

- Spec, architecture and plan (all approved): `docs/01-SPEC.md`, `docs/02-ARCHITECTURE.md`, `docs/03-PLAN.md`
- Old Bittrex arbitrage scripts (blog material, Bittrex has since shut down): `legacy/`

```bash
make install   # pip install -e ".[dev]"
make check     # ruff + mypy + pytest
```
