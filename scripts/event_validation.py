"""Event-level validation: did the model predict flood EVENTS correctly?

Cell-level instantaneous accuracy is the wrong metric for a 72h early warning
system. The right question is:

  "On days when S1 confirmed significant flooding, did our model issue an
   elevated warning in the 72 hours before the satellite passed?"

This script:
1. Classifies each S1 overpass as a FLOOD EVENT day (>N% of district cells
   flooded) or a DRY day.
2. For each flood-event day, looks back 72h to find whether any of our
   backfill predictions had district-level peak_probability above a threshold.
3. Computes event-level precision, recall, lead time.
4. Shows which specific events were caught vs missed.

Usage:
    cd "/Users/kalim/Desktop/Flood Forecast"
    python scripts/event_validation.py
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import OUTPUT_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# A day is a "flood event" if this % of cells show flooding per S1
FLOOD_EVENT_PCT = 2.0   # 2% = ~39 cells out of 1956

# Model "warned" if district-wide mean peak_probability exceeded this in
# the 72h window before the S1 overpass
WARN_THRESHOLD = 0.30

# Lookback window: how many hours before S1 to check model runs
LOOKBACK_HOURS = 72


def load_s1_daily(csv_path: Path) -> pd.DataFrame:
    """Aggregate cell-level labels to daily district-level flood extent."""
    df = pd.read_csv(csv_path)
    daily = df.groupby("date_str").agg(
        n_cells=("flooded", "count"),
        n_flooded=("flooded", "sum"),
        mean_anomaly=("anomaly_db", "mean"),
    ).reset_index()
    daily["pct_flooded"] = 100 * daily["n_flooded"] / daily["n_cells"]
    daily["flood_event"] = daily["pct_flooded"] >= FLOOD_EVENT_PCT
    daily["date"] = pd.to_datetime(daily["date_str"], utc=True)
    return daily.sort_values("date").reset_index(drop=True)


def load_runs() -> list[dict]:
    """Load backfill runs with district-level summary stats."""
    hist = OUTPUT_DIR / "history"
    runs = []
    for fp in sorted(hist.glob("202[45]-*.json")):
        stem = fp.stem
        run_id = stem[:10] + "T" + stem[11:13] + ":" + stem[14:16] + ":" + stem[17:19] + "Z"
        dt = datetime.fromisoformat(run_id.replace("Z", "+00:00"))

        with open(fp) as f:
            payload = json.load(f)

        # Skip placeholders
        if payload.get("models_used") != ["era5_reanalysis"]:
            continue

        cells = payload.get("cells", [])
        if not cells:
            continue

        probs = [c.get("peak_probability", 0.0) for c in cells]
        levels = [c.get("peak_level", 1) for c in cells]

        runs.append({
            "run_id": run_id,
            "dt": dt,
            "mean_prob": float(np.mean(probs)),
            "max_prob": float(np.max(probs)),
            "pct_l2_plus": 100 * sum(1 for l in levels if l >= 2) / len(levels),
            "pct_l3_plus": 100 * sum(1 for l in levels if l >= 3) / len(levels),
        })
    logger.info("Loaded %d valid runs", len(runs))
    return runs


def find_runs_before(s1_dt: datetime, runs: list[dict], hours: int) -> list[dict]:
    """Find all runs issued in the [s1_dt - hours, s1_dt] window."""
    window_start = s1_dt - timedelta(hours=hours)
    return [r for r in runs if window_start <= r["dt"] <= s1_dt]


def main():
    csv_path = OUTPUT_DIR / "sentinel1_labels.csv"
    if not csv_path.exists():
        logger.error("Run sentinel1_labels.py first")
        sys.exit(1)

    # ---- Load data ---------------------------------------------------------
    daily = load_s1_daily(csv_path)
    runs = load_runs()

    n_events = daily["flood_event"].sum()
    n_dry    = (~daily["flood_event"]).sum()
    logger.info("S1 images: %d total, %d flood events (>%.0f%% cells), %d dry days",
                len(daily), n_events, FLOOD_EVENT_PCT, n_dry)

    # ---- Event-level validation --------------------------------------------
    results = []
    for _, row in daily.iterrows():
        s1_dt = row["date"]
        preceding = find_runs_before(s1_dt, runs, LOOKBACK_HOURS)

        if not preceding:
            warned = False
            max_mean_prob = 0.0
            max_pct_l2 = 0.0
            lead_hours = None
        else:
            max_mean_prob = max(r["mean_prob"] for r in preceding)
            max_pct_l2   = max(r["pct_l2_plus"] for r in preceding)
            warned = max_mean_prob >= WARN_THRESHOLD
            if warned:
                # Which run first triggered the warning?
                first_warn = next((r for r in sorted(preceding, key=lambda x: x["dt"])
                                   if r["mean_prob"] >= WARN_THRESHOLD), None)
                lead_hours = (s1_dt - first_warn["dt"]).total_seconds() / 3600 \
                    if first_warn else None
            else:
                lead_hours = None

        results.append({
            "date": row["date_str"],
            "pct_flooded": round(row["pct_flooded"], 2),
            "n_flooded_cells": int(row["n_flooded"]),
            "flood_event": bool(row["flood_event"]),
            "n_preceding_runs": len(preceding),
            "max_mean_prob": round(max_mean_prob, 3),
            "max_pct_l2": round(max_pct_l2, 1),
            "warned": warned,
            "lead_hours": round(lead_hours, 1) if lead_hours else None,
        })

    res_df = pd.DataFrame(results)

    # ---- Metrics -----------------------------------------------------------
    event_rows = res_df[res_df["flood_event"]]
    dry_rows   = res_df[~res_df["flood_event"]]

    tp = (event_rows["warned"]).sum()
    fn = (~event_rows["warned"]).sum()
    fp = (dry_rows["warned"]).sum()
    tn = (~dry_rows["warned"]).sum()

    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1   = 2*prec*rec / (prec+rec) if (prec+rec) > 0 else 0.0

    lead_times = event_rows[event_rows["warned"]]["lead_hours"].dropna()

    print("\n" + "="*60)
    print(f"EVENT-LEVEL VALIDATION (warn_threshold={WARN_THRESHOLD})")
    print("="*60)
    print(f"\nS1 flood events detected (>{FLOOD_EVENT_PCT}% cells): {n_events}")
    print(f"Dry days:                                              {n_dry}")
    print(f"\nEvent-level confusion matrix:")
    print(f"  TP (event caught):      {tp:3d}")
    print(f"  FN (event missed):      {fn:3d}")
    print(f"  FP (false alarm):       {fp:3d}")
    print(f"  TN (correctly quiet):   {tn:3d}")
    print(f"\nPrecision:  {prec:.3f}")
    print(f"Recall:     {rec:.3f}")
    print(f"F1:         {f1:.3f}")
    if len(lead_times) > 0:
        print(f"\nLead time for caught events:")
        print(f"  Mean={lead_times.mean():.0f}h  Median={lead_times.median():.0f}h  "
              f"Min={lead_times.min():.0f}h  Max={lead_times.max():.0f}h")

    # ---- Flood event detail ------------------------------------------------
    print(f"\nFlood events detail:")
    print(f"  {'Date':12}  {'%Flooded':>8}  {'Cells':>6}  {'Warned':>7}  "
          f"{'MaxProb':>8}  {'Lead':>6}")
    print("  " + "-"*60)
    for _, r in event_rows.sort_values("pct_flooded", ascending=False).iterrows():
        warn_str = "✓" if r["warned"] else "✗"
        lead_str = f"{r['lead_hours']:.0f}h" if r["lead_hours"] else "—"
        print(f"  {r['date']:12}  {r['pct_flooded']:>8.1f}%  {r['n_flooded_cells']:>6d}  "
              f"  {warn_str:>5}  {r['max_mean_prob']:>8.3f}  {lead_str:>6}")

    # ---- Threshold sweep at event level ------------------------------------
    print(f"\nEvent-level threshold sweep:")
    print(f"  {'Threshold':>10}  {'Precision':>10}  {'Recall':>8}  {'F1':>6}  {'TP':>4}  {'FP':>4}")
    for t in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]:
        w_events = (event_rows["max_mean_prob"] >= t).sum()
        w_dry    = (dry_rows["max_mean_prob"] >= t).sum()
        miss     = n_events - w_events
        p = w_events / (w_events + w_dry) if (w_events + w_dry) > 0 else 0
        r = w_events / n_events if n_events > 0 else 0
        f = 2*p*r/(p+r) if (p+r) > 0 else 0
        print(f"  {t:>10.2f}  {p:>10.3f}  {r:>8.3f}  {f:>6.3f}  {int(w_events):>4}  {int(w_dry):>4}")

    # Save results
    out_path = OUTPUT_DIR / "event_validation.csv"
    res_df.to_csv(out_path, index=False)
    logger.info("Saved event validation to %s", out_path)


if __name__ == "__main__":
    main()
