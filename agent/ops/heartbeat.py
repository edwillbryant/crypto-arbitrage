"""Signed heartbeats from the primary to the watchdog on a second host.

The primary POSTs `{"t", "mode", "sig"}` every cycle; `sig` is HMAC-SHA256 over the canonical JSON body
with a shared secret. The watchdog accepts a heartbeat only if the signature is valid AND its timestamp is
within 30 s of its own clock, so a captured heartbeat can't be replayed to hide a dead primary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pandas as pd

from agent.ops.alerts import http_post_json
from agent.ops.watchdog import Watchdog

MAX_SKEW = pd.Timedelta(seconds=30)


def _canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign(secret: str, body: dict[str, Any]) -> str:
    return hmac.new(secret.encode(), _canonical(body), hashlib.sha256).hexdigest()


def verify(secret: str, body: dict[str, Any], sig: str) -> bool:
    return hmac.compare_digest(sign(secret, body), sig)


class HeartbeatClient:
    def __init__(
        self, url: str, secret: str, *, post: Callable[[str, dict[str, Any]], None] = http_post_json
    ) -> None:
        self.url, self.secret, self._post = url.rstrip("/") + "/heartbeat", secret, post

    def send(self, now: pd.Timestamp, mode: str) -> bool:
        body = {"t": now.isoformat(), "mode": mode}
        try:
            self._post(self.url, {**body, "sig": sign(self.secret, body)})
        except (
            Exception
        ):  # broad on purpose: a missed heartbeat is the watchdog's signal, not ours to crash on
            return False
        return True


def serve_watchdog(
    wd: Watchdog, *, secret: str, host: str, port: int, now: Callable[[], pd.Timestamp]
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/heartbeat":
                self.send_error(404)
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                sig = str(body.pop("sig"))
                fresh = abs(now() - pd.Timestamp(body["t"])) <= MAX_SKEW
            except (ValueError, KeyError, TypeError):
                self.send_error(400)
                return
            if not (verify(secret, body, sig) and fresh):
                self.send_error(403)
                return
            wd.heartbeat(now())
            self._reply({"ok": True})

        def do_GET(self) -> None:
            if self.path != "/status":
                self.send_error(404)
                return
            last = wd.last_heartbeat
            self._reply(
                {
                    "last_heartbeat": None if last is None else last.isoformat(),
                    "silent_s": None if last is None else (now() - last).total_seconds(),
                }
            )

        def _reply(self, obj: dict[str, Any]) -> None:
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="watchdog-http", daemon=True).start()
    return server
