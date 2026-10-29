from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FEATURE_ROOT = ROOT / "data" / "derived" / "features"
OUT_ROOT = ROOT / "data" / "derived" / "backtests"
CONFIG = json.loads((ROOT / "config" / "backtest.json").read_text(encoding="utf-8"))
BASE_MIN = CONFIG["base_interval_minutes"]


def forward_extremes(close: pd.Series, hours: int):
    n = int(hours * 60 / BASE_MIN)
    arr = close.to_numpy(dtype=float)
    max_ret = np.full(len(arr), np.nan)
    min_ret = np.full(len(arr), np.nan)
    for i in range(len(arr) - 1):
        end = min(len(arr), i + n + 1)
        future = arr[i + 1:end]
        if future.size == 0 or not np.isfinite(arr[i]) or arr[i] == 0:
            continue
        max_ret[i] = np.nanmax(future / arr[i] - 1.0)
        min_ret[i] = np.nanmin(future / arr[i] - 1.0)
    return max_ret, min_ret


def cooldown_events(mask: pd.Series, cooldown_hours: int) -> pd.Series:
    cooldown = int(cooldown_hours * 60 / BASE_MIN)
    keep = np.zeros(len(mask), dtype=bool)
    last = -10**9
    values = mask.fillna(False).to_numpy(dtype=bool)
    for i, flag in enumerate(values):
        if flag and i - last >= cooldown:
            keep[i] = True
            last = i
    return pd.Series(keep, index=mask.index)


def score_rows(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # Transparent first-pass research scores. These are hypotheses to test, not fixed trading rules.
    pump = pd.Series(0.0, index=out.index)
    dump = pd.Series(0.0, index=out.index)

    pump += (out.get("oi_accel_1h", 0) > 0.01).astype(float) * 20
    pump += (out.get("oi_pace_ratio_1h_vs_4h", 0) > 1.5).astype(float) * 15
    pump += (out.get("price_chg_1h", 0) > 0).astype(float) * 15
    pump += (out.get("volume_accel_1h", 0) > 0.25).astype(float) * 15
    pump += (out.get("funding_delta_24h", 0) < 0).astype(float) * 10
    pump += ((out.get("funding_z_7d", 0) < -1.5) & (out.get("price_chg_1h", 0) >= 0)).astype(float) * 15
    pump += (out.get("vol_compression_ratio", 1) < 0.8).astype(float) * 10

    dump += (out.get("oi_accel_1h", 0) > 0.01).astype(float) * 20
    dump += (out.get("oi_pace_ratio_1h_vs_4h", 0) > 1.5).astype(float) * 10
    dump += ((out.get("oi_chg_1h", 0) > 0) & (out.get("price_chg_1h", 0) <= 0)).astype(float) * 20
    dump += (out.get("funding_delta_24h", 0) > 0).astype(float) * 10
    dump += (out.get("funding_z_7d", 0) > 1.5).astype(float) * 15
    dump += (out.get("volume_accel_1h", 0) > 0.25).astype(float) * 15
    dump += (out.get("vol_compression_ratio", 1) < 0.8).astype(float) * 10

    out["pump_score_v0"] = pump.clip(0, 100)
    out["dump_score_v0"] = dump.clip(0, 100)
    return out


def evaluate_threshold(df: pd.DataFrame, score_col: str, event_col: str, thresholds=(50, 60, 70, 80)):
    rows = []
    actual = df[event_col].fillna(False).astype(bool)
    for t in thresholds:
        pred = df[score_col] >= t
        tp = int((pred & actual).sum())
        fp = int((pred & ~actual).sum())
        fn = int((~pred & actual).sum())
        precision = tp / (tp + fp) if tp + fp else np.nan
        recall = tp / (tp + fn) if tp + fn else np.nan
        rows.append({
            "score": score_col,
            "event": event_col,
            "threshold": t,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
        })
    return rows


def event_lead_times(df: pd.DataFrame, score_col: str, event_col: str, threshold: float, max_lookback_hours=24):
    lookback = int(max_lookback_hours * 60 / BASE_MIN)
    score = df[score_col].to_numpy(dtype=float)
    events = df[event_col].fillna(False).to_numpy(dtype=bool)
    leads = []
    for i, is_event in enumerate(events):
        if not is_event:
            continue
        start = max(0, i - lookback)
        hits = np.where(score[start:i + 1] >= threshold)[0]
        if len(hits):
            first = start + hits[0]
            leads.append((i - first) * BASE_MIN / 60)
    return leads


def main():
    dirs = sorted([p for p in FEATURE_ROOT.iterdir() if p.is_dir()]) if FEATURE_ROOT.exists() else []
    if not dirs:
        raise SystemExit("No feature dataset found. Run build_features.py first.")
    fdir = dirs[-1]
    dataset = fdir.name
    cfg_event = CONFIG["event"]
    pump_th = cfg_event["pump_threshold"]
    dump_th = cfg_event["dump_threshold"]
    horizon = cfg_event["forward_hours"]
    cooldown = cfg_event["cooldown_hours"]

    all_metrics = []
    event_rows = []
    for fp in sorted(fdir.glob("*.csv.gz")):
        symbol = fp.name.replace(".csv.gz", "")
        df = pd.read_csv(fp, compression="gzip")
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        max_ret, min_ret = forward_extremes(df["close"], horizon)
        df["forward_max_ret_24h"] = max_ret
        df["forward_min_ret_24h"] = min_ret
        raw_pump = df["forward_max_ret_24h"] >= pump_th
        raw_dump = df["forward_min_ret_24h"] <= dump_th
        df["pump_event"] = cooldown_events(raw_pump, cooldown)
        df["dump_event"] = cooldown_events(raw_dump, cooldown)
        df = score_rows(df)

        metrics = evaluate_threshold(df, "pump_score_v0", "pump_event")
        metrics += evaluate_threshold(df, "dump_score_v0", "dump_event")
        for m in metrics:
            m["symbol"] = symbol
            all_metrics.append(m)

        for score_col, event_col in [("pump_score_v0", "pump_event"), ("dump_score_v0", "dump_event")]:
            for t in [50, 60, 70, 80]:
                leads = event_lead_times(df, score_col, event_col, t)
                if leads:
                    event_rows.append({
                        "symbol": symbol,
                        "score": score_col,
                        "event": event_col,
                        "threshold": t,
                        "n_events_with_warning": len(leads),
                        "median_lead_hours": float(np.median(leads)),
                        "mean_lead_hours": float(np.mean(leads)),
                    })

    outdir = OUT_ROOT / dataset
    outdir.mkdir(parents=True, exist_ok=True)
    metrics_df = pd.DataFrame(all_metrics)
    lead_df = pd.DataFrame(event_rows)
    metrics_df.to_csv(outdir / "metrics_by_symbol.csv", index=False)
    lead_df.to_csv(outdir / "lead_times_by_symbol.csv", index=False)

    if not metrics_df.empty:
        summary = metrics_df.groupby(["score", "event", "threshold"], as_index=False)[["tp", "fp", "fn"]].sum()
        summary["precision"] = summary["tp"] / (summary["tp"] + summary["fp"]).replace(0, np.nan)
        summary["recall"] = summary["tp"] / (summary["tp"] + summary["fn"]).replace(0, np.nan)
        summary.to_csv(outdir / "summary.csv", index=False)
        print(summary.to_string(index=False))
    print(f"results: {outdir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
