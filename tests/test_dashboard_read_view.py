import json
from io import BytesIO
from email.message import Message
from api import dashboard_data
from src import dashboard_view


def sample():
    return {"revision":9,"ledger_id":"private-ledger","model_version":"test-model",
        "pending":{"internal":"private"},"states":{"A":{"state":"WATCH","last_price":100,"features":{"secret":"hidden"},"internal_error":"hidden"}},
        "last_scan":{"processed_at":"2026-10-30T01:00:00Z","scanned":1,"errors":0,"notifications":["private"]}}


def request(path,etag=None):
    h=dashboard_data.handler.__new__(dashboard_data.handler)
    h.path=path;h.headers=Message();h.wfile=BytesIO()
    if etag:h.headers['If-None-Match']=etag
    result={"headers":{}}
    h.send_response=lambda status:result.update(status=status)
    h.send_header=lambda k,v:result['headers'].update({k:v})
    h.end_headers=lambda:None
    h.do_GET();result['body']=h.wfile.getvalue()
    return result


def test_public_projection_identity_privacy_and_small_manifest(monkeypatch):
    payload=sample();first=dashboard_view.publication(payload)
    encoded=dashboard_view.encode(first)
    assert all(secret not in encoded for secret in ('private-ledger','internal_error','features','notifications'))
    assert len(dashboard_view.encode(first['meta']).encode())<=2048
    missing=dashboard_view.publication({'states':{},'last_scan':{'processed_at':'2026-10-30T01:00:00Z'}})['meta']
    assert missing['scan_status']=='unknown' and missing['last_successful_scan_at'] is None
    partial=sample();partial['last_scan']['errors']=1
    meta=dashboard_view.publication(partial)['meta']
    assert meta['scan_status']=='partial' and meta['last_successful_scan_at'] is None
    payload.update(revision=0,ledger_id='new-ledger')
    assert dashboard_view.publication(payload)['meta']['snapshot_id']==first['meta']['snapshot_id']
    payload['states']['A']['last_price']=101
    assert dashboard_view.publication(payload)['meta']['snapshot_id']!=first['meta']['snapshot_id']
    payload['states']['A']['last_price']=100
    monkeypatch.setattr(dashboard_view,'SCHEMA_VERSION',2)
    assert dashboard_view.publication(payload)['meta']['snapshot_id']!=first['meta']['snapshot_id']


def test_conditional_read_errors_and_manual_sync_cache_boundary(monkeypatch):
    public=dashboard_view.publication(sample())
    monkeypatch.setattr(dashboard_data,'read_public',lambda kind:public['meta' if kind=='meta' else 'view'])
    first=request('/api/dashboard-meta')
    assert first['status']==200 and first['headers']['Vercel-CDN-Cache-Control']=='public, max-age=30'
    unchanged=request('/api/dashboard-meta','W/'+first['headers']['ETag'])
    assert unchanged['status']==304 and unchanged['body']==b''
    sync=request('/api/dashboard-meta?sync=1',first['headers']['ETag'])
    assert sync['status']==200 and sync['headers']['Cache-Control']=='no-store'
    monkeypatch.setattr(dashboard_data,'read_public',lambda kind:None)
    missing=request('/api/dashboard-meta')
    assert missing['status']==503 and missing['headers']['Vercel-CDN-Cache-Control']=='no-store'
    assert 'ETag' not in missing['headers']


def test_read_view_uses_isolated_key_without_external_health_or_ledger(monkeypatch):
    monkeypatch.delenv('CSR_STATE_FILE',raising=False)
    monkeypatch.setenv('CSR_STATE_KEY','isolated-preview')
    commands=[]
    def command(parts):commands.append(parts);return json.dumps({'snapshot_id':'test'})
    monkeypatch.setattr(dashboard_data,'_upstash_request',command)
    assert dashboard_data.read_public('meta')['snapshot_id']=='test'
    assert commands==[['GET','isolated-preview:dashboard-meta']]
