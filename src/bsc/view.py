"""Public observation projection; no wallet/session/RPC credentials."""
from datetime import datetime, timezone
from ..v33.contracts import digest

FIELDS = ('entity_id','address','chain_id','state','first_seen_at','last_seen_at',
          'first_seen_block','last_seen_block','mint_events_observed','code_checked',
          'agent_id_reference','code_asof_block','code_checked_at','discovery_methods','reasons')


def publication(payload):
    projects = [{field:row[field] for field in FIELDS if field in row}
                for row in sorted(payload.get('projects',{}).values(),key=lambda r:(-r['last_seen_block'],r['entity_id']))[:100]]
    view = {'schema_version':'bsc-public-1','product':'bsc','projects':projects,
        'model_version':payload.get('model_version'),'execution_enabled':False,
        'status':payload.get('status','NOT_STARTED'),'cursor':payload.get('cursor'),
        'coverage_start_block':payload.get('coverage_start_block'),'lag_blocks':payload.get('lag_blocks'),
        'confirmation_delay':payload.get('confirmation_delay'),'last_attempt_at':payload.get('last_attempt_at'),
        'watch_addresses':payload.get('watch_addresses',[]),
        'last_error':payload.get('last_error'),'total_projects':len(payload.get('projects',{})),
        'pending_code':sum(not r.get('code_checked') for r in payload.get('projects',{}).values()),
        'coverage':payload.get('coverage','NOT_STARTED')}
    snapshot_id, published = digest(view), datetime.now(timezone.utc).isoformat()
    view.update(snapshot_id=snapshot_id,published_at=published)
    meta = {'schema_version':'bsc-public-1','product':'bsc','snapshot_id':snapshot_id,
        'published_at':published,'last_attempt_at':payload.get('last_attempt_at'),
        'summary_url':'/api/dashboard-latest','execution_enabled':False}
    return {'meta':meta,'view':view}
