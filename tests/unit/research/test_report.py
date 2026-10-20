from __future__ import annotations

from pathlib import Path

from agent.research.registry import Registry
from agent.research.report import build_report


def _rec(n: int, k: float, segment: str, cost: float, *, sharpe: float, pnl: float) -> dict[str, object]:
    return {
        "strategy": "c2_trend",
        "params": {"n": n, "k_atr": k},
        "segment": segment,
        "cost_multiplier": cost,
        "would_lock_at": "2021-05-19T00:00:00+00:00" if sharpe < 0.3 else None,
        "metrics": {
            "sharpe": sharpe,
            "net_pnl": pnl,
            "max_drawdown": 0.12,
            "trades": 40,
            "win_rate": 0.45,
            "profit_factor": 1.3,
            "costs_over_gross": 0.2,
            "sr_per_period": sharpe / 19.1,
            "n_obs": 1400,
            "skew": -0.5,
            "kurt": 6.0,
            "total_return": pnl / 10_000,
        },
    }


def test_report_states_verdict_choice_and_dsr(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "trials.jsonl")
    for n in (20, 50, 100, 200):
        for k in (2.0, 3.0, 4.0, 5.0):
            sr = 0.6 if n in (50, 100) else 0.2
            for cost, pnl in ((1.0, 900.0), (2.0, 400.0), (3.0, -50.0)):
                reg.append(_rec(n, k, "train", cost, sharpe=sr - 0.1 * (cost - 1), pnl=pnl))
                reg.append(_rec(n, k, "validation", cost, sharpe=0.3, pnl=pnl / 4))
    md = build_report(reg, ["c2_trend"], out_dir=tmp_path / "results")
    assert "## c2_trend" in md and "PROPOSE" in md
    assert "Deflated Sharpe" in md and "would have locked" in md
    assert "| validation | 3x |" in md
    assert (tmp_path / "results" / "c2_trend.csv").exists()


def test_report_handles_a_strategy_with_no_runs(tmp_path: Path) -> None:
    md = build_report(Registry(tmp_path / "t.jsonl"), ["c1_carry"], out_dir=tmp_path / "r")
    assert "no runs recorded" in md


def test_never_locked_is_shown_as_never(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "trials.jsonl")
    for n in (20, 50, 100, 200):
        for k in (2.0, 3.0, 4.0, 5.0):
            for cost in (1.0, 2.0, 3.0):
                for seg in ("train", "validation"):
                    rec = _rec(n, k, seg, cost, sharpe=0.6, pnl=500.0)
                    rec["would_lock_at"] = None
                    reg.append(rec)
    md = build_report(reg, ["c2_trend"], out_dir=tmp_path / "r")
    assert "nan" not in md.split("Chosen cell")[1]
    assert "| never |" in md
