"""Vercel API entrypoint for Crypto-State-Radar health and durable state.

Radar state is stored as one versioned JSON snapshot in Upstash Redis via its
REST API. Credentials are supplied by the Vercel Marketplace integration and
are never returned by these endpoints.
"""
from __future__ import annotations

import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path

BINANCE_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
STATE_KEY = "crypto-state-radar:state:v1"
PROBE_KEY = "crypto-state-radar:persistence-probe:v1"
DASHBOARD_FILE = Path(__file__).resolve().parent.parent / "index.html"


def _upstash_request(command: list[str]) -> object:
    base_url = os.getenv("KV_REST_API_URL")
    token = os.getenv("KV_REST_API_TOKEN")
    if not base_url or not token:
        raise RuntimeError("Upstash Redis environment is not configured")
    request = urllib.request.Request(
        base_url.rstrip("/"),
        data=json.dumps(command, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    if "error" in payload:
        raise RuntimeError("Upstash Redis command failed")
    return payload.get("result")


def _load_state() -> dict[str, object]:
    raw = _upstash_request(["GET", STATE_KEY])
    if raw is None:
        return {"schema_version": 1, "states": {}}
    payload = json.loads(str(raw))
    if not isinstance(payload, dict):
        raise RuntimeError("Stored Radar state is not a JSON object")
    return payload


def _save_state(payload: dict[str, object]) -> None:
    result = _upstash_request(["SET", STATE_KEY, json.dumps(payload, separators=(",", ":"))])
    if result != "OK":
        raise RuntimeError("Upstash Redis SET did not return OK")


def _authorized(header: str | None, secret: str | None) -> bool:
    if not secret or not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:], secret)


def _persistence_probe() -> tuple[int, dict[str, object]]:
    """Exercise Redis SET/GET without touching the real Radar snapshot."""
    marker = datetime.now(timezone.utc).isoformat()
    written = _upstash_request(["SET", PROBE_KEY, marker, "EX", "60"])
    read_back = _upstash_request(["GET", PROBE_KEY])
    matched = written == "OK" and read_back == marker
    return (200 if matched else 503), {
        "service": "crypto-state-radar",
        "probe": "state-persistence",
        "configured": True,
        "write_ok": written == "OK",
        "read_back_ok": read_back == marker,
        "persistent": matched,
        "probe_ttl_seconds": 60,
        "vercel_region": "sin1",
    }


def _state_health() -> tuple[int, dict[str, object]]:
    configured = bool(os.getenv("KV_REST_API_URL") and os.getenv("KV_REST_API_TOKEN"))
    if not configured:
        return 503, {"service": "crypto-state-radar", "probe": "state-store", "configured": False, "reachable": False}
    try:
        payload = _load_state()
        states = payload.get("states", {})
        return 200, {
            "service": "crypto-state-radar",
            "probe": "state-store",
            "configured": True,
            "reachable": True,
            "schema_version": payload.get("schema_version"),
            "symbols": len(states) if isinstance(states, dict) else None,
            "vercel_region": "sin1",
        }
    except Exception as exc:
        return 503, {"service": "crypto-state-radar", "probe": "state-store", "configured": True, "reachable": False, "error_type": type(exc).__name__, "vercel_region": "sin1"}


def _binance_health() -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(BINANCE_EXCHANGE_INFO, headers={"User-Agent": "Crypto-State-Radar/0.4 connectivity-check"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response.read(1)
            return 200, {"service": "crypto-state-radar", "probe": "binance-futures", "reachable": True, "upstream_status": response.status, "vercel_region": "sin1"}
    except urllib.error.HTTPError as exc:
        return 200, {"service": "crypto-state-radar", "probe": "binance-futures", "reachable": False, "upstream_status": exc.code, "vercel_region": "sin1"}
    except Exception as exc:
        return 200, {"service": "crypto-state-radar", "probe": "binance-futures", "reachable": False, "upstream_status": None, "error_type": type(exc).__name__, "vercel_region": "sin1"}


class handler(BaseHTTPRequestHandler):
    def _route(self) -> tuple[str, str]:
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        return path.rstrip("/") or "/", params.get("probe", [""])[0]

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_dashboard(self) -> None:
        if not DASHBOARD_FILE.exists():
            self._send(503, {"error": "dashboard_file_missing"})
            return
        body = DASHBOARD_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path, probe = self._route()
        if path in {"/", "/index.html"}:
            self._send_dashboard()
            return
        try:
            if path == "/api/binance-health" or probe == "binance":
                status, payload = _binance_health()
            elif path == "/api/state-health" or probe == "state-health":
                status, payload = _state_health()
            elif path == "/api/persistence-probe" or probe == "persistence":
                secret = os.getenv("CRON_SECRET") or os.getenv("CSR_STATE_STORE_TOKEN")
                if not secret:
                    status, payload = 503, {"error": "probe_auth_not_configured"}
                elif not _authorized(self.headers.get("Authorization"), secret):
                    status, payload = 401, {"error": "unauthorized"}
                else:
                    status, payload = _persistence_probe()
            elif path == "/api/state" or probe == "state":
                status, payload = 200, _load_state()
            elif path in {"/api", "/api/health", "/api/index.py"}:
                status, payload = 200, {"service": "crypto-state-radar", "status": "ok", "role": "dashboard-api", "scanner": "github-hourly-trigger", "vercel_region": "sin1"}
            else:
                status, payload = 404, {"error": "not_found", "path_seen": path}
        except Exception as exc:
            status, payload = 503, {"error": "state_store_unavailable", "error_type": type(exc).__name__}
        self._send(status, payload)

    def do_PUT(self) -> None:
        path, probe = self._route()
        if path != "/api/state" and probe != "state":
            self._send(404, {"error": "not_found", "path_seen": path})
            return

        secret = os.getenv("CSR_STATE_STORE_TOKEN")
        if not secret:
            self._send(503, {"error": "state_write_disabled"})
            return
        if not _authorized(self.headers.get("Authorization"), secret):
            self._send(401, {"error": "unauthorized"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("states"), dict):
                self._send(400, {"error": "invalid_snapshot"})
                return
            _save_state(payload)
            self._send(200, {"ok": True, "schema_version": payload.get("schema_version"), "symbols": len(payload["states"])})
        except Exception as exc:
            self._send(503, {"error": "state_store_unavailable", "error_type": type(exc).__name__})
