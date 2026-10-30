"""Protected maintenance API; no automatic production worker activation."""
import os
import time
from urllib.parse import urlparse, parse_qs
from api.scan import handler as ScanHandler, _authorized
from src.research_worker import work
from src.research_journal import RevisionConflict
from src.operation_log import emit_operation


class handler(ScanHandler):
    def do_GET(self):
        # Public health metadata only; observations/signals stay in the ledger.
        if not os.getenv("CSR_RESEARCH_DATABASE_URL"):
            self._send(200,{"configured":False,"status":"not_configured"})
            return
        try:
            self._send(200,dict(work("status"),configured=True))
        except Exception as exc:
            self._send(503,{"configured":True,"error_type":type(exc).__name__})

    def do_POST(self):
        secret = os.getenv("CSR_WORKER_TOKEN")
        if not secret:
            self._send(503,{"error":"worker_not_configured"}); return
        if not _authorized(self.headers.get("Authorization"),secret):
            self._send(401,{"error":"unauthorized"}); return
        operation = parse_qs(urlparse(self.path).query).get("operation",[""])[0]
        if operation not in {"drain","validate","publish"}:
            self._send(400,{"error":"invalid_operation"}); return
        started=time.monotonic()
        try:
            result=work(operation)
            emit_operation(self,operation,200,result=result,elapsed=time.monotonic()-started)
            self._send(200,result)
        except RevisionConflict as exc:
            emit_operation(self,operation,409,elapsed=time.monotonic()-started,error=exc)
            self._send(409,{"error":"revision_conflict","retryable":True})
        except Exception as exc:
            emit_operation(self,operation,503,elapsed=time.monotonic()-started,error=exc)
            self._send(503,{"error":"worker_failed","error_type":type(exc).__name__})
