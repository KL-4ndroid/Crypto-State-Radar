import json
from io import BytesIO
from unittest.mock import patch
from email.message import Message
from api import scan
from api import research


def request(handler,method="GET",path="/api/scan",token=None):
    h=handler.__new__(handler)
    h.path=path; h.headers=Message()
    if token:h.headers["Authorization"]="Bearer "+token
    h.wfile=BytesIO()
    status=[]
    h.send_response=lambda code:status.append(code)
    h.send_header=lambda *args:None
    h.end_headers=lambda:None
    getattr(h,"do_"+method)()
    return status[0],json.loads(h.wfile.getvalue())


def test_scan_endpoint_auth_and_revision_conflict(monkeypatch):
    monkeypatch.setenv("CSR_SCAN_TOKEN","test-only")
    with patch.object(scan,"run",side_effect=scan.RevisionConflict("stale")) as run:
        assert request(scan.handler,token="wrong")[0]==401
        run.assert_not_called()
        code,payload=request(scan.handler,token="test-only")
        assert code==409 and payload["retryable"]


def test_research_operation_auth_and_unconfigured_health(monkeypatch):
    monkeypatch.delenv("CSR_RESEARCH_DATABASE_URL",raising=False)
    monkeypatch.setenv("CSR_WORKER_TOKEN","worker-only")
    with patch.object(research,"work") as work:
        assert request(research.handler,path="/api/research")[1]["configured"] is False
        assert request(research.handler,"POST","/api/research?operation=validate",token="wrong")[0]==401
        assert request(research.handler,"POST","/api/research?operation=migrate",token="worker-only")[0]==400
        work.assert_not_called()
