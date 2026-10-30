"""QStash-signed scheduler entrypoint; manual token endpoints stay unchanged."""
import json
import os
import re
from urllib.parse import urlparse

from qstash import Receiver
from api.scan import handler as ScanHandler
from src.scheduled_tasks import execute, OPERATIONS


class handler(ScanHandler):
    def do_GET(self):
        self._send(405,{"error":"method_not_allowed"})

    def do_POST(self):
        current = os.getenv("QSTASH_CURRENT_SIGNING_KEY")
        next_key = os.getenv("QSTASH_NEXT_SIGNING_KEY")
        origin = os.getenv("CSR_SCHEDULER_ORIGIN") or ("https://"+os.environ["VERCEL_URL"] if os.getenv("VERCEL_URL") else "")
        parsed = urlparse(origin)
        if not current or not next_key or parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"","/"} or parsed.query or parsed.fragment or parsed.username:
            self._send(503,{"error":"scheduler_not_configured"}); return
        try:
            length = int(self.headers.get("Content-Length","0"))
            if not 0 < length <= 16384:
                raise ValueError()
            body = self.rfile.read(length).decode("utf-8")
        except (ValueError,UnicodeDecodeError):
            self._send(400,{"error":"invalid_body"}); return
        try:
            Receiver(current,next_key).verify(signature=self.headers.get("Upstash-Signature",""),
                body=body,url=origin.rstrip("/")+"/api/scheduler")
        except Exception:
            self._send(401,{"error":"invalid_signature"}); return
        try:
            payload = json.loads(body)
            operation = payload.get("operation") if isinstance(payload,dict) else None
            message_id = self.headers.get("Upstash-Message-Id","")
            if operation not in OPERATIONS or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",message_id):
                raise ValueError()
            wake_token = payload.get('wake_token')
            if wake_token is not None and (operation not in {'drain','validate'} or not isinstance(wake_token,str) or not re.fullmatch(r'[a-f0-9]{32}',wake_token)):
                raise ValueError()
        except (ValueError,TypeError):
            self._send(400,{"error":"invalid_operation_or_message"}); return
        try:
            args = (operation,message_id,bool(self.headers.get("Upstash-Schedule-Id")))
            self._send(200,execute(*args,**({'wake_token':wake_token} if wake_token else {})))
        except Exception:
            self._send(503,{"error":"scheduler_operation_failed","retryable":True})
