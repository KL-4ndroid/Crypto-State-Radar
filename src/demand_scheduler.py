"""Two durable wake slots, independent of model revision and signal count.

Persist intent before publishing. A lost response can safely publish the same
token again. The repair schedule recovers expired deliveries/leases; replacing
an intent makes its old delayed messages harmless. Network I/O holds no DB lock.
"""
from datetime import datetime, timedelta, timezone
import os
import time
from urllib.parse import urlparse
from uuid import uuid4

from .retry_policy import utc
from .scan_plan import PINNED, MAX_DELAY_SECONDS

DRAIN_GAP_SECONDS = 300  # Preserve the proven pacing until Binance weight is measured.
DELIVERY_GRACE_SECONDS = 180


def enabled():
    mode = os.getenv('CSR_SCHEDULER_MODE', 'periodic')
    if mode not in {'periodic', 'demand'}:
        raise RuntimeError('unsupported scheduler mode')
    return mode == 'demand'


def _lock(journal):
    # Serialize intent changes with scan commits without advancing model revision.
    journal.sql('SELECT id FROM csr_runtime WHERE id=1' + (' FOR UPDATE' if journal.pg else '')).fetchone()


def next_work(journal, now):
    """One wake for the whole drain backlog and one for all validation horizons."""
    _, payload = journal.load()
    drain = [max(now, utc(item['next_attempt_at'])) if item.get('next_attempt_at') else now
             for item in payload.get('pending', {}).values()]
    for state in payload.get('states', {}).values():
        if state.get('state') in PINNED:
            drain.append(utc(state['updated_at']) + timedelta(seconds=MAX_DELAY_SECONDS)
                         if state.get('updated_at') else now)
    # MAX handles retry dates without allowing validation before its actual horizon.
    row = journal.sql("""SELECT MIN(CASE WHEN status='processing' THEN lease_until
        WHEN next_attempt_at IS NOT NULL AND next_attempt_at>due_at THEN next_attempt_at
        ELSE due_at END) FROM csr_validation_jobs WHERE status IN ('pending','processing')""").fetchone()
    return {'drain': max(min(drain), now + timedelta(seconds=DRAIN_GAP_SECONDS)) if drain else None,
            'validate': max(utc(row[0]), now + timedelta(seconds=60)) if row[0] else None}


def plan(journal, now):
    """Keep at most one live intent per operation; expired attempts get new tokens."""
    pending = []
    with journal.transaction():
        _lock(journal)
        for operation, due in next_work(journal, now).items():
            row = journal.sql('SELECT token,due_at,status,lease_until FROM csr_scheduler_wakes WHERE operation=?', (operation,)).fetchone()
            if row and row[2] == 'processing' and row[3] and utc(row[3]) > now:
                continue
            if due is None:
                if row:
                    journal.sql("UPDATE csr_scheduler_wakes SET status='idle',lease_until=NULL WHERE operation=?", (operation,))
                continue
            if row and row[2] in {'pending', 'sent'} and utc(row[1]) <= due and utc(row[1]) + timedelta(seconds=DELIVERY_GRACE_SECONDS) > now:
                if row[2] == 'pending':
                    pending.append((operation, row[0], utc(row[1])))
                continue
            token = uuid4().hex
            journal.sql("""INSERT INTO csr_scheduler_wakes(operation,token,due_at,status,lease_until)
                VALUES (?,?,?,'pending',NULL) ON CONFLICT(operation) DO UPDATE SET
                token=excluded.token,due_at=excluded.due_at,status='pending',lease_until=NULL""",
                (operation, token, due.isoformat()))
            pending.append((operation, token, due))
    return pending


