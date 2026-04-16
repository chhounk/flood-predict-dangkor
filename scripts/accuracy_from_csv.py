"""Recompute accuracy report from an already-generated sentinel1_labels.csv.

Faster than re-running the full GEE pipeline.

Usage:
    cd "/Users/kalim/Desktop/Flood Forecast"
    python scripts/accuracy_from_csv.py
"""
import json
import logging
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import OUTPUT_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_backfill_runs():
    hist = OUTPUT_DIR / "history"
    runs = []
    for fp in sorted(hist.glob("202[45]-*.json")):
        stem = fp.stem
        run_id = stem[:10] + "T" + stem[11:13] + ":" + stem[14:16] + ":" + stem[17:19] + "Z"
        runs.append({"run_id": run_id, "path": fp})
    return {r["run_id"]: r for r in runs}


def main():
    csv_path = OUTPUT_DIR / "sentinel1_labels.csv"
    if not csv_path.exists():
        logger.error("No labels CSV found at %s — run sentinel1_labels.py first", csv_path)
        sys.exit(1)

    labels_df = pd.read_csv(csv_path)
    logger.info("Loaded %d rows from %s", len(labels_df), csv_path)

    run_by_id = load_backfill_runs()
    needed = labels_df["matched_run_id"].dropna().unique()
    logger.info("Pre-loading %d matched run files...", len(needed))

    run_cell_preds = {}
    for run_id in needed:
        if run_id not in run_by_id:
            continue
        with open(run_by_id[run_id]["path"]) as f:
            payload = json.load(f)
        run_cell_preds[run_id] = {c["grid_id"]: c for c in payload.get("cells", [])}
    logger.info("Loaded %d runs", len(run_cell_preds))

    records = []
    for _, row in labels_df.iterrows():
        run_id = row.get("matched_run_id")
        if not run_id or run_id not in run_cell_preds:
            continue
        pred = run_cell_preds[run_id].get(row["grid_id"])
        if pred is None:
            continue
        records.append({
            "grid_id": row["grid_id"],
            "commune_id": row["commune_id"],
            "observed_flood": int(row["flooded"]),
            "predicted_prob": pred.get("peak_probability", 0.0),
            "predicted_level": pred.get("peak_level", 1),
        })

    if not records:
        logger.error("No matched records found")
        sys.exit(1)

    df = pd.DataFrame(records)
    logger.info("Matched %d cell observations", len(df))

    df["predicted_flood"] = (df["predicted_level"] >= 2).astype(int)

    tp = ((df["predicted_flood"] == 1) & (df["observed_flood"] == 1)).sum()
    fp_ = ((df["predicted_flood"] == 1) & (df["observed_flood"] == 0)).sum()
    fn = ((df["predicted_flood"] == 0) & (df["observed_flood"] == 1)).sum()
    tn = ((df["predicted_flood"] == 0) & (df["observed_flood"] == 0)).sum()

    precision = tp / (tp + fp_) if (tp + fp_) > 0 else 0.0
    recall    = tp / (tp + fn)  if (tp + fn)  > 0 else 0.0
    f1        = 2*precision*recall / (precision+recall) if (precision+recall) > 0 else 0.0
    brier     = ((df["predicted_prob"] - df["observed_flood"]) ** 2).mean()

    print("\n=== ACCURACY REPORT (2024 season) ===")
    print(f"  S1 images:             {labels_df['date_str'].nunique()}")
    print(f"  Cell observations:     {len(df)}")
    print(f"  Observed flood rate:   {df['observed_flood'].mean():.1%}")
    print(f"  Precision (L2+):       {precision:.3f}")
    print(f"  Recall    (L2+):       {recall:.3f}")
    print(f"  F1 score:              {f1:.3f}")
    print(f"  Brier score:           {brier:.4f}  (0=perfect, 0.25=random)")
    print(f"  Confusion: TP={tp} FP={fp_} FN={fn} TN={tn}")

    print("\nPer-commune (sorted by recall):")
    for commune_id, cdf in sorted(
        df.groupby("commune_id"),
        key=lambda x: -(x[1]["predicted_flood"] & x[1]["observed_flood"]).sum()
                       / max((x[1]["observed_flood"]).sum(), 1)
    ):
        c_tp = ((cdf["predicted_flood"]==1) & (cdf["observed_flood"]==1)).sum()
        c_fp = ((cdf["predicted_flood"]==1) & (cdf["observed_flood"]==0)).sum()
        c_fn = ((cdf["predicted_flood"]==0) & (cdf["observed_flood"]==1)).sum()
        c_prec = c_tp/(c_tp+c_fp) if (c_tp+c_fp) > 0 else 0.0
        c_rec  = c_tp/(c_tp+c_fn) if (c_tp+c_fn) > 0 else 0.0
        flood_rate = cdf["observed_flood"].mean()
        print(f"  {commune_id:22}  recall={c_rec:.2f}  prec={c_prec:.2f}  flood_rate={flood_rate:.2f}  n={len(cdf)}")

    report = {
        "n_cell_observations": len(df),
        "n_s1_images": int(labels_df["date_str"].nunique()),
        "flood_rate_observed": round(float(df["observed_flood"].mean()), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "brier_score": round(float(brier), 4),
        "confusion_matrix": {"TP": int(tp), "FP": int(fp_), "FN": int(fn), "TN": int(tn)},
    }
    report_path = OUTPUT_DIR / "accuracy_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Saved report to %s", report_path)


if __name__ == "__main__":
    main()
