"""Run the pre-registered sweep: every variant x segment x cost level, in parallel, resumable.

Workers only compute. The main process appends each result to the hash-chained trial registry, so
writes never interleave. A run already in the registry is skipped, so a crashed campaign resumes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from agent.data.store import PITStore
from agent.research.registry import Registry
from agent.research.runner import SEGMENTS, run_variant
from agent.strategies.registry import variants


def _key(strategy: str, params: dict[str, Any], segment: str, cost: float) -> str:
    return json.dumps([strategy, params, segment, float(cost)], sort_keys=True, default=str)


Job = tuple[str, dict[str, Any], str, str, str, float, str, str]


def _work(job: Job) -> dict[str, Any]:
    name, params, segment, start, end, cost, store_path, status_path = job
    return run_variant(
        name,
        params,
        store=PITStore(Path(store_path)),
        quality_status=Path(status_path),
        start=pd.Timestamp(start),
        end=pd.Timestamp(end),
        segment=segment,
        cost_multiplier=cost,
    )


def run_campaign(
    strategies: Iterable[str],
    *,
    store_path: Path,
    status_path: Path,
    registry_path: Path,
    segments: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = SEGMENTS,
    costs: Iterable[float] = (1.0, 2.0, 3.0),
    workers: int = 4,
    limit: int | None = None,
    progress: bool = False,
) -> int:
    """Returns the number of runs performed (excluding ones already recorded)."""
    reg = Registry(registry_path)
    done = {_key(r["strategy"], r["params"], r["segment"], r["cost_multiplier"]) for r in reg.trials()}
    jobs = []
    for name in strategies:
        for params in variants(name)[:limit]:
            for segment, (start, end) in segments.items():
                for cost in costs:
                    if _key(name, params, segment, cost) not in done:
                        jobs.append(
                            (
                                name,
                                params,
                                segment,
                                start.isoformat(),
                                end.isoformat(),
                                float(cost),
                                str(store_path),
                                str(status_path),
                            )
                        )
    if not jobs:
        return 0
    if workers <= 1:
        for i, job in enumerate(jobs, 1):
            reg.append(_work(job))
            if progress:
                print(f"[{i}/{len(jobs)}] {job[0]} {job[1]} {job[2]} x{job[5]}", flush=True)
        return len(jobs)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_work, job): job for job in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            job = futures[fut]
            reg.append(fut.result())  # a failed run raises here and stops the campaign loudly
            if progress:
                print(f"[{i}/{len(jobs)}] {job[0]} {job[1]} {job[2]} x{job[5]}", flush=True)
    return len(jobs)


def results_frame(reg: Registry, strategy: str) -> pd.DataFrame:
    """Flatten registry records: one row per run, param columns + segment/cost + metrics."""
    rows = []
    for r in reg.trials(strategy):
        rows.append(
            {
                **r["params"],
                "segment": r["segment"],
                "cost_multiplier": r["cost_multiplier"],
                "would_lock_at": r.get("would_lock_at"),
                **r["metrics"],
            }
        )
    return pd.DataFrame(rows)
