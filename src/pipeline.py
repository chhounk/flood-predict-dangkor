"""Main prediction pipeline — orchestrates all 4 layers end-to-end.

Usage:
    python -m src.pipeline
"""

import json
import logging
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone

from src.config import DOCS_DIR, ENGINE_VERSION, OUTPUT_DIR, STATIC_DIR, load_district, load_thresholds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def run_pipeline() -> None:
    """Run the full prediction pipeline."""
    t_start = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info("=== Pipeline start: %s ===", run_id)

    district = load_district()
    thresholds = load_thresholds()

    # Load grid
    grid_path = STATIC_DIR / "grid.geojson"
    with open(grid_path, "r") as f:
        grid = json.load(f)
    n_cells = len(grid["features"])
    logger.info("Loaded grid: %d cells", n_cells)

    forecast_cfg = district["forecast"]
    horizon_hours: int = forecast_cfg["horizon_hours"]
    timestep_hours: int = forecast_cfg["timestep_hours"]
    n_timesteps = horizon_hours // timestep_hours + 1  # include t=0

    # ---- Layer 1: Meteorological Driver ----
    t1 = time.time()
    try:
        from src.data.fetch_open_meteo import fetch_forecasts, fetch_archive
        forecasts = fetch_forecasts(district["sample_points"], forecast_cfg)
        archive = fetch_archive(district["sample_points"], forecast_cfg)
        models_used = forecasts.get("models_used", forecast_cfg["models"])
        logger.info("Layer 1 — Forecast data fetched (%.1fs)", time.time() - t1)
    except Exception:
        logger.warning("Layer 1 — Using placeholder forecast data: %s", traceback.format_exc())
        forecasts = None
        archive = None
        models_used = forecast_cfg["models"]

    # ---- Layer 2: Hydrological Response ----
    t2 = time.time()
    try:
        from src.ensemble.monte_carlo import run_ensemble
        ensemble_size = thresholds["ensemble"]["n_scenarios"]
        physics_results = run_ensemble(
            grid=grid,
            forecasts=forecasts,
            archive=archive,
            thresholds=thresholds,
            n_scenarios=ensemble_size,
            n_timesteps=n_timesteps,
            timestep_hours=timestep_hours,
        )
        logger.info("Layer 2 — Ensemble complete (%.1fs)", time.time() - t2)
    except Exception:
        logger.warning("Layer 2 — Using placeholder physics: %s", traceback.format_exc())
        ensemble_size = 30
        physics_results = None

    # ---- Layer 3: External Signals ----
    t3 = time.time()
    try:
        from src.signals.gpm_signal import GPMSignal
        gpm = GPMSignal()
        gpm_agreement = gpm.fetch(district)
    except Exception:
        logger.warning("Layer 3 — GPM signal unavailable: %s", traceback.format_exc())
        gpm_agreement = None

    try:
        from src.signals.jaxa_gsmap_signal import JAXAGSMaPSignal
        jaxa_agreement = JAXAGSMaPSignal().fetch(district)
    except Exception:
        jaxa_agreement = None

    regional_signals = {
        "gpm_agreement": gpm_agreement,
        "jaxa_gsmap": jaxa_agreement,  # Stubbed in v1
        "glofas": None,  # Stubbed in v1
    }
    logger.info("Layer 3 — Signals collected (%.1fs)", time.time() - t3)

    # ---- Layer 4: Fusion + Classification ----
    t4 = time.time()
    try:
        from src.fusion.combine import fuse
        from src.risk.classify import classify_cells
        if physics_results is not None:
            fused = fuse(physics_results, regional_signals, thresholds)
            cells = classify_cells(fused, grid, thresholds, run_id, timestep_hours)
        else:
            cells = _placeholder_cells(grid, run_id, timestep_hours, n_timesteps)
    except Exception:
        logger.warning("Layer 4 — Using placeholder output: %s", traceback.format_exc())
        cells = _placeholder_cells(grid, run_id, timestep_hours, n_timesteps)
    logger.info("Layer 4 — Classification complete (%.1fs)", time.time() - t4)

    # ---- Write outputs ----
    t5 = time.time()
    from src.output.write_json import write_latest_json
    from src.output.write_geojson import write_latest_geojson

    write_latest_json(
        cells=cells,
        run_id=run_id,
        models_used=models_used,
        ensemble_size=ensemble_size,
        regional_signals=regional_signals,
        horizon_hours=horizon_hours,
        timestep_hours=timestep_hours,
    )
    write_latest_geojson(cells)

    # Copy to docs/data/ for dashboard
    docs_data = DOCS_DIR / "data"
    docs_data.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUTPUT_DIR / "latest.json", docs_data / "latest.json")
    shutil.copy2(OUTPUT_DIR / "latest.geojson", docs_data / "latest.geojson")
    logger.info("Outputs written and copied to docs/ (%.1fs)", time.time() - t5)

    elapsed = time.time() - t_start
    logger.info("=== Pipeline complete: %.1fs ===", elapsed)


def _placeholder_cells(
    grid: dict,
    run_id: str,
    timestep_hours: int,
    n_timesteps: int,
) -> list[dict]:
    """Generate placeholder cell outputs with all L1 (Safe) for skeleton testing."""
    import numpy as np
    from datetime import timedelta

    base_time = datetime.fromisoformat(run_id.replace("Z", "+00:00"))
    cells = []

    for feat in grid["features"]:
        props = feat["properties"]
        grid_id = props["grid_id"]

        # Generate timeseries — all safe
        timeseries = []
        for t_idx in range(n_timesteps):
            t = base_time + timedelta(hours=t_idx * timestep_hours)
            timeseries.append({
                "t": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "p": 0.0,
                "level": 1,
            })

        # Window peaks — all safe
        window_peaks = {}
        for w in [6, 12, 24, 48, 72]:
            window_peaks[f"{w}h"] = {"p": 0.0, "level": 1}

        cells.append({
            "grid_id": grid_id,
            "commune_id": props.get("commune_id"),
            "commune_name": props.get("commune_name"),
            "centroid": [props["centroid_lat"], props["centroid_lon"]],
            "bbox": props.get("bbox", []),
            "static": {
                "hand_m": props.get("hand_m"),
                "elevation_m": props.get("elevation_m"),
                "cn": props.get("assigned_cn"),
            },
            "peak_level": 1,
            "peak_probability": 0.0,
            "peak_time": None,
            "window_peaks": window_peaks,
            "timeseries": timeseries,
        })

    return cells


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as e:
        logger.error("Pipeline failed: %s", traceback.format_exc())
        # Write error file but don't overwrite good latest.json
        error_path = OUTPUT_DIR / "last_error.json"
        error_path.parent.mkdir(parents=True, exist_ok=True)
        with open(error_path, "w") as f:
            json.dump({
                "error": str(e),
                "traceback": traceback.format_exc(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
        sys.exit(1)
