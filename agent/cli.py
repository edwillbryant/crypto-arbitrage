"""Command-line entry point: `python -m agent ...`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from agent.data.datasets import FUNDING, METRICS, PREMIUM_1H, Dataset, klines
from agent.data.ingest import ingest
from agent.data.quality import quality_report, render_markdown
from agent.data.store import PITStore
from agent.live.config import DEFAULT_RULES, load_paper_config
from agent.risk.killswitch import KillSwitch
from agent.risk.ledger import RiskLedger

STATE_DB = Path("data/state/risk.db")
KILL_FLAG = Path("data/state/KILL")

DEFAULT_DATASETS: dict[str, Dataset] = {
    d.name: d
    for d in (
        klines("spot", "1m"),
        klines("spot", "1h"),
        klines("spot", "1d"),
        klines("perp", "1m"),
        klines("perp", "1h"),
        klines("perp", "1d"),
        FUNDING,
        PREMIUM_1H,
        METRICS,
    )
}


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _cmd_ingest(args: argparse.Namespace) -> int:
    store = PITStore(args.store)
    names = args.datasets.split(",") if args.datasets else list(DEFAULT_DATASETS)
    failed = False
    for name in names:
        dataset = DEFAULT_DATASETS[name]
        for symbol in args.symbols.split(","):
            rep = ingest(store, dataset, symbol, args.start, args.end, raw_dir=args.raw)
            print(
                f"{dataset.name:<24} {symbol:<8} files={len(rep.files_ok):>4} rows+={rep.rows_written:>9,} "
                f"missing={len(rep.missing)} unverified={len(rep.unverified)} "
                f"dup_rows_dropped={rep.duplicates_dropped}"
            )
            for f in rep.missing + rep.unverified:
                print(f"    not ingested: {f.path}")
            failed |= bool(rep.unverified)
    return 1 if failed else 0


def _cmd_quality(args: argparse.Namespace) -> int:
    store = PITStore(args.store)
    reports = []
    for dataset in DEFAULT_DATASETS.values():
        root = store.root / dataset.name
        symbols = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
        reports += [quality_report(dataset, s, store.scan_full_history(dataset, s)) for s in symbols]
    if not reports:
        print("store is empty: run `python -m agent data ingest` first", file=sys.stderr)
        return 1
    args.out.write_text(render_markdown(reports))
    status = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "datasets": {f"{r.dataset}:{r.symbol}": "PASS" if r.passed else "FAIL" for r in reports},
    }
    args.status.parent.mkdir(parents=True, exist_ok=True)
    args.status.write_text(json.dumps(status, indent=1, sort_keys=True))
    bad = [f"{r.dataset} {r.symbol}" for r in reports if not r.passed]
    print(f"wrote {args.out}: {len(reports) - len(bad)} passed, {len(bad)} failed")
    for b in bad:
        print(f"    FAIL {b}")
    return 1 if bad else 0


def _ledger(args: argparse.Namespace) -> RiskLedger:
    args.state.parent.mkdir(parents=True, exist_ok=True)
    return RiskLedger(args.state)


def _cmd_kill(args: argparse.Namespace) -> int:
    with _ledger(args) as led:
        KillSwitch(led, args.flag).trip(pd.Timestamp.now(tz="UTC"), source="cli", reason=args.reason)
    print("KILL SWITCH TRIPPED: trading is locked; the running agent will cancel all orders and flatten.")
    return 0


def _cmd_unlock(args: argparse.Namespace) -> int:
    if not args.confirm or not args.reason:
        print(
            "refusing: unlock requires --confirm and --reason (it also resets the drawdown peak)",
            file=sys.stderr,
        )
        return 2
    try:
        with _ledger(args) as led:
            KillSwitch(led, args.flag).reset(pd.Timestamp.now(tz="UTC"), reason=args.reason)
    except ValueError as e:
        print(f"refusing: {e}", file=sys.stderr)
        return 2
    print("unlocked; the drawdown peak was reset to current equity")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    from agent.research.campaign import run_campaign  # noqa: PLC0415

    n = run_campaign(
        args.strategies.split(","),
        store_path=args.store,
        status_path=args.status,
        registry_path=args.registry,
        workers=args.workers,
        progress=True,
    )
    print(f"done: {n} new runs recorded in {args.registry}")
    return 0


def _cmd_diagnose(args: argparse.Namespace) -> int:
    from agent.research.registry import Registry  # noqa: PLC0415
    from agent.research.runner import DIAGNOSTIC, run_variant  # noqa: PLC0415
    from agent.strategies.registry import variants  # noqa: PLC0415

    params = json.loads(args.params)
    if params not in variants(args.strategy):
        print(f"refusing: {params} is not a pre-registered variant of {args.strategy}", file=sys.stderr)
        return 2
    reg = Registry(args.registry)
    for cost in (float(c) for c in args.costs.split(",")):
        rec = run_variant(
            args.strategy,
            params,
            store=PITStore(args.store),
            quality_status=args.status,
            start=pd.Timestamp(args.start, tz="UTC"),
            end=pd.Timestamp(args.end, tz="UTC"),
            segment=DIAGNOSTIC,
            cost_multiplier=cost,
        )
        reg.append(rec)
        m, b = rec["metrics"], rec["pnl_breakdown"]
        print(
            f"{args.strategy} {params} {args.start}->{args.end} x{cost:g}: sharpe {m['sharpe']:.2f} "
            f"maxDD {m['max_drawdown']:.1%} trades {m['trades']} net {m['net_pnl']:,.0f} "
            f"= price {b['price']:,.0f} "
            f"+ funding {b['funding']:,.0f} - fees {b['fees']:,.0f}  lock {rec['would_lock_at'] or 'never'}"
        )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from agent.research.registry import Registry  # noqa: PLC0415
    from agent.research.report import build_report  # noqa: PLC0415

    reg = Registry(args.registry)
    reg.verify()
    args.out.write_text(build_report(reg, args.strategies.split(","), out_dir=args.csv_dir))
    print(f"wrote {args.out}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if args.replay:
        return _replay(args)
    cfg = load_paper_config(args.config)
    if not cfg.strategies:
        why = "each needs a hold-out lock and owner approval"
        print(f"refusing: {args.config} enables no strategies ({why})", file=sys.stderr)
        return 2
    print(
        "refusing: the live Binance market-data feed is not built yet (M6b, needs network access)",
        file=sys.stderr,
    )
    return 2


def _replay(args: argparse.Namespace) -> int:
    """Run a strategy through the full live stack over stored history. A smoke test, not validation."""
    from agent.execution.hedge import HedgeMonitor  # noqa: PLC0415 - keep CLI start-up light
    from agent.execution.order_manager import OrderManager  # noqa: PLC0415
    from agent.execution.paper import PaperBroker  # noqa: PLC0415
    from agent.execution.reconciler import Reconciler  # noqa: PLC0415
    from agent.live.feed import StoreFeed  # noqa: PLC0415
    from agent.live.loop import LiveLoop  # noqa: PLC0415
    from agent.ops.log import DecisionLog  # noqa: PLC0415
    from agent.risk.gate import RiskGate  # noqa: PLC0415
    from agent.risk.limits import load_limits  # noqa: PLC0415
    from agent.sizing.voltarget import VolTargetSizer  # noqa: PLC0415
    from agent.strategies.registry import REGISTRY, variants  # noqa: PLC0415

    if args.strategy not in REGISTRY:
        print(f"unknown strategy {args.strategy!r}; choose from {sorted(REGISTRY)}", file=sys.stderr)
        return 2
    strategy = REGISTRY[args.strategy].build(variants(args.strategy)[0])
    state = args.state_dir
    state.mkdir(parents=True, exist_ok=True)
    limits, digest = load_limits(args.limits)
    feed = StoreFeed(PITStore(args.store))
    broker = PaperBroker(
        cash=args.cash, rules=DEFAULT_RULES, book=feed.book, taker_fee_bps={"perp": 5.0, "spot": 10.0}
    )
    ledger = RiskLedger(state / "risk.db")
    gate = RiskGate(limits, ledger, killswitch=KillSwitch(ledger, state / "KILL"))
    loop = LiveLoop(
        strategy=strategy,
        feed=feed,
        broker=broker,
        gate=gate,
        sizer=VolTargetSizer(lookback_bars=24 * 7),
        orders=OrderManager(broker, on_error=lambda k, d: None),
        reconciler=Reconciler(DEFAULT_RULES),
        hedge=HedgeMonitor(),
        ledger=ledger,
        log=DecisionLog(state / "decisions.jsonl"),
        heartbeat_path=state / "heartbeat",
    )
    start, end = pd.Timestamp(args.start, tz="UTC"), pd.Timestamp(args.end, tz="UTC")
    print(f"REPLAY (not validated, not paper trading): {strategy.name} {start} -> {end}")
    print(f"limits sha256 {digest[:12]}")
    modes: dict[str, int] = {}
    rep = None
    for t in pd.date_range(start, end, freq=strategy.decision_interval, inclusive="left"):
        rep = loop.step(t)
        modes[rep.mode.name] = modes.get(rep.mode.name, 0) + 1
    ledger.close()
    print(f"cycles by mode: {modes}; final equity {rep.equity if rep else args.cash:,.2f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent")
    sub = p.add_subparsers(dest="group", required=True)
    _data_commands(sub)
    _research_commands(sub)
    _run_and_safety_commands(sub)
    return p


def _data_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    data = sub.add_parser("data", help="historical data").add_subparsers(dest="cmd", required=True)

    ing = data.add_parser("ingest", help="download and verify Binance public archives")
    ing.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    ing.add_argument("--datasets", help=f"comma list from: {','.join(DEFAULT_DATASETS)}")
    ing.add_argument("--from", dest="start", type=_date, default=dt.date(2020, 1, 1))
    ing.add_argument("--to", dest="end", type=_date, default=dt.date(2026, 9, 30))
    ing.add_argument("--store", type=Path, default=Path("data/store"))
    ing.add_argument("--raw", type=Path, default=Path("data/raw"))
    ing.set_defaults(func=_cmd_ingest)

    q = data.add_parser("quality", help="write the data-quality report")
    q.add_argument("--store", type=Path, default=Path("data/store"))
    q.add_argument("--out", type=Path, default=Path("docs/data_quality.md"))
    q.add_argument("--status", type=Path, default=Path("data/quality_status.json"))
    q.set_defaults(func=_cmd_quality)


def _research_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    research = sub.add_parser("research", help="pre-registered sweeps (docs/04-RESEARCH-PROTOCOL.md)")
    rsub = research.add_subparsers(dest="cmd", required=True)
    sw = rsub.add_parser(
        "sweep", help="run every variant on train + validation at 1x/2x/3x costs (resumable)"
    )
    sw.add_argument("--strategies", default="c1_carry,c2_trend,c3_cascade,c5_seasonality")
    sw.add_argument("--workers", type=int, default=4)
    sw.add_argument("--store", type=Path, default=Path("data/store"))
    sw.add_argument("--status", type=Path, default=Path("data/quality_status.json"))
    sw.add_argument("--registry", type=Path, default=Path("research/trials.jsonl"))
    sw.set_defaults(func=_cmd_sweep)
    dg = rsub.add_parser(
        "diagnose", help="extra recorded runs of an already-chosen cell (e.g. a regime split)"
    )
    dg.add_argument("--strategy", required=True)
    dg.add_argument("--params", required=True, help="JSON, must be a registered variant")
    dg.add_argument("--from", dest="start", required=True)
    dg.add_argument("--to", dest="end", required=True)
    dg.add_argument("--costs", default="1,2,3")
    dg.add_argument("--store", type=Path, default=Path("data/store"))
    dg.add_argument("--status", type=Path, default=Path("data/quality_status.json"))
    dg.add_argument("--registry", type=Path, default=Path("research/trials.jsonl"))
    dg.set_defaults(func=_cmd_diagnose)
    rp = rsub.add_parser("report", help="apply the pre-registered selection and write the results report")
    rp.add_argument("--strategies", default="c1_carry,c2_trend,c3_cascade,c5_seasonality")
    rp.add_argument("--registry", type=Path, default=Path("research/trials.jsonl"))
    rp.add_argument("--out", type=Path, default=Path("docs/05-SWEEP-RESULTS.md"))
    rp.add_argument("--csv-dir", type=Path, default=Path("research/results"))
    rp.set_defaults(func=_cmd_report)


def _run_and_safety_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    r = sub.add_parser("run", help="run the agent (paper only; there is no live mode until Gate 6)")
    r.add_argument("--mode", choices=["paper"], required=True)
    r.add_argument("--config", type=Path, default=Path("config/paper.yaml"))
    r.add_argument("--replay", action="store_true", help="replay stored history through the live stack")
    r.add_argument(
        "--strategy", default="c1_carry", help="replay only: registered strategy (first grid variant)"
    )
    r.add_argument("--store", type=Path, default=Path("data/store"))
    r.add_argument("--from", dest="start", default="2024-01-01")
    r.add_argument("--to", dest="end", default="2024-02-01")
    r.add_argument("--cash", type=float, default=10_000.0)
    r.add_argument("--limits", type=Path, default=Path("config/limits.yaml"))
    r.add_argument("--state-dir", type=Path, default=Path("data/state/replay"))
    r.set_defaults(func=_cmd_run)

    for name, func, help_ in (
        ("kill", _cmd_kill, "trip the kill switch: cancel all, flatten, lock"),
        ("unlock", _cmd_unlock, "release the lock (human only)"),
    ):
        c = sub.add_parser(name, help=help_)
        c.add_argument("--state", type=Path, default=STATE_DB)
        c.add_argument("--flag", type=Path, default=KILL_FLAG)
        c.add_argument("--reason", default="manual" if name == "kill" else None)
        if name == "unlock":
            c.add_argument("--confirm", action="store_true")
        c.set_defaults(func=func)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code
