"""Bounded QStash operations using the existing scan, journal and publisher."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from .research_worker import work
from .run_cycle import run

OPERATIONS = {"full_scan", "drain", "validate"}


def execute(operation, message_id, scheduled=False):
    if operation not in OPERATIONS:
        raise ValueError("unsupported scheduler operation")
    started = time.monotonic()
    receipt = {"event":"radar_scheduler", "operation":operation,
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
            if result.get("snapshot_published") is False and not result.get("deduplicated"):
                # The scan already attempted publication. Let QStash redeliver
                # for the outbox retry rather than extending this invocation.
                raise RuntimeError("snapshot publication pending")
            # Lost responses and failed publication must not recollect the market.
            # Idle continuations also retry an earlier unpublished snapshot.
            if operation != "validate" and result.get("mode") == "live" and result.get("snapshot_published") is not True:
                publication = work("publish",root=root)
                if not publication.get("published"):
                    raise RuntimeError("snapshot publication pending")
                result = dict(result,snapshot_published=True)
        # The provider receives counts only, never full market/signal payloads.
        receipt.update({key:result[key] for key in (
            "status","mode","revision","scanned","errors","pending",
            "enrichment_succeeded","enrichment_errors","snapshot_published",
            "deduplicated","processed_at") if key in result and isinstance(result[key],(str,int,float,bool,type(None)))})
        if isinstance(result.get("validated"),(int,list)):
            receipt["validated_count"] = len(result["validated"]) if isinstance(result["validated"],list) else result["validated"]
        receipt["success"] = True
        return receipt
    except Exception as exc:
        receipt.update(success=False,error_type=type(exc).__name__)
        raise RuntimeError(f"scheduler operation failed: {type(exc).__name__}") from None
    finally:
        receipt.update(completed_at=datetime.now(timezone.utc).isoformat(),duration_seconds=round(time.monotonic()-started,3))
        print(json.dumps(receipt,separators=(",",":")),flush=True)
