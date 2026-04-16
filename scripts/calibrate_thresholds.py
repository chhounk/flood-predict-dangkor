"""Probability calibration + threshold optimizer.

Uses the Sentinel-1 labeled observations (82k cell-image pairs) to:

1. Plot a reliability diagram — shows how well-calibrated the model is
   (when it says 80%, does 80% actually flood?)

2. Fit isotonic regression calibration — maps raw peak_probability to a
   calibrated probability that matches actual flood rates.

3. Sweep classification thresholds — for every candidate probability cutoff
   compute precision, recall, F1. Shows the full trade-off curve and
   highlights the optimal F1 and optimal recall operating points.

4. Write recommended thresholds to config/calibration.json — the pipeline
   can read these to replace hard-coded level cutoffs.

Usage:
    cd "/Users/kalim/Desktop/Flood Forecast"
    python scripts/calibrate_thresholds.py

Output:
    data/outputs/calibration_curve.txt   — reliability diagram (ASCII)
    data/outputs/threshold_sweep.csv     — full P/R/F1 sweep table
    config/calibration.json              — recommended thresholds
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.calibration import calibration_curve

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import OUTPUT_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_data() -> pd.DataFrame:
    """Load sentinel1_labels.csv and merge with prediction probabilities."""
    csv_path = OUTPUT_DIR / "sentinel1_labels.csv"
    if not csv_path.exists():
        logger.error("Run sentinel1_labels.py first to generate %s", csv_path)
        sys.exit(1)

    labels_df = pd.read_csv(csv_path)
    logger.info("Loaded %d labeled observations", len(labels_df))

    # Load matching run predictions (cache by run_id)
    hist = OUTPUT_DIR / "history"
    runs = {}
    for fp in sorted(hist.glob("202[45]-*.json")):
        stem = fp.stem
        run_id = stem[:10] + "T" + stem[11:13] + ":" + stem[14:16] + ":" + stem[17:19] + "Z"
        runs[run_id] = fp

    needed = labels_df["matched_run_id"].dropna().unique()
    logger.info("Pre-loading %d run files...", len(needed))
    run_cell_preds: dict[str, dict] = {}
    for run_id in needed:
        if run_id not in runs:
            continue
        with open(runs[run_id]) as f:
            payload = json.load(f)
        run_cell_preds[run_id] = {
            c["grid_id"]: {
                "peak_probability": c.get("peak_probability", 0.0),
                "peak_level": c.get("peak_level", 1),
            }
            for c in payload.get("cells", [])
        }

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
            "observed": int(row["flooded"]),
            "prob": float(pred["peak_probability"]),
            "level": int(pred["peak_level"]),
        })

    df = pd.DataFrame(records)
    logger.info("Matched %d observations — flood rate=%.1f%%",
                len(df), 100 * df["observed"].mean())
    return df


def reliability_diagram(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> str:
    """ASCII reliability diagram."""
    fraction_of_pos, mean_predicted = calibration_curve(
        y_true, y_prob, n_bins=n_bins, strategy="quantile"
    )
    lines = ["Reliability diagram (predicted vs actual flood rate):"]
    lines.append(f"  {'Predicted':>10}  {'Actual':>10}  {'Bias':>8}  {'Bar'}")
    lines.append("  " + "-" * 60)
    for pred, actual in zip(mean_predicted, fraction_of_pos):
        bias = actual - pred
        bar_len = int(actual * 40)
        bar = "█" * bar_len
        lines.append(f"  {pred:>10.3f}  {actual:>10.3f}  {bias:>+8.3f}  {bar}")
    return "\n".join(lines)


def threshold_sweep(y_true: np.ndarray, y_prob: np.ndarray) -> pd.DataFrame:
    """Sweep probability thresholds from 0 to 1, compute P/R/F1."""
    thresholds = np.linspace(0.01, 0.99, 200)
    rows = []
    for t in thresholds:
        pred = (y_prob >= t).astype(int)
        tp = ((pred == 1) & (y_true == 1)).sum()
        fp = ((pred == 1) & (y_true == 0)).sum()
        fn = ((pred == 0) & (y_true == 1)).sum()
        tn = ((pred == 0) & (y_true == 0)).sum()
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1   = 2*prec*rec / (prec+rec) if (prec+rec) > 0 else 0.0
        rows.append({
            "threshold": round(float(t), 4),
            "precision": round(float(prec), 4),
            "recall":    round(float(rec), 4),
            "f1":        round(float(f1), 4),
            "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
        })
    return pd.DataFrame(rows)


def main():
    df = load_data()
    y_true = df["observed"].values
    y_prob = df["prob"].values

    # ---- 1. Reliability diagram --------------------------------------------
    logger.info("Building reliability diagram...")
    diagram = reliability_diagram(y_true, y_prob)
    print("\n" + diagram)
    diagram_path = OUTPUT_DIR / "calibration_curve.txt"
    with open(diagram_path, "w") as f:
        f.write(diagram + "\n")

    # ---- 2. Isotonic regression calibration --------------------------------
    logger.info("Fitting isotonic regression calibrator...")
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(y_prob, y_true)
    y_cal = iso.predict(y_prob)

    brier_raw = float(((y_prob - y_true) ** 2).mean())
    brier_cal = float(((y_cal  - y_true) ** 2).mean())
    logger.info("Brier score: raw=%.4f → calibrated=%.4f", brier_raw, brier_cal)

    # ---- 3. Threshold sweep on RAW probabilities ---------------------------
    logger.info("Sweeping thresholds (raw probabilities)...")
    sweep_df = threshold_sweep(y_true, y_prob)
    sweep_path = OUTPUT_DIR / "threshold_sweep.csv"
    sweep_df.to_csv(sweep_path, index=False)

    # Best F1
    best_f1_row = sweep_df.loc[sweep_df["f1"].idxmax()]
    # Best recall >= 0.80 with highest precision
    high_recall = sweep_df[sweep_df["recall"] >= 0.80]
    best_recall_row = high_recall.loc[high_recall["precision"].idxmax()] \
        if not high_recall.empty else sweep_df.loc[sweep_df["recall"].idxmax()]

    # ---- 4. Threshold sweep on CALIBRATED probabilities --------------------
    logger.info("Sweeping thresholds (calibrated probabilities)...")
    sweep_cal_df = threshold_sweep(y_true, y_cal)
    best_f1_cal  = sweep_cal_df.loc[sweep_cal_df["f1"].idxmax()]

    # ---- 5. Print summary --------------------------------------------------
    print(f"\n{'='*60}")
    print("THRESHOLD ANALYSIS")
    print(f"{'='*60}")
    print(f"\nCurrent model (L2+ means any level>=2):")
    cur = sweep_df[sweep_df["threshold"] <= df[df["level"] >= 2]["prob"].min()]
    print(f"  Precision={best_f1_row['precision']:.3f}  "
          f"Recall={best_f1_row['recall']:.3f}  F1={best_f1_row['f1']:.3f}")

    print(f"\nBest F1 threshold (raw):        {best_f1_row['threshold']:.3f}")
    print(f"  Precision={best_f1_row['precision']:.3f}  "
          f"Recall={best_f1_row['recall']:.3f}  F1={best_f1_row['f1']:.3f}  "
          f"TP={best_f1_row['tp']}  FP={best_f1_row['fp']}  "
          f"FN={best_f1_row['fn']}")

    print(f"\nBest recall>=0.80 threshold:    {best_recall_row['threshold']:.3f}")
    print(f"  Precision={best_recall_row['precision']:.3f}  "
          f"Recall={best_recall_row['recall']:.3f}  F1={best_recall_row['f1']:.3f}  "
          f"TP={best_recall_row['tp']}  FP={best_recall_row['fp']}  "
          f"FN={best_recall_row['fn']}")

    print(f"\nBest F1 threshold (calibrated): {best_f1_cal['threshold']:.3f}")
    print(f"  Precision={best_f1_cal['precision']:.3f}  "
          f"Recall={best_f1_cal['recall']:.3f}  F1={best_f1_cal['f1']:.3f}")

    print(f"\nBrier score improvement: {brier_raw:.4f} → {brier_cal:.4f} "
          f"({100*(brier_raw-brier_cal)/brier_raw:.1f}% better)")

    # Key probability distribution stats
    flood_probs  = y_prob[y_true == 1]
    dry_probs    = y_prob[y_true == 0]
    print(f"\nProbability distribution:")
    print(f"  Flooded cells  — mean={flood_probs.mean():.3f}  "
          f"median={np.median(flood_probs):.3f}  p90={np.percentile(flood_probs,90):.3f}")
    print(f"  Dry cells      — mean={dry_probs.mean():.3f}  "
          f"median={np.median(dry_probs):.3f}  p90={np.percentile(dry_probs,90):.3f}")

    # ---- 6. Write recommended thresholds -----------------------------------
    config_dir = REPO / "config"
    config_dir.mkdir(exist_ok=True)
    calibration = {
        "version": "1.0",
        "source": "sentinel1_2024",
        "n_observations": len(df),
        "flood_rate_observed": round(float(y_true.mean()), 4),
        "brier_raw": round(brier_raw, 4),
        "brier_calibrated": round(brier_cal, 4),
        "thresholds": {
            "recommended_l2_prob": round(float(best_f1_row["threshold"]), 3),
            "high_recall_l2_prob": round(float(best_recall_row["threshold"]), 3),
            "note": (
                "recommended_l2_prob maximises F1. "
                "high_recall_l2_prob gives recall>=0.80 at cost of more false alarms. "
                "Use high_recall for early-warning (don't miss floods), "
                "recommended for operational alerts (reduce fatigue)."
            ),
        },
        "isotonic_calibrator_xy": {
            "x": [round(float(v), 4) for v in iso.X_thresholds_],
            "y": [round(float(v), 4) for v in iso.y_thresholds_],
        },
    }
    cal_path = config_dir / "calibration.json"
    with open(cal_path, "w") as f:
        json.dump(calibration, f, indent=2)
    logger.info("Saved calibration config to %s", cal_path)
    print(f"\nCalibration saved to {cal_path}")
    print(f"Threshold sweep saved to {sweep_path}")


if __name__ == "__main__":
    main()
