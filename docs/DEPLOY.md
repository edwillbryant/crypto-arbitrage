# Deploying M7: monitor-only paper trading

**What this runs:** the full live stack, 24/7, against real Binance market data. That's the feed, RiskGate, kill switch, reconciler, paper broker, alerts and watchdog heartbeat, all **with no strategy**: every target is flat and no order is sent anywhere.
**What it proves:** whether the infrastructure stays up, keeps fresh data and halts correctly for ≥ 30 days. It proves nothing about profitability. No strategy passed M4 (`docs/07-HOLDOUT-REVIEW.md`).
**What it needs:** no exchange API keys. Paper trading reads public endpoints only.

## 1. Where it can run

Binance blocks restricted locations (HTTP 451 from this cloud session, see `docs/PROGRESS.md`).

- **Run the primary on a server in a jurisdiction where you are eligible to use Binance.** Check Binance's terms for your country yourself. This repo can't decide it for you.
- **Don't use a VPN or proxy to get around the restriction.** That breaches the terms and can get an account frozen.
- **Primary host:** small VM (1 vCPU, 1 GB RAM is plenty) close to Binance [ASSUMPTION: AWS ap-northeast-1 / Tokyo].
- **Watchdog host:** a second VM at a **different provider**, so one provider's outage can't take out both.
- **Clock:** both hosts NTP-synced (`timedatectl` shows `System clock synchronized: yes`). The gate halts at > 500 ms drift.

Check eligibility from the primary before installing anything:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://fapi.binance.com/fapi/v1/time   # must print 200
curl -s -o /dev/null -w "%{http_code}\n" https://api.binance.com/api/v3/time     # must print 200
```

## 2. Install (both hosts)

```bash
sudo useradd --system --home /opt/agent --shell /usr/sbin/nologin agent
sudo mkdir -p /opt/agent /etc/agent && sudo chown agent:agent /opt/agent
sudo -u agent git clone https://github.com/edwillbryant/crypto-arbitrage /opt/agent/app
sudo -u agent python3.12 -m venv /opt/agent/venv
sudo -u agent /opt/agent/venv/bin/pip install -e /opt/agent/app
sudo -u agent mkdir -p /opt/agent/app/data/state
cd /opt/agent/app && sudo -u agent /opt/agent/venv/bin/python -m pytest -q   # offline suite must pass here too
```

Generate the heartbeat secret once and put the **same** value on both hosts:

```bash
openssl rand -hex 32
```

## 3. Primary host

1. `sudo cp deploy/agent.env.example /etc/agent/agent.env && sudo chmod 600 /etc/agent/agent.env`, then fill in `AGENT_WATCHDOG_SECRET` (and optionally `AGENT_ALERT_WEBHOOK`).
2. In `config/paper.yaml`, set `watchdog_url: http://<watchdog-host>:8787`. Keep `monitor_only: true` and `strategies: []`.
3. Smoke test, three cycles in the foreground:
   ```bash
   sudo -u agent --preserve-env=AGENT_WATCHDOG_SECRET /opt/agent/venv/bin/python -m agent run --mode paper --max-cycles 3
   tail -n 3 data/state/paper/decisions.jsonl     # each cycle: mode NORMAL, no orders
   cat data/state/paper/alerts.jsonl              # "paper service started", watchdog=on
   ```
   If the mode isn't `NORMAL`, the `triggers` field says why (stale book, clock drift, depeg ...). Fix that first.
4. Install the unit:
   ```bash
   sudo cp deploy/agent-paper.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now agent-paper
   journalctl -u agent-paper -f
   ```

## 4. Watchdog host

1. Fill in `/etc/agent/watchdog.env` from `deploy/watchdog.env.example` (same secret), mode 600.
2. **Firewall:** allow TCP 8787 **only from the primary's IP**. Heartbeats are signed and rejected if older than 30 s, so a forged or replayed one fails. The firewall still keeps everyone else off the port.
3. Install the unit:
   ```bash
   sudo cp deploy/agent-watchdog.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now agent-watchdog
   curl -s http://127.0.0.1:8787/status    # last_heartbeat updates every cycle once the primary is up
   ```

**In M7 the watchdog can only page you.** Paper positions exist only on the primary, so there's nothing on an exchange for it to flatten. When live trading is approved (Gate 6), it gets its own trade-only key and flattens through `BinanceBroker`.

## 5. Drills, before the 30-day clock starts

Do each drill once and record the result in `docs/PROGRESS.md`. Every drill must produce an alert.

| Drill | How | Expected |
|---|---|---|
| Kill switch, flag file | `sudo -u agent touch data/state/paper/KILL` | Next cycle `FLATTEN`, alert `mode FLATTEN`; it stays locked after you delete the file |
| Kill switch, HTTP | `curl -X POST http://127.0.0.1:8765/kill` on the primary | Same |
| Unlock (human only) | `python -m agent unlock --state data/state/paper/risk.db --flag data/state/paper/KILL --confirm --reason "drill"` | Back to `NORMAL`, alert |
| Primary dies | `sudo systemctl kill -s KILL agent-paper` and wait 70 s | Watchdog alert `primary silent: watchdog fired`; systemd restarts the primary; heartbeats resume |
| Network loss | Block outbound 443 on the primary for 2 min | Stale data → `HALT`/`FLATTEN` alert, back to `NORMAL` after |
| Restart keeps the risk state | `sudo systemctl restart agent-paper` while locked | Still locked after the restart |

## 6. Daily operation and pass criteria (M7, ≥ 30 days)

- **Daily:** `data/state/paper/daily/<date>.json` has cycles per mode, errors and crashes. With 60 s cycles, a full day is about 1,440 cycles.
- **Pass:**
  - cycles ≥ 98% of expected
  - zero crashes left unexplained
  - every non-NORMAL period explained by a real cause (Binance maintenance, network)
  - every watchdog page was a real outage
  - no reconciliation mismatch
- **Not a pass criterion:** PnL. With no strategy, paper PnL is zero by construction.

## 7. Before anything trades real money (Gate 6, needs your explicit approval)

None of this applies to M7. It's listed here so nobody skips it later.

- **Dedicated Binance sub-account** holding only the capital you assign. On a shared account, the orphan rule would sell your other holdings.
- **API key settings:** reading and spot/futures trading only. **Withdrawals off. Universal transfer off.** IP whitelist set to the primary's IP only.
- **A separate trade-only key for the watchdog,** whitelisted to the watchdog's IP.
- Keys go in `/etc/agent/*.env` (mode 600), never in the repo, never in logs (the decision log redacts them).
- **Spot↔futures transfers stay manual.** The agent asks; you move the funds.
- Start at the smallest size the exchange allows.
