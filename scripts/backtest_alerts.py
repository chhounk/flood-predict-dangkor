"""Backtest the two-tier alert dispatcher against 2024–2025 event validation.

Shows how many false alarms the persistence check eliminates vs single-run
threshold, and which flood events we'd miss.

Usage:
    python scripts/backtest_alerts.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src.config import OUTPUT_DIR  # noqa: E402

WATCH_THRESHOLD  = 0.10
ALERT_THRESHOLD  = 0.15
PERSISTENCE_RUNS = 2
FLOOD_EVENT_PCT  = 2.0
LOOKBACK_HOURS   = 72


def load_runs() -> pd.DataFrame:
    hist = OUTPUT_DIR / "history"
    rows = []
    for fp in sorted(hist.glob("202[45]-*.json")):
        stem = fp.stem
        run_id = stem[:10] + "T" + stem[11:13] + ":" + stem[14:16] + ":" + stem[17:19] + "Z"
        dt = datetime.fromisoformat(run_id.replace("Z", "+00:00"))
        with open(fp) as f:
            payload = json.load(f)
        if payload.get("models_used") != ["era5_reanalysis"]:
            continue
        cells = payload.get("cells", [])
        if not cells:
            continue
        probs = [c.get("peak_probability", 0.0) for c in cells]
        rows.append({
            "run_id":    run_id,
            "dt":        dt,
            "mean_prob": float(np.mean(probs)),
        })
    df = pd.DataFrame(rows).sort_values("dt").reset_index(drop=True)
    print(f"Loaded {len(df)} valid historical runs")
    return df


def load_s1_events() -> pd.DataFrame:
    csv = OUTPUT_DIR / "sentinel1_labels.csv"
    df = pd.read_csv(csv)
    daily = df.groupby("date_str").agg(
        n_cells=("flooded", "count"),
        n_flooded=("flooded", "sum"),
    ).reset_index()
    daily["pct_flooded"] = 100 * daily["n_flooded"] / daily["n_cells"]
    daily["flood_event"] = daily["pct_flooded"] >= FLOOD_EVENT_PCT
    daily["date"] = pd.to_datetime(daily["date_str"], utc=True)
    return daily.sort_values("date").reset_index(drop=True)


def runs_before(s1_dt: datetime, runs_df: pd.DataFrame) -> pd.DataFrame:
    window_start = s1_dt - timedelta(hours=LOOKBACK_HOURS)
    mask = (runs_df["dt"] >= window_start) & (runs_df["dt"] <= s1_dt)
    return runs_df[mask].sort_values("dt")


def check_persistence(preceding: pd.DataFrame, threshold: float, n: int) -> bool:
    """True if the last N runs ALL have mean_prob >= threshold."""
    if len(preceding) < n:
        return False
    last_n = preceding.tail(n)
    return bool((last_n["mean_prob"] >= threshold).all())


def main():
    runs_df  = load_runs()
    events   = load_s1_events()

    results = []
    for _, row in events.iterrows():
        preceding = runs_before(row["date"], runs_df)
        if preceding.empty:
            max_prob = 0.0
            watch    = False
            alert    = False
        else:
            max_prob = preceding["mean_prob"].max()
            watch    = max_prob >= WATCH_THRESHOLD
            alert    = check_persistence(preceding, ALERT_THRESHOLD, PERSISTENCE_RUNS)

        results.append({
            "date":        row["date_str"],
            "pct_flooded": round(row["pct_flooded"], 2),
            "flood_event": bool(row["flood_event"]),
            "n_runs":      len(preceding),
            "max_prob":    round(max_prob, 3),
            "watch":       watch,
            "alert":       alert,
        })

    df = pd.DataFrame(results)
    events_df = df[df["flood_event"]]
    dry_df    = df[~df["flood_event"]]

    n_events = len(events_df)
    n_dry    = len(dry_df)

    print(f"\n{'='*65}")
    print(f"ALERT BACKTEST — 2024+2025 Combined ({len(df)} S1 images)")
    print(f"{'='*65}")
    print(f"Flood events (>{FLOOD_EVENT_PCT}% cells): {n_events}")
    print(f"Dry days:                              {n_dry}")

    for label, watch_thr, alert_mode in [
        ("Single-run WATCH (≥0.10)",             True,  False),
        ("Single-run ALERT (≥0.15)",             False, False),
        ("Persistence ALERT (≥0.15 × 2 runs)",  False, True),
        ("Watch OR Persist-Alert",               True,  True),
    ]:
        if label == "Single-run WATCH (≥0.10)":
            e_warned = events_df["watch"]
            d_warned = dry_df["watch"]
        elif label == "Single-run ALERT (≥0.15)":
            e_warned = events_df["max_prob"] >= ALERT_THRESHOLD
            d_warned = dry_df["max_prob"] >= ALERT_THRESHOLD
        elif label == "Persistence ALERT (≥0.15 × 2 runs)":
            e_warned = events_df["alert"]
            d_warned = dry_df["alert"]
        else:  # Watch OR persist-alert
            e_warned = events_df["watch"] | events_df["alert"]
            d_warned = dry_df["watch"] | dry_df["alert"]

        tp = e_warned.sum()
        fn = (~e_warned).sum()
        fp = d_warned.sum()
        tn = (~d_warned).sum()
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1   = 2*prec*rec / (prec+rec) if (prec+rec) > 0 else 0.0
        print(f"\n  {label}")
        print(f"    TP={tp:3d}  FN={fn:3d}  FP={fp:3d}  TN={tn:3d}")
        print(f"    Precision={prec:.3f}  Recall={rec:.3f}  F1={f1:.3f}")

    # Show what gets lost with persistence vs single-run
    print(f"\n{'='*65}")
    print("Events MISSED by persistence check (caught by watch, not by alert):")
    missed_by_persist = events_df[events_df["watch"] & ~events_df["alert"]]
    if missed_by_persist.empty:
        print("  None — persistence check catches all watch-level events ✓")
    else:
        for _, r in missed_by_persist.sort_values("pct_flooded", ascending=False).iterrows():
            print(f"  {r['date']:12} {r['pct_flooded']:5.1f}% flooded  "
                  f"max_prob={r['max_prob']:.3f}  n_runs={r['n_runs']}")

    print(f"\nFalse alarms ELIMINATED by persistence (FP watch→alert):")
    fp_eliminated = dry_df[dry_df["watch"] & ~dry_df["alert"]]
    if fp_eliminated.empty:
        print("  None")
    else:
        for _, r in fp_eliminated.sort_values("max_prob", ascending=False).iterrows():
            print(f"  {r['date']:12} {r['pct_flooded']:5.1f}% flooded  max_prob={r['max_prob']:.3f}")

    # Recommended operating point
    print(f"\n{'='*65}")
    print("RECOMMENDATION")
    print(f"{'='*65}")
    print(f"  Use WATCH (≥0.10) for early heads-up to ground teams.")
    print(f"  Use ALERT (≥0.15 × 2 consecutive runs) to trigger")
    print(f"  resource pre-positioning — eliminates single-run spikes.")
    print(f"  Both signals are sent via Telegram with different urgency.")


if __name__ == "__main__":
    main()
