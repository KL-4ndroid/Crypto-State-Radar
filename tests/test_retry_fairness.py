from datetime import datetime,timedelta,timezone
from src.research_journal import ResearchJournal
from src.scan_plan import plan_scan,drain_candidates
from src.state_machine import SymbolState


def test_failed_validation_does_not_block_other_due_jobs(tmp_path):
    store=ResearchJournal(tmp_path/"ledger.sqlite3")
    now=datetime(2026,1,10,tzinfo=timezone.utc)
    try:
        store.commit_cycle(0,{"states":{}},[("signals",s,{"signal_id":s,"observed_at":"2026-01-01T00:00:00+00:00","entry_price":100}) for s in ("a","b")])
        sid,h,token=store.claim_due_jobs(now,limit=1,lease_now=now)[0]
        assert store.finish_validation(sid,h,error="upstream",lease_token=token,now=now)
        jobs=store.claim_due_jobs(now,limit=20,lease_now=now)
        assert len(jobs)==13 and (sid,h) not in {(s,h) for s,h,_ in jobs}
        next_at=store.sql("SELECT next_attempt_at FROM csr_validation_jobs WHERE signal_id=? AND horizon=?",(sid,h)).fetchone()[0]
        assert now+timedelta(seconds=60)<=datetime.fromisoformat(next_at)<=now+timedelta(seconds=75)
    finally:store.close()


def test_expired_lease_is_reclaimed_and_old_worker_cannot_write(tmp_path):
    store=ResearchJournal(tmp_path/"ledger.sqlite3")
    now=datetime(2026,1,10,tzinfo=timezone.utc)
    try:
        store.commit_cycle(0,{"states":{}},[("signals","s",{"signal_id":"s","observed_at":"2026-01-01T00:00:00+00:00","entry_price":100})])
        sid,h,token=store.claim_due_jobs(now,limit=1,lease_now=now)[0]
        new=store.claim_due_jobs(now+timedelta(minutes=3),limit=1,lease_now=now+timedelta(minutes=3))[0]
        assert new[:2]==(sid,h) and new[2]!=token
        assert not store.finish_validation(sid,h,row={"stale":True},lease_token=token,now=now+timedelta(minutes=3))
        assert store.finish_validation(sid,h,row={"valid":True},lease_token=new[2],now=now+timedelta(minutes=3))
        assert store.read("validations")==[{"valid":True}]
    finally:store.close()


def test_scan_retry_cooldown_preserves_pinning_without_starving_healthy_work():
    now=datetime(2026,1,1,tzinfo=timezone.utc)
    states={s:SymbolState(s,state="LONG_READY",updated_at=(now-timedelta(hours=2)).isoformat()) for s in ("BAD","GOOD")}
    pending={"BAD":{"reason":"active_lifecycle","due_since":(now-timedelta(hours=2)).isoformat(),"next_attempt_at":(now+timedelta(minutes=5)).isoformat()}}
    selected,queue,overdue=plan_scan([{"symbol":s} for s in states],states,pending,now,1)
    assert selected[0]["symbol"]=="GOOD"
    assert "BAD" in queue and "BAD" in overdue
    drain,_=drain_candidates(states,queue,now,2)
    assert "BAD" not in drain
    drain,_=drain_candidates(states,queue,now+timedelta(minutes=6),2)
    assert "BAD" in drain


def test_fresh_active_state_is_not_refetched_by_idle_drain():
    now=datetime(2026,1,1,tzinfo=timezone.utc)
    states={"A":SymbolState("A",state="LONG_READY",updated_at=now.isoformat())}
    assert drain_candidates(states,{},now+timedelta(minutes=5))[0]==[]
    assert drain_candidates(states,{},now+timedelta(hours=1))[0]==["A"]
