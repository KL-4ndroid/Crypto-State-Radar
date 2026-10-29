"""Aggregate validated signal outcomes for human/ChatGPT research review."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from persistence import JsonlResearchStore


def summarize(store: JsonlResearchStore | None = None) -> dict[str, Any]:
    store = store or JsonlResearchStore()
    rows = list(store.read("validations"))
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("model_version") or "unknown"),
            str(row.get("horizon") or "unknown"),
            str(row.get("direction") or "unknown"),
        )
        buckets[key].append(row)

    output = []
    for (version, horizon, direction), items in sorted(buckets.items()):
        n = len(items)
        successes = sum(x.get("outcome") in {"SUCCESS", "STRONG_SUCCESS"} for x in items)
        strong = sum(x.get("outcome") == "STRONG_SUCCESS" for x in items)
        failures = sum(x.get("outcome") == "FAILURE" for x in items)
        returns = [float(x.get("return_pct") or 0) for x in items]
        mfes = [float(x.get("mfe_pct") or 0) for x in items]
        maes = [float(x.get("mae_pct") or 0) for x in items]
        output.append({
            "model_version": version,
            "horizon": horizon,
            "direction": direction,
            "samples": n,
            "success_rate": round(successes / n, 4) if n else None,
            "strong_success_rate": round(strong / n, 4) if n else None,
            "failure_rate": round(failures / n, 4) if n else None,
            "avg_return_pct": round(sum(returns) / n, 4) if n else None,
            "avg_mfe_pct": round(sum(mfes) / n, 4) if n else None,
            "avg_mae_pct": round(sum(maes) / n, 4) if n else None,
        })
    return {"validation_rows": len(rows), "groups": output}


if __name__ == "__main__":
    print(json.dumps(summarize(), ensure_ascii=False, indent=2))
