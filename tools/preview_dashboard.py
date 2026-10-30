"""Loopback-only acceptance server with synthetic markets and disposable storage.

Never reads real credentials or contacts Binance/Upstash/PostgreSQL.
"""
import os
import sys
import json
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
for key in ("KV_REST_API_URL","KV_REST_API_TOKEN","CSR_STATE_STORE_URL","CSR_STATE_STORE_TOKEN",
            "CSR_RESEARCH_DATABASE_URL","VERCEL","VERCEL_ENV"):
    os.environ.pop(key,None)
from api import scan
from api.dashboard import _render_dashboard
from api.dashboard_data import handler as DataHandler
import run_cycle


with tempfile.TemporaryDirectory(prefix="radar-acceptance-") as directory:
    state_file=Path(directory)/"state.json"
    os.environ.update(CSR_STATE_FILE=str(state_file),CSR_JOURNAL_ROOT=directory,CSR_SCAN_MODE="live",CSR_SCAN_TOKEN="test-only")
    cases=json.loads((ROOT/"tests/fixtures/market_cases.json").read_text())[:3]
    def snapshots(symbols=None):
        return [{"symbol":c["name"].upper()+"USDT","observed_at":datetime.now(timezone.utc).isoformat(),
            "price":c["price"],"open_interest":1000000,"funding_rate":0.0001,"quote_volume_24h":1000000,
            **{f"oi_change_{h}h":c[f"oi{h}"] for h in (1,4,24)}} for c in cases]
    def enrich(symbol):
        c=next(c for c in cases if c["name"].upper()+"USDT"==symbol)
        return {"price_change_1h":c["p1"],"price_change_4h":c["p4"],"volume_z":c["volume_z"],
            "volatility_compression":c["compression"],"funding_z":c["funding_z"],"funding_acceleration":c.get("funding_acceleration")}
    run_cycle.collect_market=snapshots
    run_cycle.derive_live_features=enrich
    class Handler(BaseHTTPRequestHandler):
        _send=scan.handler._send
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=="/":
                body=_render_dashboard();self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.end_headers();self.wfile.write(body)
            elif self.path=="/dashboard-controller.js":
                body=(ROOT/"dashboard-controller.js").read_bytes();self.send_response(200);self.send_header("Content-Type","text/javascript");self.end_headers();self.wfile.write(body)
            elif self.path.startswith(("/api/dashboard-meta","/api/dashboard-latest")):
                DataHandler.do_GET(self)
            elif self.path=="/api/state":
                self._send(200,json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {"states":{}})
            elif self.path in {"/api/state-health","/api/binance-health"}:
                self._send(200,{"reachable":True,"symbols":3,"schema_version":1})
            elif self.path=="/__shutdown":
                self._send(200,{"stopped":True});threading.Thread(target=self.server.shutdown,daemon=True).start()
            else:self._send(404,{"error":"not_found"})
        def do_POST(self):
            if self.path=="/api/scan":scan.handler._run_scan(self)
            else:self._send(404,{"error":"not_found"})
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    print(json.dumps({"url":f"http://127.0.0.1:{server.server_port}"}),flush=True)
    try:server.serve_forever()
    finally:server.server_close()
