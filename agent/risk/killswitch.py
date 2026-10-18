"""Kill switch: cancel everything, flatten, lock. Three ways in, one way out.

Trip:   `python -m agent kill`, creating the flag file, or `POST http://127.0.0.1:<port>/kill`.
Unlock: only `python -m agent unlock --confirm --reason "..."`. Deleting the flag file does NOT unlock,
        and the HTTP endpoint has no unlock route.
The lock lives in the persistent RiskLedger, so it survives restarts. While locked, the RiskGate flattens.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

from agent.risk.ledger import RiskLedger


class KillSwitch:
    def __init__(self, ledger: RiskLedger, flag_path: Path) -> None:
        self.ledger = ledger
        self.flag_path = Path(flag_path)

    def tripped(self, now: pd.Timestamp) -> bool:
        """True while trading is locked (kill switch or drawdown lock)."""
        if self.flag_path.exists() and not self.ledger.locked:
            note = self.flag_path.read_text(errors="replace").strip()[:200]
            self.ledger.lock(now, f"kill switch (flag file){': ' + note if note else ''}", source="file")
        return self.ledger.locked

    def trip(self, now: pd.Timestamp, *, source: str, reason: str) -> None:
        self.flag_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.flag_path.with_suffix(".tmp")
        tmp.write_text(f"{now.isoformat()} {source}: {reason}\n")
        tmp.replace(self.flag_path)
        self.ledger.lock(now, f"kill switch ({source}): {reason}", source=source)

    def reset(self, now: pd.Timestamp, *, reason: str) -> None:
        # Unlock first: it validates the reason. If we crash before removing the flag, the next check
        # re-locks, which is the safe direction.
        self.ledger.unlock(now, reason=reason)
        self.flag_path.unlink(missing_ok=True)


def serve_http(ks: KillSwitch, *, port: int) -> ThreadingHTTPServer:
    """Start a localhost-only endpoint in a daemon thread. Only `POST /kill` and `GET /status` exist."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/kill":
                self.send_error(404)
                return
            ks.trip(
                pd.Timestamp.now(tz="UTC"), source="http", reason=f"POST /kill from {self.client_address[0]}"
            )
            self._reply(b"locked\n")

        def do_GET(self) -> None:
            if self.path != "/status":
                self.send_error(404)
                return
            self._reply(b"locked\n" if ks.tripped(pd.Timestamp.now(tz="UTC")) else b"unlocked\n")

        def _reply(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass  # the ledger's audit log records every trip

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, name="killswitch-http", daemon=True).start()
    return server
