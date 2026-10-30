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

# The dashboard stays a single static HTML file. Inject the small authenticated
# manual-scan control at response time so the CSR token is never embedded in the
# deployed page, persisted in browser storage, or exposed by a public endpoint.
DASHBOARD_ENHANCEMENT = r"""
<style id="manual-scan-style">
#manualScanDialog{inset:50% auto auto 50%;width:min(470px,calc(100% - 28px));height:auto;max-height:calc(100dvh - 32px);margin:0;padding:0;transform:translate(-50%,-48%);border:1px solid rgba(139,219,255,.16);border-radius:22px;background:linear-gradient(180deg,rgba(12,29,43,.985),rgba(6,18,28,.99));box-shadow:0 32px 90px rgba(0,0,0,.5);opacity:0;transition:transform .2s ease,opacity .2s ease;color:#edf8fd;overflow:hidden}
#manualScanDialog[open]{transform:translate(-50%,-50%);opacity:1}
#manualScanDialog::backdrop{background:rgba(2,8,12,.68);backdrop-filter:blur(6px)}
.manual-scan-head{padding:23px 24px 17px;border-bottom:1px solid rgba(255,255,255,.055)}
.manual-scan-head h2{margin:0 0 7px;font-size:24px;letter-spacing:-.025em}.manual-scan-head p{margin:0;color:#a1b7c5;font-size:15px;line-height:1.7}
.manual-scan-body{padding:21px 24px 24px}.manual-scan-label{display:block;color:#a1b7c5;font-size:15px;margin-bottom:8px}.manual-scan-input{width:100%;min-height:46px;padding:11px 13px;border:1px solid rgba(139,219,255,.13);border-radius:13px;background:rgba(2,11,18,.58);color:#edf8fd;outline:none;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}.manual-scan-input:focus{border-color:rgba(100,217,235,.32);box-shadow:0 0 0 3px rgba(100,217,235,.05)}
.manual-scan-note{margin:10px 0 0;color:#a1b7c5;font-size:13px;line-height:1.6}.manual-scan-status{min-height:22px;margin:15px 0 0;color:#a1b7c5;font-size:15px;line-height:1.6}.manual-scan-status.ok{color:#75d39c}.manual-scan-status.bad{color:#ee9299}.manual-scan-status.busy{color:#8bdfea}
.manual-scan-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:17px}.manual-scan-primary{border-color:rgba(100,217,235,.24)!important;background:rgba(100,217,235,.075)!important}.manual-scan-primary:hover{background:rgba(100,217,235,.12)!important}
#manualScanBtn{border-color:rgba(100,217,235,.16);background:rgba(100,217,235,.035)}#manualScanBtn.busy{border-color:rgba(100,217,235,.3);box-shadow:0 0 22px rgba(100,217,235,.07)}
@media(max-width:720px){#manualScanDialog{width:calc(100% - 20px)}.manual-scan-head,.manual-scan-body{padding-left:18px;padding-right:18px}}
@media(prefers-reduced-motion:reduce){#manualScanDialog{transition:none!important}}
</style>
<script>
(()=>{
  const actions=document.querySelector('.actions');
  const refresh=document.getElementById('refreshBtn');
  if(!actions||document.getElementById('manualScanBtn'))return;

  const scanBtn=document.createElement('button');
  scanBtn.id='manualScanBtn';scanBtn.className='btn';scanBtn.type='button';scanBtn.setAttribute('aria-label','立即掃描市場');
  scanBtn.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v4"/><path d="M12 17v4"/><path d="M3 12h4"/><path d="M17 12h4"/><circle cx="12" cy="12" r="4"/></svg><span>立即掃描</span>';
  actions.insertBefore(scanBtn,refresh||null);

  const dlg=document.createElement('dialog');dlg.id='manualScanDialog';
  dlg.innerHTML='<div class="manual-scan-head"><h2>手動更新市場</h2><p>這會立即重新抓取市場資料，執行完整分析與生命週期判定，完成後更新整個儀表板。</p></div><div class="manual-scan-body"><label class="manual-scan-label" for="manualScanToken">CSR 掃描權杖</label><input id="manualScanToken" class="manual-scan-input" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="輸入 CSR_SCAN_TOKEN value"><p class="manual-scan-note">權杖只用於這一次請求，不會寫入 localStorage、sessionStorage 或頁面原始碼。</p><div id="manualScanStatus" class="manual-scan-status"></div><div class="manual-scan-actions"><button id="manualScanCancel" class="btn" type="button">取消</button><button id="manualScanRun" class="btn manual-scan-primary" type="button">開始掃描</button></div></div>';
  document.body.appendChild(dlg);

  const input=document.getElementById('manualScanToken'),runBtn=document.getElementById('manualScanRun'),cancelBtn=document.getElementById('manualScanCancel'),status=document.getElementById('manualScanStatus');
  let running=false;
  const setStatus=(text,kind='')=>{status.textContent=text;status.className=`manual-scan-status ${kind}`.trim()};
  const open=()=>{if(running)return;setStatus('');input.value='';dlg.showModal();setTimeout(()=>input.focus(),30)};
  const close=()=>{if(running)return;input.value='';dlg.close()};

  async function runManualScan(){
    const token=input.value.trim();
    const previousSnapshot=window.radarSnapshotId?.();
    if(!token){setStatus('請先輸入 CSR 掃描權杖。','bad');input.focus();return}
    running=true;scanBtn.disabled=true;scanBtn.classList.add('busy');runBtn.disabled=true;cancelBtn.disabled=true;input.disabled=true;runBtn.textContent='掃描中…';setStatus('正在重新抓取市場並執行分析與狀態判定，請保持此頁開啟。','busy');
    try{
      const response=await fetch('/api/scan',{method:'POST',cache:'no-store',headers:{'Authorization':`Bearer ${token}`,'Accept':'application/json'}});
      const payload=await response.json().catch(()=>({}));
      if(!response.ok){
        if(response.status===409)throw new Error('另一輪掃描已先完成，請重新整理後再試。');
        if(response.status===401)throw new Error('權杖不正確，請重新確認。');
        if(payload.error==='scan_token_not_configured')throw new Error('Vercel 尚未設定掃描權杖。');
        throw new Error(payload.error_type?`掃描失敗（${payload.error_type}，HTTP ${response.status}）`:`掃描失敗（HTTP ${response.status}）`);
      }
      input.value='';
      const summary=payload.summary||{};
      if(summary.mode==='shadow'){setStatus('影子掃描已完成，正式市場狀態尚未切換。','ok');return}
      if(summary.snapshot_published===false){setStatus('研究資料已保存，儀表板狀態待同步。','busy');window.waitForRadarPublication?.(previousSnapshot);return}
      setStatus('分析與狀態判定已完成，正在載入最新結果。','ok');
      if(window.waitForRadarPublication){const published=await window.waitForRadarPublication(previousSnapshot);if(!published){setStatus('分析已完成，公開版本待同步；畫面將短期檢查。','busy');return}}
      else if(window.refreshRadar){const loaded=await window.refreshRadar({afterScan:true});if(!loaded)throw new Error('掃描已完成，但最新資料載入失敗，請重新整理。')}else refresh?.click();
      dlg.close();
    }catch(err){
      input.value='';setStatus(err?.message||'掃描失敗，請重新嘗試。','bad');
    }finally{
      running=false;scanBtn.disabled=false;scanBtn.classList.remove('busy');runBtn.disabled=false;cancelBtn.disabled=false;input.disabled=false;runBtn.textContent='開始掃描';
    }
  }

  scanBtn.addEventListener('click',open);cancelBtn.addEventListener('click',close);runBtn.addEventListener('click',runManualScan);
  input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();runManualScan()}});
  dlg.addEventListener('cancel',e=>{if(running)e.preventDefault();else input.value=''});
  dlg.addEventListener('click',e=>{if(e.target===dlg&&!running)close()});

  // The production scheduler now runs at minute 7 of every hour. Keep the
  // dashboard countdown aligned with the real GitHub Actions schedule.
  const updateMinute7Countdown=()=>{
    const now=new Date(),next=new Date(now);next.setMinutes(7,0,0);if(next<=now)next.setHours(next.getHours()+1);
    const sec=Math.max(0,Math.floor((next-now)/1000)),mm=String(Math.floor(sec/60)).padStart(2,'0'),ss=String(sec%60).padStart(2,'0');
    const value=document.getElementById('nextScanValue'),foot=document.getElementById('nextScanFoot');if(value)value.textContent=`${mm}:${ss}`;if(foot)foot.textContent=`下次自動掃描 ${next.toLocaleTimeString('zh-TW',{hour:'2-digit',minute:'2-digit',hour12:false})}`;
    document.querySelectorAll('.metric-foot').forEach(el=>{if(el.textContent.includes('整點重新掃描'))el.textContent='每小時第 7 分鐘重新掃描完整市場'});
  };
  updateMinute7Countdown();setInterval(updateMinute7Countdown,1000);
})();
</script>
"""


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
    raw = _upstash_request(["GET", os.getenv("CSR_STATE_KEY",STATE_KEY)])
    if raw is None:
        return {"schema_version": 1, "states": {}}
    payload = json.loads(str(raw))
    if not isinstance(payload, dict):
        raise RuntimeError("Stored Radar state is not a JSON object")
    return payload


