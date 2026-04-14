"""Write machine-readable JSON output for the prediction pipeline.

Output schema is the contract that the future Telegram bot will consume.
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import ENGINE_VERSION, OUTPUT_DIR

logger = logging.getLogger(__name__)


def write_latest_json(
    cells: list[dict[str, Any]],
    run_id: str,
    models_used: list[str],
    ensemble_size: int,
    regional_signals: dict[str, Any],
    horizon_hours: int = 72,
    timestep_hours: int = 6,
) -> Path:
    """Write the full prediction output to latest.json and archive it.

    Args:
        cells: List of cell dicts with grid_id, commune, centroid, static,
               peak_level, peak_probability, peak_time, window_peaks, timeseries.
        run_id: ISO timestamp of this run.
        models_used: List of NWP model names used.
        ensemble_size: Number of Monte Carlo scenarios.
        regional_signals: Dict of signal name -> value (or None).
        horizon_hours: Forecast horizon in hours.
        timestep_hours: Timestep resolution in hours.

    Returns:
        Path to the written latest.json file.
    """
    # Compute summary
    cells_total = len(cells)
    level_counts = {str(i): 0 for i in range(1, 5)}
    for cell in cells:
        level = str(cell.get("peak_level", 1))
        level_counts[level] = level_counts.get(level, 0) + 1

    # Find peak risk time
    peak_time = None
    max_p = 0.0
    for cell in cells:
        if cell.get("peak_probability", 0) > max_p:
            max_p = cell["peak_probability"]
            peak_time = cell.get("peak_time")

    # Compute window peaks summary
    windows = ["6h", "12h", "24h", "48h", "72h"]
    window_peaks_summary = {}
    for w in windows:
        l4_count = 0
        l3_count = 0
        for cell in cells:
            wp = cell.get("window_peaks", {}).get(w, {})
            lev = wp.get("level", 1)
            if lev == 4:
                l4_count += 1
            elif lev == 3:
                l3_count += 1
        window_peaks_summary[w] = {
            "level_4_cells": l4_count,
            "level_3_cells": l3_count,
        }

    output = {
        "run_id": run_id,
        "forecast_issued_at": run_id,
        "horizon_hours": horizon_hours,
        "timestep_hours": timestep_hours,
        "engine_version": ENGINE_VERSION,
        "ensemble_size": ensemble_size,
        "models_used": models_used,
        "regional_signals": regional_signals,
        "summary": {
            "cells_total": cells_total,
            "cells_by_peak_level": level_counts,
            "peak_risk_time": peak_time,
            "window_peaks": window_peaks_summary,
        },
        "cells": cells,
    }

    # Write latest.json
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = OUTPUT_DIR / "latest.json"
    with open(latest_path, "w") as f:
        json.dump(output, f, indent=2)
    logger.info("Wrote %s (%d cells)", latest_path, cells_total)

    # Archive
    history_dir = OUTPUT_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    safe_ts = run_id.replace(":", "-")
    archive_path = history_dir / f"{safe_ts}.json"
    shutil.copy2(latest_path, archive_path)
    logger.info("Archived to %s", archive_path)

    return latest_path
