"""Minimal Vercel web entrypoint for Crypto-State-Radar.

The Vercel layer is intentionally read-only. The Binance health endpoint is a
small connectivity probe only; it does not scan markets or place trades.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

BINANCE_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"


def _binance_health() -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        BINANCE_EXCHANGE_INFO,
        headers={"User-Agent": "Crypto-State-Radar/0.2 connectivity-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            # Read only a byte so this remains a connectivity test rather than
            # a market-data collection path.
            response.read(1)
            return 200, {
                "service": "crypto-state-radar",
                "probe": "binance-futures",
                "reachable": True,
                "upstream_status": response.status,
                "vercel_region": "sin1",
            }
    except urllib.error.HTTPError as exc:
        return 200, {
            "service": "crypto-state-radar",
            "probe": "binance-futures",
            "reachable": False,
            "upstream_status": exc.code,
            "vercel_region": "sin1",
        }
    except Exception as exc:
        return 200, {
            "service": "crypto-state-radar",
            "probe": "binance-futures",
            "reachable": False,
            "upstream_status": None,
            "error_type": type(exc).__name__,
            "vercel_region": "sin1",
        }


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/api/binance-health":
            status, payload = _binance_health()
        elif path in {"/", "/api", "/api/health"}:
            status = 200
            payload = {
                "service": "crypto-state-radar",
                "status": "ok",
                "role": "dashboard-api",
                "scanner": "external-worker",
            }
        else:
            status = 404
            payload = {"error": "not_found"}

        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
