from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from agent.ops.alerts import AlertSink
from agent.ops.heartbeat import HeartbeatClient, serve_watchdog, sign, verify
from agent.ops.watchdog import Watchdog
from tests.unit.execution.fakes import paper

T = pd.Timestamp("2026-10-22 12:00", tz="UTC")


def test_alerts_go_to_file_and_webhook_with_secrets_redacted(tmp_path: Path) -> None:
    posted: list[dict[str, object]] = []
    sink = AlertSink(
        tmp_path / "alerts.jsonl",
        webhook_url="https://hooks.example/x",
        secrets=["SECRETVALUE"],
        post=lambda url, body: posted.append(body),
    )
    sink.send("CRITICAL", "mode FLATTEN", "exchange echoed SECRETVALUE", now=T)
    line = json.loads((tmp_path / "alerts.jsonl").read_text())
    assert line["level"] == "CRITICAL" and "SECRETVALUE" not in json.dumps(line)
    assert posted and "SECRETVALUE" not in json.dumps(posted[0]) and "mode FLATTEN" in posted[0]["text"]  # type: ignore[operator]


def test_a_broken_webhook_never_breaks_the_loop(tmp_path: Path) -> None:
    def boom(url: str, body: dict[str, object]) -> None:
        raise urllib.error.URLError("down")

    sink = AlertSink(tmp_path / "a.jsonl", webhook_url="https://hooks.example/x", post=boom)
    sink.send("WARN", "t", "d", now=T)  # must not raise
    assert "webhook failed" in (tmp_path / "a.jsonl").read_text()


def test_heartbeat_signatures() -> None:
    body = {"t": T.isoformat(), "mode": "NORMAL"}
    sig = sign("s3cret", body)
    assert verify("s3cret", body, sig) and not verify("other", body, sig)
    assert not verify("s3cret", {**body, "mode": "FLATTEN"}, sig)


def test_client_posts_signed_heartbeats_and_swallows_failures() -> None:
    sent: list[dict[str, object]] = []
    ok = HeartbeatClient("http://wd:8787", "s3cret", post=lambda url, body: sent.append(body)).send(
        T, "NORMAL"
    )
    assert ok and verify("s3cret", {k: v for k, v in sent[0].items() if k != "sig"}, str(sent[0]["sig"]))

    def down(url: str, body: dict[str, object]) -> None:
        raise ConnectionError("unreachable")

    assert HeartbeatClient("http://wd:8787", "s3cret", post=down).send(T, "NORMAL") is False


def _post(port: int, body: dict[str, object]) -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/heartbeat",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        e.close()
        return e.code


def test_watchdog_server_accepts_only_fresh_signed_heartbeats() -> None:
    now = {"t": T}
    wd = Watchdog(paper(), silence=pd.Timedelta(seconds=60))
    server = serve_watchdog(wd, secret="s3cret", host="127.0.0.1", port=0, now=lambda: now["t"])
    try:
        port = server.server_address[1]
        good = {"t": T.isoformat(), "mode": "NORMAL"}
        assert _post(port, {**good, "sig": sign("s3cret", good)}) == 200
        assert _post(port, {**good, "sig": "forged"}) == 403
        stale = {"t": (T - pd.Timedelta(minutes=5)).isoformat(), "mode": "NORMAL"}
        assert _post(port, {**stale, "sig": sign("s3cret", stale)}) == 403  # replayed old heartbeat
    finally:
        server.shutdown()
        server.server_close()
    now["t"] = T + pd.Timedelta(seconds=90)
    assert wd.check(now["t"]) is not None  # silence after the last good heartbeat triggers the watchdog
