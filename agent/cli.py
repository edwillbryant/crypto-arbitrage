"""Command-line entry point: `python -m agent ...`."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path

from agent.data.datasets import FUNDING, METRICS, PREMIUM_1H, Dataset, klines
from agent.data.ingest import ingest
from agent.data.quality import quality_report, render_markdown
from agent.data.store import PITStore

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
                f"missing={len(rep.missing)} unverified={len(rep.unverified)}"
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
    bad = [f"{r.dataset} {r.symbol}" for r in reports if not r.passed]
    print(f"wrote {args.out}: {len(reports) - len(bad)} passed, {len(bad)} failed")
    for b in bad:
        print(f"    FAIL {b}")
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent")
    sub = p.add_subparsers(dest="group", required=True)
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
    q.set_defaults(func=_cmd_quality)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code