def claim(journal, operation, token, now):
    with journal.transaction():
        _lock(journal)
        row = journal.sql('SELECT token,due_at,status,lease_until FROM csr_scheduler_wakes WHERE operation=?', (operation,)).fetchone()
        if not row or row[0] != token or row[2] == 'idle':
            return False
        if utc(row[1]) > now:
            raise RuntimeError('wake arrived before due time')
        if row[2] == 'processing' and row[3] and utc(row[3]) > now:
            raise RuntimeError('wake already leased')
        journal.sql("UPDATE csr_scheduler_wakes SET status='processing',lease_until=? WHERE operation=? AND token=?",
                    ((now + timedelta(seconds=120)).isoformat(), operation, token))
        return True


def finish(journal, operation, token):
    with journal.transaction():
        _lock(journal)
        journal.sql("UPDATE csr_scheduler_wakes SET status='idle',lease_until=NULL WHERE operation=? AND token=?", (operation, token))


def release(journal, operation, token):
    with journal.transaction():
        journal.sql("UPDATE csr_scheduler_wakes SET status='sent',lease_until=NULL WHERE operation=? AND token=? AND status='processing'", (operation, token))


def publish(operation, token, due):
    import requests
    environment = os.environ['CSR_RESEARCH_ENVIRONMENT']
    origin = os.getenv('CSR_SCHEDULER_ORIGIN') or ('https://' + os.environ['VERCEL_URL'])
    parsed = urlparse(origin)
    if environment not in {'preview', 'production'} or environment != os.getenv('VERCEL_ENV'):
        raise RuntimeError('wake environment mismatch')
    if parsed.scheme != 'https' or not parsed.netloc or parsed.path not in {'', '/'} or parsed.query or parsed.fragment or parsed.username:
        raise RuntimeError('invalid wake origin')
    if environment == 'production' and origin.rstrip('/') != 'https://crypto-state-radar.vercel.app':
        raise RuntimeError('unexpected production wake origin')
    provider = urlparse(os.environ['QSTASH_URL'])
    if provider.scheme != 'https' or not provider.hostname or not provider.hostname.endswith('.upstash.io') or provider.path not in {'','/'} or provider.query or provider.fragment or provider.username:
        raise RuntimeError('invalid provider origin')
    headers = {}
    # Only isolated protected previews may forward this explicitly supplied token.
    if environment == 'preview' and os.getenv('CSR_SCHEDULER_PREVIEW_AUTH'):
        headers['x-vercel-trusted-oidc-idp-token'] = os.environ['CSR_SCHEDULER_PREVIEW_AUTH']
    # The pinned SDK defaults to a 600-second publishing timeout. Use its documented
    # HTTP contract with a bounded timeout rather than hold a serverless invocation.
    headers = {'Upstash-Forward-' + k: v for k, v in headers.items()}
    headers.update({'Authorization': 'Bearer ' + os.environ['QSTASH_TOKEN'],
        'Upstash-Not-Before': str(int(due.timestamp()) + 1),
        'Upstash-Deduplication-Id': 'csr-' + environment + '-' + token,
        'Upstash-Flow-Control-Key': 'csr-' + environment + '-v1',
        'Upstash-Flow-Control-Value': 'parallelism=1',
        'Upstash-Retries': '2', 'Upstash-Timeout': '55s',
        'Upstash-Label': 'csr-' + environment + '-wake'})
    response = requests.post(os.environ['QSTASH_URL'].rstrip('/') + '/v2/publish/' + origin.rstrip('/') + '/api/scheduler',
        headers=headers, json={'operation': operation, 'wake_token': token}, timeout=(2, 3))
    if not response.ok:
        raise RuntimeError('wake publish failed')


def reconcile(journal, now=None, publisher=None, deadline=None):
    now = utc(now or datetime.now(timezone.utc))
    intents = plan(journal, now)
    for operation, token, due in intents:
        if deadline is not None and time.monotonic() + 6 > deadline:
            raise RuntimeError('wake publication deferred to retry')
        (publisher or publish)(operation, token, due)
        with journal.transaction():
            journal.sql("UPDATE csr_scheduler_wakes SET status='sent' WHERE operation=? AND token=? AND status='pending'", (operation, token))
    return len(intents)
