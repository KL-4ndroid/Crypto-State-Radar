"""Cacheable public Dashboard reads; never probe Binance or the research DB."""
import hashlib
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from api.index import _upstash_request, STATE_KEY
from src.dashboard_view import encode


def read_public(kind):
    if os.getenv("CSR_STATE_FILE") and not os.getenv("VERCEL"):
        path=Path(os.environ["CSR_STATE_FILE"])
        return json.loads(path.read_text(encoding="utf-8")).get("_dashboard",{}).get(kind) if path.exists() else None
    suffix="dashboard-meta" if kind=="meta" else "dashboard-latest"
    raw=_upstash_request(["GET",os.getenv("CSR_STATE_KEY",STATE_KEY)+":"+suffix])
    return json.loads(raw) if raw else None


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed=urlparse(self.path)
        params=parse_qs(parsed.query)
        kind=params.get("kind",[None])[0] or ("meta" if parsed.path=="/api/dashboard-meta" else "view" if parsed.path=="/api/dashboard-latest" else None)
        status=200
        try:
            payload=read_public(kind) if kind in {"meta","view"} else None
            if payload is None:
                status=503 if kind else 404
                payload={"error":"public_view_not_published" if kind else "not_found"}
        except Exception:
            status,payload=503,{"error":"public_view_unavailable"}
        body=encode(payload).encode()
        etag='"'+hashlib.sha256(body).hexdigest()+'"'
        cached=status==200 and "sync" not in params
        matched=cached and any(tag.strip() in {etag,"W/"+etag,"*"} for tag in self.headers.get("If-None-Match", "").split(","))
        self.send_response(304 if matched else status)
        self.send_header("Cache-Control","public, max-age=0, must-revalidate" if cached else "no-store")
        self.send_header("Vercel-CDN-Cache-Control","public, max-age=30" if cached else "no-store")
        if status==200:self.send_header("ETag",etag)
        self.send_header("X-Content-Type-Options","nosniff")
        if not matched:
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        if not matched:self.wfile.write(body)
