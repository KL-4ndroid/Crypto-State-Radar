from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timezone, timedelta
import pytest
from src.research_journal import ResearchJournal, RevisionConflict, open_journal
from src.scan_plan import plan_scan
from src.state_machine import SymbolState


def test_reopen_atomic_records_and_stale_commit(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    a, b = ResearchJournal(path), ResearchJournal(path)
    try:
        signal = {"signal_id":"s","observed_at":"2026-01-01T00:00:00+00:00","entry_price":100}
        a.commit_cycle(0,{"states":{}},[("signals","s",signal)])
        with pytest.raises(RevisionConflict):
            b.commit_cycle(0,{"states":{"lost":{}}},[("observations","bad",{})])
        assert b.read("observations") == []
        assert len(b.due_jobs(datetime(2026,1,10,tzinfo=timezone.utc))) == 7
        with pytest.raises(ValueError):
            a.commit_cycle(1,{"states":{}},[("observations","rollback",{}),("signals","s",dict(signal,entry_price=999))])
        assert a.read("observations") == []
        assert a.load()[0] == 1
        a.finish_validation("s","1h",row={"value":1})
        a.finish_validation("s","1h",row={"value":2})
        assert a.read("validations") == [{"value":1}]
    finally:
        a.close(); b.close()
    reopened = ResearchJournal(path)
    try:
        assert reopened.load()[0] == 1
        assert reopened.read("signals") == [signal]
    finally:
        reopened.close()


def test_concurrent_cycles_have_one_winner(tmp_path):
    path = tmp_path/"parallel.sqlite3"
    setup = ResearchJournal(path); setup.close()
    barrier = Barrier(2)
    def commit(index):
        store = ResearchJournal(path)
        try:
            rev,_ = store.load()
            barrier.wait(timeout=5)
            try:
                store.commit_cycle(rev,{"states":{},"winner":index},[])
                return "committed"
            except RevisionConflict:
                return "conflict"
        finally:
            store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(commit,[1,2])) == ["committed","conflict"]


def test_overflow_active_symbols_persist_and_drain_fairly():
    now = datetime(2026,1,1,tzinfo=timezone.utc)
    states = {f"S{i:02}":SymbolState(f"S{i:02}",state="LONG_READY",updated_at=now.isoformat()) for i in range(40)}
    rows = [{"symbol":s} for s in states]
    selected, queue, _ = plan_scan(rows,states,{},now,25)
    assert len(selected) == 25 and len(queue) == 40
    for s in selected:
        queue.pop(s["symbol"])
        states[s["symbol"]].updated_at = (now+timedelta(minutes=5)).isoformat()
    selected2, queue2, overdue = plan_scan(rows,states,queue,now+timedelta(hours=2),25)
    assert set(states)-{s["symbol"] for s in selected} <= {s["symbol"] for s in selected2}
    assert len(overdue) == 40
    _, queue3,_ = plan_scan([rows[-1]],states,queue2,now+timedelta(hours=2),1)
    assert len(queue3) == 40  # subset continuation must never drop other work


def test_serverless_cannot_silently_use_temporary_database(monkeypatch,tmp_path):
    monkeypatch.setenv("VERCEL","1")
    monkeypatch.delenv("CSR_RESEARCH_DATABASE_URL",raising=False)
    with pytest.raises(RuntimeError,match="durable research database required"):
        open_journal(tmp_path)