def _save_state(payload: dict[str, object]) -> None:
    from src.dashboard_view import redis_publication
    result = _upstash_request(redis_publication(os.getenv("CSR_STATE_KEY",STATE_KEY),payload))
    if result != 1:
        raise RuntimeError("Upstash publication was superseded")


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
        html = DASHBOARD_FILE.read_text(encoding="utf-8")
        if "</body>" in html:
            html = html.replace("</body>", f"{DASHBOARD_ENHANCEMENT}\n</body>", 1)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path, probe = self._route()
        if path in {"/", "/index.html"} or probe == "dashboard":
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
            elif path in {"/api", "/api/health", "/api/index", "/api/index.py"} or probe == "health":
                status, payload = 200, {"service": "crypto-state-radar", "status": "ok", "role": "dashboard-api", "scanner": "github-hourly-trigger", "vercel_region": "sin1"}
            else:
                status, payload = 404, {"error": "not_found", "path_seen": path}
        except Exception as exc:
            status, payload = 503, {"error": "state_store_unavailable", "error_type": type(exc).__name__}
        self._send(status, payload)

    def do_PUT(self) -> None:
        if os.getenv("CSR_RESEARCH_DATABASE_URL"):
            self._send(409,{"error":"state_is_managed_by_research_ledger"})
            return
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
