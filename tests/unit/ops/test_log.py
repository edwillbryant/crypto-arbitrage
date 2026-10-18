from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from agent.ops.log import DecisionLog


def test_one_json_line_per_record_with_secrets_redacted(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "decisions.jsonl", secrets=["s3cr3t-key-value"])
    log.write({"time": pd.Timestamp("2024-01-01", tz="UTC"), "apiKey": "abc", "nested": {"secret": "xyz"},
               "note": "error from exchange echoing s3cr3t-key-value back", "weight": 0.25})  # fmt: skip
    log.write({"n": 2})
    lines = (tmp_path / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    text = lines[0]
    assert "s3cr3t-key-value" not in text and "abc" not in text and "xyz" not in text
    assert rec["weight"] == 0.25 and rec["apiKey"] == "[REDACTED]"
    assert rec["time"].startswith("2024-01-01")
