"""Bounded continuation, validation, and outbox replay commands."""
from __future__ import annotations
import json
import os
import argparse
from datetime import datetime, timezone
try:
    from .research_journal import open_journal, ResearchJournal
    from .state_store import state_store_from_env
    from .validation_cycle import run_validation_cycle
    from .request_budget import RequestBudget, CURRENT
except ImportError:
    from research_journal import open_journal, ResearchJournal
    from state_store import state_store_from_env
    from validation_cycle import run_validation_cycle
    from request_budget import RequestBudget, CURRENT


def work(operation, root="data/journal"):
    if operation == "drain":
        try:
            from .run_cycle import run
        except ImportError:
            from run_cycle import run
        return run(root=root,drain=True)
    journal = open_journal(root)
    try:
        if operation == "validate":
            budget = RequestBudget(seconds=40,max_requests=60)
            token = CURRENT.set(budget)
            try:
                return dict(run_validation_cycle(journal),request_metrics=budget.report())
            finally:
                CURRENT.reset(token)
        revision,payload = journal.load()
        if operation == "status":
            full=payload.get("last_full_scan")
            if full is None and payload.get("last_scan") and not payload["last_scan"].get("drain",True):full=payload["last_scan"]
            full_at=(full or {}).get("processed_at")
            try:
                stamp=datetime.fromisoformat(full_at.replace("Z","+00:00"))
                age=(datetime.now(timezone.utc)-stamp).total_seconds() if stamp.tzinfo else None
            except (ValueError,TypeError,AttributeError):age=None
            return {"revision":revision,"pending":len(payload.get("pending",{})),"last_scan":payload.get("last_scan"),
                "mode":(payload.get("last_scan") or {}).get("mode"),"durable":bool(os.getenv("CSR_RESEARCH_DATABASE_URL")),
                "last_full_scan_at":full_at,"full_scan_status":"unknown" if age is None or age<0 else "overdue" if age>4500 else "failed" if not full.get("scanned") else "partial" if full.get("errors") else "recent"}
        if operation == "publish":
            if payload.get("last_scan",{}).get("mode") != "live":
                raise RuntimeError("shadow snapshots cannot be published to the live read view")
            return {"revision":revision,"published":state_store_from_env(load=False).publish_snapshot(payload)}
        raise ValueError("unsupported operation")
    finally:
        journal.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation",choices=["migrate","drain","validate","publish","status"])
    parser.add_argument("--root",default="data/journal")
    args = parser.parse_args()
    if args.operation == "migrate":
        # Explicit maintenance step, never auto-migrate production at request time.
        url = os.getenv("CSR_RESEARCH_DATABASE_URL")
        if not url:
            store = open_journal(args.root)
        else:
            import psycopg
            from pathlib import Path
            label=os.getenv("CSR_RESEARCH_ENVIRONMENT")
            if label not in {"preview","production","test"}:
                raise RuntimeError("set CSR_RESEARCH_ENVIRONMENT before migration")
            with psycopg.connect(url,autocommit=True,connect_timeout=8) as connection:
                with connection.transaction():
                    connection.execute((Path(__file__).resolve().parents[1]/"schema/runtime_ledger.sql").read_text(encoding="utf-8"))
                    current=connection.execute("SELECT environment FROM csr_runtime WHERE id=1 FOR UPDATE").fetchone()[0]
                    if current is not None and current!=label:
                        raise RuntimeError("migration cannot rebind an existing ledger environment")
                    connection.execute("UPDATE csr_runtime SET environment=%s WHERE id=1 AND environment IS NULL",(label,))
            print("research schema initialized")
            return
        store.close()
        print("research schema initialized")
    else:
        print(json.dumps(work(args.operation,args.root),ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
