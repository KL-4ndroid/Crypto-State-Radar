from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived" / "features"
CONFIG = json.loads((ROOT / "config" / "backtest.json").read_text(encoding="utf-8"))
BASE_MIN = CONFIG["base_interval_minutes"]


def read_csv_gz(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, compression="gzip")


def pct_change_safe(s: pd.Series, periods: int) -> pd.Series:
    prev = s.shift(periods)
    out = s / prev - 1.0
    return out.replace([np.inf, -np.inf], np.nan)


def build_symbol(klines_path: Path, oi_path: Path, funding_path: Path | None = None) -> pd.DataFrame:
    k = read_csv_gz(klines_path)
    k["timestamp"] = pd.to_datetime(k["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        k[c] = pd.to_numeric(k[c], errors="coerce")
    k = k.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")

    oi = read_csv_gz(oi_path)
    oi["timestamp"] = pd.to_datetime(oi["timestamp"], unit="ms", utc=True)
    oi["sum_open_interest"] = pd.to_numeric(oi["sum_open_interest"], errors="coerce")
    oi["sum_open_interest_value"] = pd.to_numeric(oi["sum_open_interest_value"], errors="coerce")
    oi = oi.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")

    df = k.join(oi[["sum_open_interest", "sum_open_interest_value"]], how="left")
    df[["sum_open_interest", "sum_open_interest_value"]] = df[["sum_open_interest", "sum_open_interest_value"]].ffill(limit=3)

    if funding_path and funding_path.exists():
        f = read_csv_gz(funding_path)
        f["timestamp"] = pd.to_datetime(f["funding_time"], unit="ms", utc=True)
        f["funding_rate"] = pd.to_numeric(f["funding_rate"], errors="coerce")
        f = f.sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")
        df = df.join(f[["funding_rate"]], how="left")
        df["funding_rate"] = df["funding_rate"].ffill()
    else:
        df["funding_rate"] = np.nan

    windows = CONFIG["features"]["oi_change_windows_hours"]
    for h in windows:
        p = int(h * 60 / BASE_MIN)
        df[f"price_chg_{h}h"] = pct_change_safe(df["close"], p)
        df[f"oi_chg_{h}h"] = pct_change_safe(df["sum_open_interest_value"], p)
        vol_now = df["quote_volume"].rolling(p, min_periods=max(2, p // 2)).sum()
        vol_prev = vol_now.shift(p)
        df[f"volume_accel_{h}h"] = vol_now / vol_prev - 1.0

    # OI acceleration: recent 1h pace vs prior 1h pace and vs 4h average pace.
    p1 = int(60 / BASE_MIN)
    p4 = int(4 * 60 / BASE_MIN)
    oi_1h = pct_change_safe(df["sum_open_interest_value"], p1)
    oi_prev_1h = pct_change_safe(df["sum_open_interest_value"].shift(p1), p1)
    oi_4h = pct_change_safe(df["sum_open_interest_value"], p4)
    df["oi_accel_1h"] = oi_1h - oi_prev_1h
    df["oi_pace_ratio_1h_vs_4h"] = oi_1h / (oi_4h / 4.0).replace(0, np.nan)

    # Price/OI elasticity and divergence.
    df["price_oi_elasticity_1h"] = df["price_chg_1h"] / df["oi_chg_1h"].replace(0, np.nan)
    df["price_oi_divergence_1h"] = np.sign(df["price_chg_1h"]) != np.sign(df["oi_chg_1h"])

    # Funding acceleration & z-score.
    funding_steps_24h = max(1, int(24 * 60 / BASE_MIN))
    funding_steps_7d = max(2, int(7 * 24 * 60 / BASE_MIN))
    df["funding_delta_24h"] = df["funding_rate"] - df["funding_rate"].shift(funding_steps_24h)
    f_mean = df["funding_rate"].rolling(funding_steps_7d, min_periods=funding_steps_24h).mean()
    f_std = df["funding_rate"].rolling(funding_steps_7d, min_periods=funding_steps_24h).std()
    df["funding_z_7d"] = (df["funding_rate"] - f_mean) / f_std.replace(0, np.nan)

    # Volatility compression: recent realized vol vs 7d rolling baseline.
    ret = df["close"].pct_change()
    p24 = int(24 * 60 / BASE_MIN)
    p7d = int(7 * 24 * 60 / BASE_MIN)
    df["rv_24h"] = ret.rolling(p24, min_periods=max(12, p24 // 3)).std()
    baseline = df["rv_24h"].rolling(p7d, min_periods=p24).median()
    df["vol_compression_ratio"] = df["rv_24h"] / baseline.replace(0, np.nan)

    return df


def latest_dataset_dirs():
    kroot = RAW / "klines" / "5m"
    if not kroot.exists():
        return []
    return sorted([p for p in kroot.iterdir() if p.is_dir()])


def main():
    dirs = latest_dataset_dirs()
    if not dirs:
        raise SystemExit("No raw datasets found. Run collect_binance_history.py first.")
    kdir = dirs[-1]
    dataset = kdir.name
    odir = RAW / "oi" / "5m" / dataset
    fdir = RAW / "funding" / dataset
    outdir = DERIVED / dataset
    outdir.mkdir(parents=True, exist_ok=True)

    written = 0
    for kp in sorted(kdir.glob("*.csv.gz")):
        symbol = kp.name.replace(".csv.gz", "")
        op = odir / kp.name
        fp = fdir / kp.name
        if not op.exists():
            continue
        df = build_symbol(kp, op, fp if fp.exists() else None)
        out = outdir / f"{symbol}.csv.gz"
        df.reset_index().to_csv(out, index=False, compression="gzip")
        written += 1
        print(symbol, len(df), out.relative_to(ROOT))
    print(f"wrote {written} feature files")


if __name__ == "__main__":
    main()
