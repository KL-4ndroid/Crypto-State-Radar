"""Bounded multi-category discovery and durable lifecycle backlog planning."""
from datetime import datetime, timezone
try:
    from .model_contract import number
except ImportError:
    from model_contract import number

PINNED = {"EARLY_WARNING", "PUMP_PRESSURE", "DUMP_PRESSURE", "LONG_READY", "SHORT_READY"}
MAX_DELAY_SECONDS = 3600


def plan_scan(snapshots, states, pending, now, capacity=25):
    available = {s["symbol"]: s for s in snapshots if not s.get("error") and s.get("symbol")}
    # A drain sees a subset of the universe. Never discard other queued work.
    queue = {s: dict(item) for s,item in pending.items()}
    for symbol, state in states.items():
        if state.state in PINNED:
            queue.setdefault(symbol, {"due_since":state.updated_at or now.isoformat(), "reason":"active_lifecycle"})
            queue[symbol]["reason"] = "active_lifecycle"
    selected = []
    def add(symbol):
        if symbol in available and symbol not in selected and len(selected)<capacity:
            selected.append(symbol)
    # All active symbols stay pinned, even when they overflow this execution.
    active = sorted((s for s in queue if queue[s]["reason"]=="active_lifecycle"), key=lambda s:(queue[s].get("last_attempt",""),queue[s]["due_since"],s))
    for symbol in active:
        add(symbol)
    categories = (
        ("oi_acceleration",lambda s: abs((number(s.get("oi_change_1h")) or 0)-(number(s.get("oi_change_24h")) or 0)/24)),
        ("price_up",lambda s: number(s.get("price_change_24h")) or 0),
        ("price_down",lambda s: -(number(s.get("price_change_24h")) or 0)),
        ("funding_crowding",lambda s: abs(number(s.get("funding_rate")) or 0)),
        ("volume_activity",lambda s: number(s.get("quote_volume_24h")) or 0),
    )
    ranked = [(reason,sorted(available,key=lambda s:(-key(available[s]),s))) for reason,key in categories]
    discovery_budget = max(0,(capacity-len(selected))//2)
    for i in range(discovery_budget):
        reason, symbols = ranked[i%len(ranked)]
        for symbol in symbols:
            if symbol not in selected:
                queue.setdefault(symbol,{"due_since":now.isoformat(),"reason":reason})
                add(symbol)
                break
    for symbol in available:
        queue.setdefault(symbol,{"due_since":states[symbol].updated_at or now.isoformat() if symbol in states else now.isoformat(),"reason":"rotation"})
    for symbol in sorted(queue,key=lambda s:(queue[s]["due_since"],s)):
        add(symbol)
    overdue = []
    for symbol,item in queue.items():
        due = datetime.fromisoformat(item["due_since"]).replace(tzinfo=timezone.utc) if "+" not in item["due_since"] and not item["due_since"].endswith("Z") else datetime.fromisoformat(item["due_since"].replace("Z","+00:00"))
        if (now-due).total_seconds()>MAX_DELAY_SECONDS:
            overdue.append(symbol)
    return [available[s] for s in selected],queue,overdue
