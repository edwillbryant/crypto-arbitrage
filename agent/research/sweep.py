"""Parameter grids and the neighbourhood-robustness check (spec §3: reject lone peaks)."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd


def grid(space: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = list(space)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*(space[k] for k in keys))]


def sweep(
    evaluate: Callable[[dict[str, Any]], dict[str, Any]], space: Mapping[str, Sequence[Any]]
) -> pd.DataFrame:
    """Run `evaluate(params) -> metrics` over the grid. Recording to the registry is the caller's job."""
    return pd.DataFrame([{**p, **evaluate(p)} for p in grid(space)])


def neighbour_stats(df: pd.DataFrame, params: list[str], metric: str) -> pd.DataFrame:
    """For every grid cell: how many adjacent cells (Chebyshev distance 1 in grid-index space) exist,
    the fraction of them with metric > 0, and their median."""
    values = df[metric].to_numpy(dtype="float64")
    idx = np.column_stack(
        [df[p].map({v: i for i, v in enumerate(sorted(df[p].unique()))}).to_numpy() for p in params]
    )
    rows = []
    for i in range(len(df)):
        dist = np.abs(idx - idx[i]).max(axis=1)
        nb = dist == 1
        vals = values[nb]
        rows.append({
            "n_neighbours": int(nb.sum()),
            "frac_positive": float((vals > 0).mean()) if len(vals) else float("nan"),
            "neighbour_median": float(np.median(vals)) if len(vals) else float("nan"),
        })  # fmt: skip
    return pd.concat([df[params].reset_index(drop=True), pd.DataFrame(rows)], axis=1)
