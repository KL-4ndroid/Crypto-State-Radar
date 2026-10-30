"""Explicit event detection and one-to-one matching, separate from V0 scores.

The denominator is the full supplied market series, never just selected symbols.
Incomplete forward windows are excluded rather than labeled negative.
"""
from datetime import timedelta
try:
    from .model_contract import number
except ImportError:
    from model_contract import number


def detect_events(rows, horizon_hours=24, cooldown_hours=24):
    events, eligible = [], []
    groups = {}
    for row in rows:
        groups.setdefault(row["symbol"],[]).append(row)
    for symbol, series in sorted(groups.items()):
        series.sort(key=lambda r:r["time"])
        last = {}
        for i, origin in enumerate(series):
            end = origin["time"]+timedelta(hours=horizon_hours)
            window = [r for r in series[i+1:] if r["time"]<=end]
            if (not window or window[-1]["time"]!=end
                    or any(b["time"]-a["time"]!=timedelta(minutes=5) for a,b in zip([origin]+window,window))):
                continue
            entry = number(origin.get("close"))
            if entry is None or entry<=0 or any(number(r.get("close")) is None or r["close"]<=0 for r in window):
                continue
            eligible.append((symbol,origin["time"]))
            # Explicit close-to-close event basis matches the historical V0 definition.
            for direction,threshold in (("LONG",0.30),("SHORT",-0.25)):
                hits = [r for r in window if (r["close"]/entry-1>=threshold if direction=="LONG" else r["close"]/entry-1<=threshold)]
                if not hits:
                    continue
                crossing = hits[0]["time"]
                if direction in last and crossing-last[direction]<timedelta(hours=cooldown_hours):
                    continue
                last[direction] = crossing
                events.append({"event_id":f"{symbol}|{direction}|{crossing.isoformat()}","symbol":symbol,
                    "direction":direction,"origin_time":origin["time"],"event_time":crossing,"price_basis":"5m_close"})
    return events,set(eligible)


def match_events(events, warnings, eligible, lookback_hours=24):
    # One warning episode per symbol/direction/setup cycle. State escalation is
    # not an independent forecast; keep the earliest warning for that setup.
    def evaluate(selected_only):
        episodes = {}
        for w in sorted(warnings,key=lambda w:w["time"]):
            floor = w["time"].replace(minute=(w["time"].minute//5)*5,second=0,microsecond=0)
            mature = (w["symbol"],floor) in eligible and (floor==w["time"] or (w["symbol"],floor+timedelta(minutes=5)) in eligible)
            if not mature or selected_only and not w.get("selected",True):
                continue
            key = (w["symbol"],w["direction"],w.get("setup_id",w["time"].isoformat()))
            episodes.setdefault(key,w)
        available = list(episodes.values())
        used, matches = set(), []
        for event in sorted(events,key=lambda e:e["event_time"]):
            hits = [(i,w) for i,w in enumerate(available) if i not in used and w["symbol"]==event["symbol"] and w["direction"]==event["direction"]
                and timedelta(0)<event["event_time"]-w["time"]<=timedelta(hours=lookback_hours)]
            if hits:
                i,w = min(hits,key=lambda pair:pair[1]["time"])
                used.add(i)
                matches.append({"event_id":event["event_id"],"signal_id":w.get("signal_id"),"lead_hours":(event["event_time"]-w["time"]).total_seconds()/3600})
        tp = len(matches); fp = len(available)-tp; fn = len(events)-tp
        return {"tp":tp,"fp":fp,"fn":fn,"precision":tp/(tp+fp) if tp+fp else None,
            "recall":tp/(tp+fn) if tp+fn else None,"matches":matches}
    model,system = evaluate(False),evaluate(True)
    return {"methodology":"event-v1-close-one-to-one","model":model,"system":system,
        "selection_misses":max(0,model["tp"]-system["tp"]),"eligible_windows":len(eligible),"events":len(events)}
