from __future__ import annotations

import csv
import gzip
import io
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://fapi.binance.com"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "raw"
MANIFESTS = ROOT / "data" / "manifests"
UA = "Crypto-State-Radar/0.1"


@dataclass
class Http:
    min_interval: float = 0.12
    last_call: float = 0.0

    def get_json(self, path: str, params: dict | None = None):
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        url = BASE + path
        if params:
            url += "?" + urlencode(params)
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode("utf-8"))
        self.last_call = time.time()
        return payload


http = Http()


def dt_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def utc_day_range(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    return start, end


def active_usdt_perpetuals() -> list[str]:
    info = http.get_json("/fapi/v1/exchangeInfo")
    out = []
    for s in info["symbols"]:
        if (
            s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
        ):
            out.append(s["symbol"])
    return sorted(out)


def paged(path: str, symbol: str, interval_key: str, interval: str,
          start: datetime, end: datetime, limit: int = 500):
    cursor = dt_ms(start)
    end_ms = dt_ms(end)
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            interval_key: interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": limit,
        }
        rows = http.get_json(path, params)
        if not rows:
            break
        yield from rows
        ts = rows[-1][0] if isinstance(rows[-1], list) else rows[-1].get("timestamp")
        if ts is None or ts < cursor:
            break
        cursor = int(ts) + 1
        if len(rows) < limit:
            break


def write_gz_csv(path: Path, header: list[str], rows: Iterable[Iterable]):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def collect_klines(symbol: str, start: datetime, end: datetime):
    rows = list(paged("/fapi/v1/klines", symbol, "interval", "5m", start, end, 1500))
    normalized = [
        [r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]]
        for r in rows
    ]
    path = DATA / "klines" / "5m" / f"{start:%Y-%m-%d}_{end:%Y-%m-%d}" / f"{symbol}.csv.gz"
    write_gz_csv(path,
                 ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote"],
                 normalized)
    return len(normalized), str(path.relative_to(ROOT))


def collect_oi(symbol: str, start: datetime, end: datetime):
    # Binance historical OI statistics endpoint; recent history retention is limited.
    rows = list(paged("/futures/data/openInterestHist", symbol, "period", "5m", start, end, 500))
    normalized = [
        [r.get("timestamp"), r.get("sumOpenInterest"), r.get("sumOpenInterestValue")]
        for r in rows
    ]
    path = DATA / "oi" / "5m" / f"{start:%Y-%m-%d}_{end:%Y-%m-%d}" / f"{symbol}.csv.gz"
    write_gz_csv(path, ["timestamp","sum_open_interest","sum_open_interest_value"], normalized)
    return len(normalized), str(path.relative_to(ROOT))


def collect_funding(symbol: str, start: datetime, end: datetime):
    cursor = dt_ms(start)
    end_ms = dt_ms(end)
    all_rows = []
    while cursor < end_ms:
        rows = http.get_json("/fapi/v1/fundingRate", {
            "symbol": symbol,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        })
        if not rows:
            break
        all_rows.extend(rows)
        cursor = int(rows[-1]["fundingTime"]) + 1
        if len(rows) < 1000:
            break
    normalized = [[r["fundingTime"], r["fundingRate"], r.get("markPrice")] for r in all_rows]
    path = DATA / "funding" / f"{start:%Y-%m-%d}_{end:%Y-%m-%d}" / f"{symbol}.csv.gz"
    write_gz_csv(path, ["funding_time","funding_rate","mark_price"], normalized)
    return len(normalized), str(path.relative_to(ROOT))


def main():
    days = int(os.environ.get("CSR_DAYS", "30"))
    only = {x.strip().upper() for x in os.environ.get("CSR_SYMBOLS", "").split(",") if x.strip()}
    start, end = utc_day_range(days)
    symbols = [s for s in active_usdt_perpetuals() if not only or s in only]
    results = []
    for i, symbol in enumerate(symbols, 1):
        record = {"symbol": symbol}
        for name, fn in [("klines", collect_klines), ("oi", collect_oi), ("funding", collect_funding)]:
            try:
                n, path = fn(symbol, start, end)
                record[name] = {"rows": n, "path": path, "ok": True}
            except Exception as e:
                record[name] = {"ok": False, "error": repr(e)}
        results.append(record)
        print(f"[{i}/{len(symbols)}] {symbol}: {record}")

    MANIFESTS.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": results,
    }
    out = MANIFESTS / f"collection-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
