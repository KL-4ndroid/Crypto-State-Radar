"""Pure analysis of existing receipts; never collects market data or changes state."""
from datetime import datetime,timedelta,timezone
from .retry_policy import utc
from .scheduler_contract import CRONS,scheduled_minute


def analyze(start,end,as_of,runs,receipts,events,provider_complete=True):
    start,end,as_of=map(utc,(start,end,as_of))
    if end<=start:raise ValueError('acceptance end must follow start')
    stop=min(end,as_of-timedelta(minutes=5))
    slots={op:set() for op in CRONS}
    cursor=start.replace(second=0,microsecond=0)
    if cursor<start:cursor+=timedelta(minutes=1)
    while cursor<stop:
        for op in CRONS:
            if scheduled_minute(op,cursor.minute):slots[op].add(cursor)
        cursor+=timedelta(minutes=1)
    schedule_ids={'csr-production-v1-'+op.replace('_','-'):op for op in CRONS}
    grouped={}
    for event in events:
        if event.get('scheduleId') not in schedule_ids or not event.get('messageId'):continue
        grouped.setdefault(event['messageId'],[]).append(event)
    matched={op:set() for op in CRONS};messages={};failed=[];delays=[];slot_counts={};unmatched=[]
    for mid,history in grouped.items():
        history.sort(key=lambda e:e['time'])
        created=next((e for e in history if e.get('state')=='CREATED'),None)
        # Without creation evidence a delivered message cannot prove its slot.
        if created is None:continue
        stamp=datetime.fromtimestamp(created['time']/1000,timezone.utc)
        if not start<=stamp<stop:continue
        op=schedule_ids[created['scheduleId']]
        candidates=[s for s in slots[op] if 0<=(stamp-s).total_seconds()<=90]
        if candidates:
            slot=max(candidates);matched[op].add(slot)
            slot_counts[(op,slot)]=slot_counts.get((op,slot),0)+1
        else:unmatched.append(mid)
        messages[mid]=history
        last=history[-1]
        if last.get('state')!='DELIVERED' or last.get('responseStatus')!=200:failed.append(mid)
        else:delays.append((last['time']-created['time'])/1000)
    observed=[r for r in receipts if r.get('message_id') in messages]
    with_metrics=[r for r in observed if isinstance(r.get('request_metrics'),dict)]
    accounted={r['message_id'] for r in with_metrics}
    by_operation={}
    for op in CRONS:
        rows=[r for r in with_metrics if r.get('operation')==op]
        by_operation[op]={'expected_slots':len(slots[op]),'observed_slots':len(matched[op]),
            'missing_slots':[x.isoformat() for x in sorted(slots[op]-matched[op])],
            'upstream_requests_recorded':sum(r['request_metrics'].get('requests',0) for r in rows),
            'upstream_failures_recorded':sum(r['request_metrics'].get('failed_requests',0) for r in rows),
            'idle_receipts':sum(r.get('status') in {'idle','waiting'} for r in rows),
            'validated_outcomes':sum(r.get('validated_count',0) for r in rows)}
    ordered=sorted((r for r in runs if r.get('processed_at') and start<=utc(r['processed_at'])<min(end,as_of)),key=lambda r:utc(r['processed_at']))
    rounds=[]
    full=[r for r in ordered if not r.get('drain')]
    for index,row in enumerate(full):
        at=utc(row['processed_at'])
        boundary=min(utc(full[index+1]['processed_at']) if index+1<len(full) else at+timedelta(hours=1),end,as_of)
        cleared=next((r for r in ordered if at<=utc(r['processed_at'])<boundary and r.get('pending')==0),None)
        rounds.append({'full_scan_at':at.isoformat(),'cleared_at':cleared['processed_at'] if cleared else None,
            'clearance_minutes':round((utc(cleared['processed_at'])-at).total_seconds()/60,2) if cleared else None,
            'overdue':cleared is None and min(end,as_of)>=at+timedelta(hours=1)})
    metrics_complete=provider_complete and bool(messages) and set(messages)<=accounted and len(with_metrics)==len(observed)
    missing=sum(len(slots[op]-matched[op]) for op in CRONS)
    failures=sum(x['upstream_failures_recorded'] for x in by_operation.values())
    application_errors=sum((r.get('errors_count',0) or 0)+(r.get('enrichment_errors',0) or 0)+(r.get('errors',0) if isinstance(r.get('errors'),int) else 0) for r in observed)
    checks={'provider_history_complete':provider_complete,'no_missing_mature_schedule_slots':missing==0,
        'no_duplicate_or_unmatched_slots':not unmatched and all(n==1 for n in slot_counts.values()),
        'all_observed_messages_delivered':not failed,'upstream_accounting_complete':metrics_complete,
        'no_recorded_upstream_failures':failures==0,'no_recorded_application_errors':application_errors==0,
        'no_overdue_scan_rounds':not any(r['overdue'] for r in rounds)}
    finished=as_of>=end+timedelta(minutes=5)
    return {'window_start':start.isoformat(),'window_end':end.isoformat(),'checked_at':as_of.isoformat(),
        'status':'in_progress' if not finished else ('passed' if end-start>=timedelta(hours=24) else 'sample_passed') if all(checks.values()) else 'needs_review',
        'full_24_hours_observed':finished and (end-start)>=timedelta(hours=24),
        'checks':checks,'operations':by_operation,'scan_rounds':rounds,
        'scheduled_messages_observed':len(messages),'messages_without_upstream_metrics':len(set(messages)-accounted),
        'receipts_without_upstream_metrics':len(observed)-len(with_metrics),
        'duplicate_slots':[dict(operation=op,at=at.isoformat(),messages=n) for (op,at),n in slot_counts.items() if n>1],
        'unmatched_messages':unmatched,
        'failed_or_unfinished_messages':failed,'delivery_seconds_max':max(delays,default=None),
        'counted_delivery_attempts':sum(e.get('state')=='ACTIVE' for h in messages.values() for e in h),
        'cost_assessment':'not_measured: request counts are not Binance weights or Vercel/Redis billing',
        'method':'UTC slots; 90s trigger matching; last 5 minutes excluded while in flight; counts from immutable receipts'}
