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
    from collections import deque
    try:
        from .retry_policy import utc
    except ImportError:
        from retry_policy import utc
    steps=int(horizon_hours*12)
    if steps<=0 or steps!=horizon_hours*12:
        raise ValueError("event horizon must be a positive multiple of five minutes")
    events,eligible=[],set()
    groups={}
    for row in rows:
        groups.setdefault(row["symbol"],[]).append(dict(row,time=utc(row["time"])))
    for symbol,series in sorted(groups.items()):
        series.sort(key=lambda row:row["time"])
        times=[row["time"] for row in series]
        prices=[number(row.get("close")) for row in series]
        count=len(series)
        invalid=[0];gaps=[0]
        for i,price in enumerate(prices):
            invalid.append(invalid[-1]+int(price is None or price<=0))
            gaps.append(gaps[-1]+int(i>0 and times[i]-times[i-1]!=timedelta(minutes=5)))
        # Monotonic windows avoid scanning/copying the whole remaining dataset
        # at every origin. Only rare threshold hits need a bounded crossing scan.
        highs,lows=deque(),deque()
        maxima,minima=[None]*count,[None]*count
        for i in range(count-1,-1,-1):
            j=i+1
            if j<count and prices[j] is not None and prices[j]>0:
                while highs and prices[highs[-1]]<=prices[j]:highs.pop()
                while lows and prices[lows[-1]]>=prices[j]:lows.pop()
                highs.append(j);lows.append(j)
            while highs and highs[0]>i+steps:highs.popleft()
            while lows and lows[0]>i+steps:lows.popleft()
            if highs:maxima[i]=prices[highs[0]]
            if lows:minima[i]=prices[lows[0]]
        last={}
        for i in range(max(0,count-steps)):
            right=i+steps
            if invalid[right+1]!=invalid[i] or gaps[right+1]!=gaps[i+1]:
                continue
            eligible.add((symbol,times[i]))
            entry=prices[i]
            for direction,threshold,extreme in (("LONG",0.30,maxima[i]),("SHORT",-0.25,minima[i])):
                if extreme is None or (extreme/entry-1<threshold if direction=="LONG" else extreme/entry-1>threshold):
                    continue
                if direction in last and times[right]-last[direction]<timedelta(hours=cooldown_hours):
                    continue
                crossing=next(times[j] for j in range(i+1,right+1) if (prices[j]/entry-1>=threshold if direction=="LONG" else prices[j]/entry-1<=threshold))
                if direction in last and crossing-last[direction]<timedelta(hours=cooldown_hours):
                    continue
                last[direction]=crossing
                events.append({"event_id":f"{symbol}|{direction}|{crossing.isoformat()}","symbol":symbol,
                    "direction":direction,"origin_time":times[i],"event_time":crossing,"price_basis":"5m_close"})
    return events,eligible


def match_events(events, warnings, eligible, lookback_hours=24):
    # One warning episode per symbol/direction/setup cycle. State escalation is
    # not an independent forecast; keep the earliest warning for that setup.
    def evaluate(selected_only):
        episodes = {}
        for w in sorted(warnings,key=lambda w:w["time"]):
            floor = w["time"].replace(minute=(w["time"].minute//5)*5,second=0,microsecond=0)
            mature = (w["symbol"],floor) in eligible and (floor==w["time"] or (w["symbol"],floor+timedelta(minutes=5)) in eligible)
            if (not mature or selected_only and (w.get("stream")=="model" or not w.get("selected",True))
                    or not selected_only and w.get("stream")=="system"):
                continue
            key = (w["symbol"],w["direction"],w.get("setup_id",w["time"].isoformat()))
            episodes.setdefault(key,w)
        available = list(episodes.values())
        groups={}
        for warning in available:
            groups.setdefault((warning["symbol"],warning["direction"]),[]).append(warning)
        positions={key:0 for key in groups}
        matches=[]
        for event in sorted(events,key=lambda e:e["event_time"]):
            key=(event["symbol"],event["direction"])
            candidates=groups.get(key,[]);index=positions.get(key,0)
            lower=event["event_time"]-timedelta(hours=lookback_hours)
            while index<len(candidates) and candidates[index]["time"]<lower:index+=1
            if index<len(candidates) and candidates[index]["time"]<event["event_time"]:
                warning=candidates[index];index+=1
                matches.append({"event_id":event["event_id"],"signal_id":warning.get("signal_id"),
                    "lead_hours":(event["event_time"]-warning["time"]).total_seconds()/3600})
            positions[key]=index
        tp=len(matches);fp=len(available)-tp;fn=len(events)-tp
        return {"tp":tp,"fp":fp,"fn":fn,"precision":tp/(tp+fp) if tp+fp else None,
            "recall":tp/(tp+fn) if tp+fn else None,"matches":matches}
    model,system = evaluate(False),evaluate(True)
    return {"methodology":"event-v2-close-one-to-one","model":model,"system":system,
        "selection_misses":max(0,model["tp"]-system["tp"]),"eligible_windows":len(eligible),"events":len(events)}
