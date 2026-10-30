from datetime import datetime,timedelta,timezone
from src.operational_acceptance import analyze
from src.scheduler_contract import CRONS,scheduled_minute


def evidence(start,end):
    events=[];receipts=[];cursor=start;index=0
    while cursor<end:
        for op in CRONS:
            if not scheduled_minute(op,cursor.minute):continue
            index+=1;mid=str(index);sid='csr-production-v1-'+op.replace('_','-');stamp=int(cursor.timestamp()*1000)
            events.extend([dict(time=stamp,messageId=mid,scheduleId=sid,state='CREATED'),
                dict(time=stamp+1000,messageId=mid,scheduleId=sid,state='ACTIVE'),
                dict(time=stamp+2000,messageId=mid,scheduleId=sid,state='DELIVERED',responseStatus=200)])
            receipts.append(dict(message_id=mid,operation=op,request_metrics={'requests':0,'failed_requests':0},success=True))
        cursor+=timedelta(minutes=1)
    return events,receipts


def test_full_day_requires_all_slots_and_receipt_accounting():
    start=datetime(2026,10,30,10,7,tzinfo=timezone.utc);end=start+timedelta(days=1)
    events,receipts=evidence(start,end)
    result=analyze(start,end,end+timedelta(minutes=6),[],receipts,events)
    assert result['status']=='passed' and result['full_24_hours_observed']
    assert result['scheduled_messages_observed']==600
    assert result['operations']['full_scan']['expected_slots']==24
    assert result['operations']['drain']['expected_slots']==288
    assert analyze(start,end,start+timedelta(hours=1),[],receipts,events)['status']=='in_progress'
    assert not analyze(start,end,end+timedelta(minutes=6),[],[],events)['checks']['upstream_accounting_complete']
    assert not analyze(start,end,end+timedelta(minutes=6),[],receipts,events[3:])['checks']['no_missing_mature_schedule_slots']


def test_retries_do_not_count_as_duplicate_scheduled_jobs_and_pagination_matters():
    start=datetime(2026,10,30,10,7,tzinfo=timezone.utc);end=start+timedelta(hours=1)
    events,receipts=evidence(start,end)
    events.append(dict(events[1],time=events[1]['time']+500,state='ACTIVE'))
    result=analyze(start,end,end+timedelta(minutes=6),[],receipts,events,provider_complete=False)
    assert result['counted_delivery_attempts']==len(receipts)+1
    assert not result['checks']['provider_history_complete']
    assert result['status']=='needs_review'


def test_backlog_clearance_is_measured_without_assuming_every_feature_exists():
    start=datetime(2026,10,30,10,7,tzinfo=timezone.utc);end=start+timedelta(days=1)
    runs=[dict(drain=False,processed_at=start.isoformat(),pending=497),
        dict(drain=True,processed_at=(start+timedelta(minutes=21)).isoformat(),pending=0)]
    result=analyze(start,end,start+timedelta(hours=2),runs,[],[])
    assert result['scan_rounds'][0]['clearance_minutes']==21 and not result['scan_rounds'][0]['overdue']
