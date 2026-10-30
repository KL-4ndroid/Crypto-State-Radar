"""Bounded multi-category discovery and durable lifecycle backlog planning."""
from datetime import datetime, timezone
from datetime import timedelta
try:
    from .model_contract import number
    from .retry_policy import utc, ready
except ImportError:
    from model_contract import number
    from retry_policy import utc, ready

PINNED = {"EARLY_WARNING", "PUMP_PRESSURE", "DUMP_PRESSURE", "LONG_READY", "SHORT_READY"}
MAX_DELAY_SECONDS = 3600


def order_time(value):
    return utc(value) if value else datetime.min.replace(tzinfo=timezone.utc)


def pending_work(states,pending,now):
    queue={s:dict(item) for s,item in pending.items()}
    for symbol,state in states.items():
        if state.state not in PINNED:
            continue
        due=utc(state.updated_at)+timedelta(seconds=MAX_DELAY_SECONDS) if state.updated_at else utc(now)
        if symbol not in queue and due>utc(now):
            continue
        queue.setdefault(symbol,{"due_since":state.updated_at or now.isoformat(),"reason":"active_lifecycle"})
        queue[symbol]["reason"]="active_lifecycle"
    return queue


def drain_candidates(states,pending,now,capacity=25):
    queue=pending_work(states,pending,now)
    order=lambda s:(queue[s]["reason"]!="active_lifecycle",order_time(queue[s].get("last_attempt")),order_time(queue[s]["due_since"]),s)
    return sorted((s for s in queue if ready(queue[s],now)),key=order)[:capacity],queue


def plan_scan(snapshots, states, pending, now, capacity=25):
    available = {s["symbol"]: s for s in snapshots if not s.get("error") and s.get("symbol")}
    # A drain sees a subset of the universe. Never discard other queued work.
    queue = pending_work(states,pending,now)
    selected = []
    def add(symbol):
        if symbol in available and symbol not in selected and len(selected)<capacity and ready(queue.get(symbol,{}),now):
            selected.append(symbol)
    # All active symbols stay pinned, even when they overflow this execution.
    active = sorted((s for s in queue if queue[s]["reason"]=="active_lifecycle"), key=lambda s:(order_time(queue[s].get("last_attempt")),order_time(queue[s]["due_since"]),s))
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
            if symbol not in selected and ready(queue.get(symbol,{}),now):
                queue.setdefault(symbol,{"due_since":now.isoformat(),"reason":reason})
                add(symbol)
                break
    for symbol in available:
        queue.setdefault(symbol,{"due_since":states[symbol].updated_at or now.isoformat() if symbol in states else now.isoformat(),"reason":"rotation"})
    for symbol in sorted(queue,key=lambda s:(order_time(queue[s]["due_since"]),s)):
        add(symbol)
    overdue = []
    for symbol,item in queue.items():
        due = utc(item["due_since"])
        if (utc(now)-due).total_seconds()>MAX_DELAY_SECONDS:
            overdue.append(symbol)
    return [available[s] for s in selected],queue,overdue
