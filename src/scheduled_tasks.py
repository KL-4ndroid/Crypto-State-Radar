"""Bounded QStash operations using the existing scan, journal and publisher."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from .research_worker import work
from .run_cycle import run
from .research_journal import open_journal

OPERATIONS = {"full_scan", "drain", "validate"}


def persist_receipt(receipt):
    journal = open_journal(os.getenv("CSR_JOURNAL_ROOT", "/tmp/radar-journal"))
    try:
        journal.append_scheduler_receipt(receipt)
    finally:
        journal.close()


def execute(operation, message_id, scheduled=False):
    if operation not in OPERATIONS:
        raise ValueError("unsupported scheduler operation")
    started = time.monotonic()
    receipt = {"event":"radar_scheduler", "operation":operation,
        "started_at":datetime.now(timezone.utc).isoformat(),
        "message_id":message_id, "trigger":"qstash_schedule" if scheduled else "qstash_delivery"}
    try:
        if os.getenv("CSR_SCHEDULER_ENABLED") != "true":
            result = {"status":"disabled"}
        else:
            target = os.getenv("VERCEL_ENV")
            if target not in {"production", "preview"} or os.getenv("CSR_RESEARCH_ENVIRONMENT") != target:
                raise RuntimeError("scheduler environment mismatch")
            if not os.getenv("CSR_RESEARCH_DATABASE_URL"):
                raise RuntimeError("scheduler requires a durable ledger")
            root = os.getenv("CSR_JOURNAL_ROOT", "/tmp/radar-journal")
            operation_id = f"qstash:{message_id}:{operation}"
            result = run(root=root, operation_id=operation_id) if operation == "full_scan" else work(operation, root=root, operation_id=operation_id)
        # The provider receives counts only, never full market/signal payloads.
        receipt.update({key:result[key] for key in (
            "status","mode","revision","scanned","errors","pending",
            "enrichment_succeeded","enrichment_errors","snapshot_published",
            "deduplicated","processed_at") if key in result and isinstance(result[key],(str,int,float,bool,type(None)))})
        if isinstance(result.get("validated"),(int,list)):
            receipt["validated_count"] = len(result["validated"]) if isinstance(result["validated"],list) else result["validated"]
        if isinstance(result.get("errors"),list):
            receipt["errors_count"] = len(result["errors"])
        if isinstance(result.get("checked_jobs"),int):
            receipt["checked_jobs"] = result["checked_jobs"]
        metrics = result.get("request_metrics")
        if result.get("deduplicated"):
            receipt["request_metrics"] = {"requests":0,"failed_requests":0,"request_seconds":0}
        elif isinstance(metrics,dict):
            receipt["request_metrics"] = {k:metrics[k] for k in ("requests","failed_requests","request_seconds") if isinstance(metrics.get(k),(int,float))}
        elif result.get("status") in {"idle","waiting"}:
            receipt["request_metrics"] = {"requests":0,"failed_requests":0,"request_seconds":0}
        if result.get("snapshot_published") is False and not result.get("deduplicated"):
            raise RuntimeError("snapshot publication pending")
        if operation != "validate" and result.get("mode") == "live" and result.get("snapshot_published") is not True:
            publication = work("publish",root=root)
            if not publication.get("published"):
                raise RuntimeError("snapshot publication pending")
            receipt["snapshot_published"] = True
        receipt["success"] = True
        return receipt
    except Exception as exc:
        receipt.update(success=False,error_type=type(exc).__name__)
        raise RuntimeError(f"scheduler operation failed: {type(exc).__name__}") from None
    finally:
        receipt.update(completed_at=datetime.now(timezone.utc).isoformat(),duration_seconds=round(time.monotonic()-started,3))
        receipt["telemetry_persisted"] = False
        # Bound optional evidence writes; never turn a completed market operation
        # into a retry merely because its telemetry write failed.
        if receipt["duration_seconds"] < 30 and os.getenv("CSR_SCHEDULER_ENABLED")=="true" and os.getenv("VERCEL_ENV") in {"preview","production"} and os.getenv("VERCEL_ENV")==os.getenv("CSR_RESEARCH_ENVIRONMENT"):
            try:
                receipt["telemetry_persisted"] = True
                persist_receipt(dict(receipt,telemetry_schema=1))
            except Exception as exc:
                receipt.update(telemetry_persisted=False,telemetry_error_type=type(exc).__name__)
        print(json.dumps(receipt,separators=(",",":")),flush=True)
