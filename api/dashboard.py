"""Dashboard response handler.

Keeps the public dashboard on one countdown source aligned with the Upstash
full-scan schedule (minute 7 of every hour), while reusing the existing manual
scan UI enhancement from api.index.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from pathlib import Path

from api.index import DASHBOARD_ENHANCEMENT

DASHBOARD_FILE = Path(__file__).resolve().parent.parent / "index.html"

OLD_COUNTDOWN = "function updateCountdown(){window.refreshRadarAge?.();const now=new Date(),next=new Date(now);next.setHours(now.getHours()+1,0,0,0);const sec=Math.max(0,Math.floor((next-now)/1000)),mm=String(Math.floor(sec/60)).padStart(2,'0'),ss=String(sec%60).padStart(2,'0');$('#nextScanValue').textContent=`${mm}:${ss}`;$('#nextScanFoot').textContent=`下次整點 ${next.toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit',hour12:false})}`}"
NEW_COUNTDOWN = "function updateCountdown(){window.refreshRadarAge?.();const now=new Date(),next=new Date(now);next.setMinutes(7,0,0);if(next<=now)next.setHours(next.getHours()+1);const sec=Math.max(0,Math.floor((next-now)/1000)),mm=String(Math.floor(sec/60)).padStart(2,'0'),ss=String(sec%60).padStart(2,'0');$('#nextScanValue').textContent=`${mm}:${ss}`;$('#nextScanFoot').textContent=`下次自動掃描 ${next.toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit',hour12:false})}`}"

COUNTDOWN_MARKER = "  // The production scheduler now runs at minute 7 of every hour. Keep the\n  // dashboard countdown aligned with the real GitHub Actions schedule.\n"
COUNTDOWN_END = "  updateMinute7Countdown();setInterval(updateMinute7Countdown,1000);\n"


def _manual_scan_enhancement_without_countdown() -> str:
    """Keep the manual scan UI but remove its duplicate countdown interval."""
    enhancement = DASHBOARD_ENHANCEMENT
    start = enhancement.find(COUNTDOWN_MARKER)
    if start == -1:
        return enhancement
    end = enhancement.find(COUNTDOWN_END, start)
    if end == -1:
        return enhancement
    return enhancement[:start] + enhancement[end + len(COUNTDOWN_END):]


def _render_dashboard() -> bytes:
    html = DASHBOARD_FILE.read_text(encoding="utf-8")
    html = html.replace("<div class=\"radar-caption\" id=\"nextScanFoot\">下次整點</div>", "<div class=\"radar-caption\" id=\"nextScanFoot\">下次自動掃描</div>", 1)
    html = html.replace("<div class=\"metric-foot\">整點重新掃描完整市場</div>", "<div class=\"metric-foot\">每小時第 7 分鐘重新掃描完整市場</div>", 1)
    html = html.replace(OLD_COUNTDOWN, NEW_COUNTDOWN, 1)

    enhancement = _manual_scan_enhancement_without_countdown()
    if "</body>" in html:
        html = html.replace("</body>", f"{enhancement}\n</body>", 1)
    return html.encode("utf-8")


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if not DASHBOARD_FILE.exists():
            body = b'{"error":"dashboard_file_missing"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
        else:
            body = _render_dashboard()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=0, must-revalidate" if DASHBOARD_FILE.exists() else "no-store")
        self.send_header("Vercel-CDN-Cache-Control", "public, max-age=30" if DASHBOARD_FILE.exists() else "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
