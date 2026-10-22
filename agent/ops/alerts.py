"""Alerts: always appended to a local JSONL file; optionally POSTed to a webhook (Slack/Telegram-style
`{"text": ...}`). Secrets are scrubbed from both. Sending an alert never raises: a dead webhook must not stop
the trading loop, and the failure is itself recorded in the file."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from agent.ops.log import DecisionLog


def http_post_json(url: str, body: dict[str, Any], timeout: float = 5.0) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout):
        pass


class AlertSink:
    def __init__(
        self,
        path: Path,
        *,
        webhook_url: str | None = None,
        secrets: Iterable[str] = (),
        post: Callable[[str, dict[str, Any]], None] = http_post_json,
    ) -> None:
        self._log = DecisionLog(path, secrets=[*secrets, *([webhook_url] if webhook_url else [])])
        self.webhook_url, self._post = webhook_url, post

    def send(self, level: str, title: str, detail: str = "", *, now: pd.Timestamp) -> None:
        record = {"time": now, "level": level, "title": title, "detail": detail}
        clean = self._log._clean(record)
        self._log.write(record)
        if not self.webhook_url:
            return
        try:
            self._post(self.webhook_url, {"text": f"[{level}] {clean['title']}: {clean['detail']}"})
        except Exception as e:  # broad on purpose: alerting must never break the loop
            self._log.write(
                {"time": now, "level": "WARN", "title": "webhook failed", "detail": type(e).__name__}
            )
