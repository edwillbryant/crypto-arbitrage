from __future__ import annotations

import pandas as pd
import pytest

from agent.research.sweep import grid, neighbour_stats


def test_grid_is_cartesian_and_ordered() -> None:
    g = grid({"a": [1, 2], "b": [10, 20, 30]})
    assert len(g) == 6
    assert g[0] == {"a": 1, "b": 10}
    assert g[-1] == {"a": 2, "b": 30}


def test_neighbour_stats_on_a_hand_made_grid() -> None:
    # 3x3 grid of Sharpe values; centre is a lone peak surrounded by negatives except one.
    vals = {(1, 1): -0.2, (1, 2): -0.1, (1, 3): 0.3, (2, 1): -0.3, (2, 2): 2.0, (2, 3): -0.1,
            (3, 1): -0.2, (3, 2): -0.4, (3, 3): -0.5}  # fmt: skip
    df = pd.DataFrame([{"a": a, "b": b, "sharpe": s} for (a, b), s in vals.items()])
    st = neighbour_stats(df, ["a", "b"], "sharpe").set_index(["a", "b"])
    centre = st.loc[(2, 2)]
    assert centre["n_neighbours"] == 8
    assert centre["frac_positive"] == pytest.approx(1 / 8)
    corner = st.loc[(1, 1)]
    assert corner["n_neighbours"] == 3
    assert corner["frac_positive"] == pytest.approx(1 / 3)  # (2,2) positive; (1,2), (2,1) not


def test_neighbour_stats_requires_full_grid_columns() -> None:
    with pytest.raises(KeyError):
        neighbour_stats(pd.DataFrame({"a": [1]}), ["a"], "sharpe")
