"""Protected Vercel entrypoint for the hourly Radar market scan."""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Vercel executes this file from the project bundle. Add src explicitly so the
# same scanner modules can run here without packaging a second copy of Radar.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from run_cycle import run  # noqa: E402


def _authorized(header: str | None, secret: str | None) -> bool:
    if not secret or not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:], secret)


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run_scan(self) -> None:
        secret = os.getenv("CSR_SCAN_TOKEN")
        if not secret:
            self._send(503, {"error": "scan_token_not_configured", "service": "crypto-state-radar"})
            return
        if not _authorized(self.headers.get("Authorization"), secret):
            self._send(401, {"error": "unauthorized", "service": "crypto-state-radar"})
            return

        started = time.monotonic()
        try:
            # Vercel's deployed filesystem is not the durable research journal.
            # Use /tmp for per-run diagnostics; lifecycle state itself is saved
            # through the direct Upstash adapter selected by state_store_from_env.
            summary = run(root=os.getenv("CSR_JOURNAL_ROOT", "/tmp/radar-journal"))
            self._send(200, {
                "service": "crypto-state-radar",
                "probe": "hourly-scan",
                "vercel_region": os.getenv("VERCEL_REGION", "sin1"),
                "duration_seconds": round(time.monotonic() - started, 3),
                "summary": summary,
            })
        except Exception as exc:
            self._send(503, {
                "error": "scan_failed",
                "error_type": type(exc).__name__,
                "service": "crypto-state-radar",
                "vercel_region": os.getenv("VERCEL_REGION", "sin1"),
                "duration_seconds": round(time.monotonic() - started, 3),
            })

    def do_GET(self) -> None:
        self._run_scan()

    def do_POST(self) -> None:
        self._run_scan()
