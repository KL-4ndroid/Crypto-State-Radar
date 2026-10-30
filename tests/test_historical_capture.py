from datetime import datetime,timedelta,timezone
from tools import capture_replay_cohort as capture


def test_oi_paging_retains_earliest_context_when_api_returns_latest_rows(monkeypatch,tmp_path):
    now=datetime(2026,1,11,tzinfo=timezone.utc)
    class Fixed(datetime):
        @classmethod
        def now(cls,tz=None):return now
    monkeypatch.setattr(capture,"datetime",Fixed)
    candles=[]
    for i in range(2305):
        t=int((now-timedelta(days=8)+timedelta(minutes=5*i)).timestamp()*1000)
        candles.append([t,100,100,100,100,1,t+299999,100])
    oi=[{"timestamp":int((now-timedelta(days=4)+timedelta(minutes=5*i)).timestamp()*1000),"sumOpenInterestValue":"100"} for i in range(1153)]
    funding=[{"fundingTime":int((now-timedelta(hours=8*i)).timestamp()*1000),"fundingRate":"0.001"} for i in range(31)][::-1]
    pages=[]
    def get(path,params):
        name="oi" if path.endswith("openInterestHist") else "klines" if path.endswith("klines") else "funding"
        source,field=(oi,"timestamp") if name=="oi" else (candles,0) if name=="klines" else (funding,"fundingTime")
        rows=[r for r in source if params["startTime"]<=r[field]<=params["endTime"]]
        if name=="oi":
            pages.append(params)
            return rows[-params["limit"]:]  # OI range endpoint's latest-page behavior.
        return rows[:params["limit"]]
    monkeypatch.setattr(capture,"_get",get)
    def replay(frames,market):
        assert len(frames)==49
        assert all(f["snapshot"]["oi_change_24h"]==0 for f in frames)
        return {"predictions":[{"state":"WATCH"} for _ in frames],"metrics":{}}
    monkeypatch.setattr(capture,"replay",replay)
    monkeypatch.setattr(capture.sys,"argv",["capture","--symbols","A","--output",str(tmp_path)])
    capture.main()
    assert len(pages)==3
