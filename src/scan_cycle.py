"""One autonomous discovery cycle: collect -> enrich -> classify -> journal.

Designed for a scheduler/worker. ChatGPT/OpenAI is not part of runtime.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from live_collector import collect_market
from live_features import derive_live_features
from persistence import JsonlResearchStore
from radar_engine import classify

SIGNAL_STATES = {"EARLY_WARNING", "PUMP_PRESSURE", "DUMP_PRESSURE", "LONG_READY", "SHORT_READY"}
PRIORITY = {"EVAAUSDT", "ONUSDT", "PREDICTUSDT"}


def _feature_snapshot(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "oi_change_1h": (observation.get("oi_change_1h") * 100.0) if observation.get("oi_change_1h") is not None else None,
        "oi_change_4h": (observation.get("oi_change_4h") * 100.0) if observation.get("oi_change_4h") is not None else None,
        "oi_change_24h": (observation.get("oi_change_24h") * 100.0) if observation.get("oi_change_24h") is not None else None,
    }


def run_cycle(symbols: list[str] | None = None, store: JsonlResearchStore | None = None) -> dict[str, Any]:
    store = store or JsonlResearchStore()
    started = datetime.now(timezone.utc).isoformat()
    observations = collect_market(symbols)
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for obs in observations:
        if obs.get("error"):
            errors.append({"symbol": obs.get("symbol", "?"), "error": obs["error"]})
            continue
        store.append_observation(obs)
        features = _feature_snapshot(obs)
        try:
            features.update(derive_live_features(obs["symbol"]))
        except Exception as exc:
            # Preserve objective observation even if enrichment is temporarily unavailable.
            errors.append({"symbol": obs["symbol"], "error": f"feature enrichment: {exc}"})

        decision = classify(obs["symbol"], features).to_dict()
        decision["entry_price"] = obs.get("price")
        decision["funding_rate"] = obs.get("funding_rate")
        decision["open_interest"] = obs.get("open_interest")
        if decision["state"] in SIGNAL_STATES:
            store.append_signal(decision)
            candidates.append(decision)

    candidates.sort(key=lambda x: max(x["pump_score"], x["dump_score"]), reverse=True)
    return {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "observations": len(observations),
        "candidate_count": len(candidates),
        "priority": [x for x in candidates if x["symbol"] in PRIORITY],
        "top_candidates": candidates[:25],
        "errors": errors[:50],
    }


if __name__ == "__main__":
    print(json.dumps(run_cycle(), ensure_ascii=False, indent=2))
