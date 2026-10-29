"""Minimal Vercel web entrypoint for Crypto-State-Radar.

The Vercel layer is intentionally read-only. Market collection and radar
scanning run in the worker pipeline, not inside an HTTP request.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in {"/", "/api", "/api/health"}:
            body = json.dumps(
                {
                    "service": "crypto-state-radar",
                    "status": "ok",
                    "role": "dashboard-api",
                    "scanner": "external-worker",
                }
            ).encode("utf-8")
            self.send_response(200)
        else:
            body = json.dumps({"error": "not_found"}).encode("utf-8")
            self.send_response(404)

        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
