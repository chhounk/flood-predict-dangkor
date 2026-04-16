"""Sentinel-1 SAR flood labeling pipeline.

For every Sentinel-1 overpass over Dangkor District during the 2024 and 2025
rainy seasons, compute which grid cells had detectable surface water (i.e.,
actually flooded), then write those observations to the Supabase `observations`
table and produce an accuracy report against our backfill predictions.

Method
------
Change detection on VV backscatter (dB):
  1. Build a dry-season baseline (Nov-Apr preceding each wet season) —
     mean VV over each cell when the ground is definitely NOT flooded.
  2. For each wet-season image: anomaly = VV_image - VV_baseline.
     A cell is flagged as flooded if:
       (a) anomaly < ANOMALY_THRESHOLD_DB (drop in backscatter = open water)
       AND (b) not permanently covered by water (JRC mask).
  3. Reduce over each 250m cell (centroid + 100m buffer) to get mean VV.

Output
------
  - data/outputs/sentinel1_labels.csv   — raw per-cell-per-image table
  - data/outputs/accuracy_report.json  — precision/recall/Brier score

Usage
-----
    cd "/Users/kalim/Desktop/Flood Forecast"
    export SUPABASE_URL=...
    export SUPABASE_SERVICE_KEY=...
    python scripts/sentinel1_labels.py
    python scripts/sentinel1_labels.py --dry-run   # skip Supabase write
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ee
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import OUTPUT_DIR, STATIC_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---- Config ----------------------------------------------------------------
GEE_PROJECT = "flood-predict-dangkor"
DANGKOR_BBOX = [104.776, 11.417, 104.928, 11.528]   # [W, S, E, N]

# Wet seasons to label
WET_SEASONS = [
    ("2024-05-01", "2024-10-31"),
    ("2025-05-01", "2025-10-31"),
]

# Dry-season baselines (preceding each wet season)
DRY_BASELINES = [
    ("2023-11-01", "2024-04-30"),
    ("2024-11-01", "2025-04-30"),
]

# Flood detection threshold: if cell VV drops > this many dB below baseline
ANOMALY_THRESHOLD_DB = -3.0

# Cell buffer radius in metres for GEE reduceRegions
CELL_BUFFER_M = 100

# Backfill run matching: max hours between S1 overpass and nearest run
MAX_MATCH_HOURS = 72


# ---- GEE helpers -----------------------------------------------------------

def init_ee():
    ee.Initialize(project=GEE_PROJECT)
    logger.info("GEE initialised (project=%s)", GEE_PROJECT)


def dangkor_geom():
    w, s, e, n = DANGKOR_BBOX
    return ee.Geometry.Rectangle([w, s, e, n])


def build_cell_fc(grid_path: Path) -> ee.FeatureCollection:
    """Create a GEE FeatureCollection of buffered cell centroids."""
    with open(grid_path) as f:
        grid = json.load(f)

    features = []
    for feat in grid["features"]:
        props = feat["properties"]
        lat = props.get("centroid_lat")
        lon = props.get("centroid_lon")
        if lat is None or lon is None:
            continue
        pt = ee.Geometry.Point([lon, lat]).buffer(CELL_BUFFER_M)
        features.append(ee.Feature(pt, {
            "grid_id": props["grid_id"],
            "commune_id": props.get("commune_id", "UNKNOWN"),
        }))

    logger.info("Built cell FeatureCollection: %d cells", len(features))
    return ee.FeatureCollection(features)


def build_baseline(dry_start: str, dry_end: str, geom) -> ee.Image:
    """Compute mean VV (dB) over the dry season as flood-free reference."""
    col = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(geom)
        .filterDate(dry_start, dry_end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV")
    )
    count = col.size().getInfo()
    logger.info("Dry baseline %s–%s: %d S1 images", dry_start, dry_end, count)
    return col.mean()


def get_wet_images(wet_start: str, wet_end: str, geom) -> list[dict]:
    """Return list of {date, ee.Image} for wet-season S1 overpasses."""
    col = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(geom)
        .filterDate(wet_start, wet_end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV")
    )
    ids = col.aggregate_array("system:index").getInfo()
    dates = col.aggregate_array("system:time_start").getInfo()
    images = col.toList(col.size())

    result = []
    for i, (idx, ms) in enumerate(zip(ids, dates)):
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        result.append({
            "date": dt,
            "date_str": dt.strftime("%Y-%m-%d"),
            "image": ee.Image(images.get(i)),
        })
    logger.info("Wet season %s–%s: %d S1 overpasses", wet_start, wet_end, len(result))
    return result


def label_image(
    img: ee.Image,
    baseline: ee.Image,
    cell_fc: ee.FeatureCollection,
) -> list[dict]:
    """
    For one S1 image, compute per-cell VV anomaly and flood flag.
    Returns a list of dicts: {grid_id, commune_id, vv_db, anomaly_db, flooded}.
    """
    anomaly = img.subtract(baseline).rename("anomaly")

    # JRC permanent water mask — exclude areas always covered by water
    jrc = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence")
    perm_water = jrc.gt(80)  # >80% occurrence = permanent water
    not_perm = perm_water.Not()

    # Flood = anomaly drops below threshold AND not permanently wet
    flood_mask = anomaly.lt(ANOMALY_THRESHOLD_DB).And(not_perm)

    # Stack: anomaly + flood flag
    stack = anomaly.addBands(flood_mask.rename("flood_flag"))

    reduced = stack.reduceRegions(
        collection=cell_fc,
        reducer=ee.Reducer.mean(),
        scale=30,           # S1 native resolution
        tileScale=4,
    )

    rows = reduced.getInfo()["features"]
    out = []
    for r in rows:
        p = r["properties"]
        anomaly_db = p.get("anomaly")      # None if cell outside swath
        flood_val = p.get("flood_flag")    # None if cell outside swath
        out.append({
            "grid_id": p.get("grid_id"),
            "commune_id": p.get("commune_id"),
            "anomaly_db": anomaly_db,
            # No data → assume not flooded (conservative)
            "flooded": bool(flood_val is not None and flood_val > 0.5),
        })
    return out


# ---- Backfill run matching -------------------------------------------------

def load_backfill_runs() -> list[dict]:
    """Load all historical run_ids + timestamps from local archive."""
    hist = OUTPUT_DIR / "history"
    runs = []
    for fp in sorted(hist.glob("202[45]-*.json")):
        # parse run_id from file
        stem = fp.stem  # e.g. 2024-07-15T12-00-00Z
        run_id = stem[:10] + "T" + stem[11:13] + ":" + stem[14:16] + ":" + stem[17:19] + "Z"
        dt = datetime.fromisoformat(run_id.replace("Z", "+00:00"))
        runs.append({"run_id": run_id, "dt": dt, "path": fp})
    logger.info("Loaded %d backfill runs", len(runs))
    return runs


def nearest_run(s1_date: datetime, runs: list[dict]) -> dict | None:
    """Find the backfill run closest in time to an S1 overpass date."""
    best = None
    best_delta = timedelta(hours=MAX_MATCH_HOURS + 1)
    for r in runs:
        delta = abs(s1_date - r["dt"])
        if delta < best_delta:
            best_delta = delta
            best = r
    if best and best_delta <= timedelta(hours=MAX_MATCH_HOURS):
        return best, best_delta
    return None, None


# ---- Accuracy metrics ------------------------------------------------------

def compute_accuracy(labels_df: pd.DataFrame, runs: list[dict]) -> dict:
    """
    Match S1 flood labels to nearest backfill run predictions,
    compute precision, recall, F1, Brier score at commune level.
    """
    run_by_id = {r["run_id"]: r for r in runs}

    # Pre-load each unique matched run once (not once per row)
    needed_run_ids = labels_df["matched_run_id"].dropna().unique()
    logger.info("Loading %d matched run JSON files...", len(needed_run_ids))
    run_cell_preds: dict[str, dict] = {}
    for run_id in needed_run_ids:
        if run_id not in run_by_id:
            continue
        fp = run_by_id[run_id]["path"]
        with open(fp) as f:
            payload = json.load(f)
        run_cell_preds[run_id] = {c["grid_id"]: c for c in payload.get("cells", [])}
    logger.info("Loaded predictions for %d runs", len(run_cell_preds))

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
            "date": row["date"],
            "observed_flood": int(row["flooded"]),
            "predicted_prob": pred.get("peak_probability", 0.0),
            "predicted_level": pred.get("peak_level", 1),
        })

    if not records:
        return {"error": "no matched records"}

    df = pd.DataFrame(records)

    # Binary classification: predict flood if peak_level >= 2
    df["predicted_flood"] = (df["predicted_level"] >= 2).astype(int)

    tp = ((df["predicted_flood"] == 1) & (df["observed_flood"] == 1)).sum()
    fp_ = ((df["predicted_flood"] == 1) & (df["observed_flood"] == 0)).sum()
    fn = ((df["predicted_flood"] == 0) & (df["observed_flood"] == 1)).sum()
    tn = ((df["predicted_flood"] == 0) & (df["observed_flood"] == 0)).sum()

    precision = tp / (tp + fp_) if (tp + fp_) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    brier = ((df["predicted_prob"] - df["observed_flood"]) ** 2).mean()

    flood_rate = df["observed_flood"].mean()

    report = {
        "n_cell_observations": len(df),
        "n_s1_images": labels_df["date"].nunique(),
        "flood_rate_observed": round(float(flood_rate), 4),
        "confusion_matrix": {"TP": int(tp), "FP": int(fp_), "FN": int(fn), "TN": int(tn)},
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "brier_score": round(float(brier), 4),
    }

    # Per-commune summary
    commune_stats = []
    for commune_id, cdf in df.groupby("commune_id"):
        c_tp = ((cdf["predicted_flood"] == 1) & (cdf["observed_flood"] == 1)).sum()
        c_fp = ((cdf["predicted_flood"] == 1) & (cdf["observed_flood"] == 0)).sum()
        c_fn = ((cdf["predicted_flood"] == 0) & (cdf["observed_flood"] == 1)).sum()
        c_prec = c_tp / (c_tp + c_fp) if (c_tp + c_fp) > 0 else 0.0
        c_rec = c_tp / (c_tp + c_fn) if (c_tp + c_fn) > 0 else 0.0
        commune_stats.append({
            "commune_id": commune_id,
            "precision": round(float(c_prec), 3),
            "recall": round(float(c_rec), 3),
            "observed_flood_rate": round(float(cdf["observed_flood"].mean()), 3),
            "n_obs": len(cdf),
        })
    commune_stats.sort(key=lambda x: -x["recall"])
    report["commune_stats"] = commune_stats

    return report


# ---- Supabase write --------------------------------------------------------

def write_to_supabase(labels_df: pd.DataFrame):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        logger.warning("SUPABASE creds not set — skipping DB write")
        return
    from supabase import create_client
    client = create_client(url, key)

    rows = []
    for _, r in labels_df.iterrows():
        if r["grid_id"] is None:
            continue
        rows.append({
            "observed_at": r["date"].isoformat(),
            "source": "sentinel1",
            "grid_id": r["grid_id"],
            "commune_id": r["commune_id"],
            "flood_flag": bool(r["flooded"]),
            "notes": f"anomaly_db={r['anomaly_db']:.2f}" if r["anomaly_db"] else None,
        })

    # Batch upsert in chunks of 500
    inserted = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i+500]
        client.table("observations").upsert(batch).execute()
        inserted += len(batch)
    logger.info("Supabase: inserted %d observation rows", inserted)


# ---- Main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Skip Supabase write, just print accuracy report")
    ap.add_argument("--season", choices=["2024", "2025", "both"], default="both")
    args = ap.parse_args()

    init_ee()
    geom = dangkor_geom()
    grid_path = STATIC_DIR / "grid.geojson"
    cell_fc = build_cell_fc(grid_path)
    runs = load_backfill_runs()

    seasons = WET_SEASONS if args.season == "both" else \
        [WET_SEASONS[0]] if args.season == "2024" else [WET_SEASONS[1]]
    baselines = DRY_BASELINES if args.season == "both" else \
        [DRY_BASELINES[0]] if args.season == "2024" else [DRY_BASELINES[1]]

    all_rows: list[dict] = []

    for (wet_start, wet_end), (dry_start, dry_end) in zip(seasons, baselines):
        logger.info("=== Season %s ===", wet_start[:4])
        baseline = build_baseline(dry_start, dry_end, geom)
        images = get_wet_images(wet_start, wet_end, geom)

        for i, img_info in enumerate(images, 1):
            s1_date = img_info["date"]
            date_str = img_info["date_str"]
            logger.info("[%d/%d] Processing S1 image %s", i, len(images), date_str)

            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(label_image, img_info["image"], baseline, cell_fc)
                    cell_labels = future.result(timeout=90)
            except FuturesTimeout:
                logger.warning("  S1 %s timed out after 90s — skipping", date_str)
                continue
            except Exception as e:
                logger.warning("  S1 %s failed: %s", date_str, e)
                continue

            # Match to nearest backfill run
            matched_run, delta = nearest_run(s1_date, runs)
            run_id = matched_run["run_id"] if matched_run else None
            delta_h = delta.total_seconds() / 3600 if delta else None

            n_flooded = sum(1 for c in cell_labels if c["flooded"])
            logger.info("  %d/%d cells flooded (matched run: %s Δ%.0fh)",
                        n_flooded, len(cell_labels), run_id, delta_h or 0)

            for cell in cell_labels:
                all_rows.append({
                    "date": s1_date,
                    "date_str": date_str,
                    "matched_run_id": run_id,
                    "match_delta_hours": delta_h,
                    **cell,
                })

            # Polite pause between GEE calls
            time.sleep(1)

    if not all_rows:
        logger.error("No labels generated — check GEE access and date ranges")
        sys.exit(1)

    labels_df = pd.DataFrame(all_rows)

    # Save CSV — per-season files (never overwrite the other season)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    seasons_in_data = labels_df["date_str"].str[:4].unique().tolist()
    for yr in seasons_in_data:
        yr_df = labels_df[labels_df["date_str"].str.startswith(yr)]
        yr_path = OUTPUT_DIR / f"sentinel1_labels_{yr}.csv"
        yr_df.to_csv(yr_path, index=False)
        logger.info("Saved %d labels for %s to %s", len(yr_df), yr, yr_path)

    # Combined file: merge with any existing other-season file
    combined_parts = [labels_df]
    for yr in ["2024", "2025"]:
        if yr not in seasons_in_data:
            other = OUTPUT_DIR / f"sentinel1_labels_{yr}.csv"
            if other.exists():
                combined_parts.append(pd.read_csv(other, parse_dates=["date"]))
                logger.info("Merged existing %s into combined CSV", other.name)
    combined_df = pd.concat(combined_parts, ignore_index=True).sort_values("date_str")
    csv_path = OUTPUT_DIR / "sentinel1_labels.csv"
    combined_df.to_csv(csv_path, index=False)
    logger.info("Saved combined %d cell-image labels to %s", len(combined_df), csv_path)
    labels_df = combined_df  # use combined for accuracy report

    # Accuracy report
    logger.info("Computing accuracy metrics...")
    report = compute_accuracy(labels_df, runs)
    report_path = OUTPUT_DIR / "accuracy_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Accuracy report: %s", report_path)

    print("\n=== ACCURACY REPORT ===")
    print(f"  S1 images processed:   {report.get('n_s1_images', 0)}")
    print(f"  Cell observations:     {report.get('n_cell_observations', 0)}")
    print(f"  Observed flood rate:   {report.get('flood_rate_observed', 0):.1%}")
    print(f"  Precision (L2+):       {report.get('precision', 0):.3f}")
    print(f"  Recall (L2+):          {report.get('recall', 0):.3f}")
    print(f"  F1 score:              {report.get('f1', 0):.3f}")
    print(f"  Brier score:           {report.get('brier_score', 0):.4f}  (0=perfect)")
    cm = report.get("confusion_matrix", {})
    print(f"  Confusion: TP={cm.get('TP')} FP={cm.get('FP')} FN={cm.get('FN')} TN={cm.get('TN')}")
    print("\nPer-commune recall (sorted):")
    for c in report.get("commune_stats", [])[:5]:
        print(f"  {c['commune_id']:20} recall={c['recall']:.2f}  prec={c['precision']:.2f}  flood_rate={c['observed_flood_rate']:.2f}")

    # Write to Supabase
    if not args.dry_run:
        write_to_supabase(labels_df)
    else:
        logger.info("--dry-run: skipping Supabase write")


if __name__ == "__main__":
    main()
