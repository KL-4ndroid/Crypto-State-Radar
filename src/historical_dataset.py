"""Replay a bounded raw historical cohort without repeating history in JSONL."""
from bisect import bisect_right
from datetime import datetime,timedelta,timezone
from .retry_policy import utc


def frames_from_dataset(dataset):
    start,end=utc(dataset["evaluation_start"]),utc(dataset["evaluation_end"])
    sources={}
    for symbol,source in dataset["symbols"].items():
        candles=source["klines"]
        oi=source["oi"]
        sources[symbol]=(source,[int(r[6]) for r in candles],[int(r["timestamp"]) for r in oi],{int(r["timestamp"]):r for r in oi})
    when=start
    while when<=end:
        cutoff=int(when.timestamp()*1000)
        for symbol,(source,candle_times,oi_times,oi_map) in sorted(sources.items()):
            # Close time must be strictly before as_of, matching the live adapter.
            count=bisect_right(candle_times,cutoff-1)
            oi_count=bisect_right(oi_times,cutoff)
            rows=source["klines"][max(0,count-1500):count]
            current=source["oi"][oi_count-1] if oi_count else None
            if current and cutoff-int(current["timestamp"])>600000:
                current=None
            snapshot={"symbol":symbol,"observed_at":when.isoformat(),"price":float(rows[-1][4]) if rows else None,
                "open_interest":float(current["sumOpenInterestValue"]) if current else None,"provenance":"historical_timestamp_reconstruction"}
            for h in (1,4,24):
                old=oi_map.get(int(current["timestamp"])-h*3600000) if current else None
                previous=float(old["sumOpenInterestValue"]) if old else None
                snapshot[f"oi_change_{h}h"]=(snapshot["open_interest"]/previous-1) if previous and current else None
            funding=[r for r in source["funding"] if int(r["fundingTime"])<=cutoff]
            snapshot["funding_rate"]=float(funding[-1]["fundingRate"]) if funding else None
            yield {"as_of":when.isoformat(),"snapshot":snapshot,"klines":rows,"funding":funding,"selected":True}
        when+=timedelta(hours=1)


def market_from_dataset(dataset):
    start=utc(dataset["evaluation_start"])
    end=utc(dataset["captured_as_of"])
    for symbol,source in sorted(dataset["symbols"].items()):
        for r in source["klines"]:
            # Availability of the completed candle, not its opening time.
            closed=datetime.fromtimestamp((int(r[6])+1)/1000,timezone.utc)
            if start<=closed<=end:
                yield {"symbol":symbol,"time":closed.isoformat(),"close":float(r[4])}
