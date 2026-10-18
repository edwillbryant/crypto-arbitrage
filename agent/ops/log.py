"""Append-only JSONL decision log: one line per cycle with inputs, gate verdicts, orders and fills.

Secrets never reach disk: values of keys that look like credentials are replaced, and any registered
secret string is scrubbed from every value (exchange errors sometimes echo request parameters).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(r"(api_?key|secret|password|passphrase|signature|token|private)", re.IGNORECASE)


class DecisionLog:
    def __init__(self, path: Path, *, secrets: Iterable[str] = ()) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._secrets = [s for s in secrets if s]

    def _clean(self, value: Any, key: str = "") -> Any:
        if key and _SECRET_KEY.search(key):
            return REDACTED
        if isinstance(value, dict):
            return {k: self._clean(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean(v) for v in value]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        text = str(value)
        for s in self._secrets:
            text = text.replace(s, REDACTED)
        return text

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(self._clean(record), sort_keys=True)
        with self.path.open("a") as fh:
            fh.write(line + "\n")
            fh.flush()
