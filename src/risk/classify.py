"""Risk classification — converts final_P to 4-level risk per cell per timestep.

Also computes window peaks (6h, 12h, 24h, 48h, 72h) for dashboard display.

Levels (from thresholds.yaml):
    L1 Safe:     P < 0.10
    L2 Low:      0.10 ≤ P < 0.40
    L3 Moderate: 0.40 ≤ P < 0.85
    L4 High:     P ≥ 0.85
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def _probability_to_level(p: float, levels: dict) -> int:
    """Convert a probability to a risk level (1-4).

    Args:
        p: Flood probability, 0-1.
        levels: Risk level config from thresholds.yaml.

    Returns:
        Risk level integer 1-4.
    """
    if p >= levels["L4"]["min_p"]:
        return 4
    elif p >= levels["L3"]["min_p"]:
        return 3
    elif p >= levels["L2"]["min_p"]:
        return 2
    else:
        return 1


def classify_cells(
    fused: dict[str, Any],
    grid: dict,
    thresholds: dict[str, Any],
    run_id: str,
    timestep_hours: int,
) -> list[dict]:
    """Classify all cells into risk levels with window peaks and timeseries.

    Args:
        fused: Dict with 'final_P' (n_cells, n_timesteps) and 'timestep_times'.
        grid: Grid GeoJSON dict.
        thresholds: Thresholds config dict.
        run_id: ISO timestamp of this run.
        timestep_hours: Hours per timestep.

    Returns:
        List of cell output dicts matching the latest.json schema.
    """
    final_P = fused["final_P"]
    timestep_times = fused["timestep_times"]
    features = grid["features"]
    levels_cfg = thresholds["risk_levels"]
    windows = thresholds["windows"]  # [6, 12, 24, 48, 72]

    n_cells, n_timesteps = final_P.shape

    # Parse run_id to base time
    try:
        base_time = datetime.fromisoformat(run_id.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        base_time = datetime.now(timezone.utc)

    # Generate timestep ISO strings if not available from forecast
    if timestep_times is None or len(timestep_times) < n_timesteps:
        timestep_times = [
            (base_time + timedelta(hours=i * timestep_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for i in range(n_timesteps)
        ]
    else:
        # Ensure they're proper ISO strings
        clean_times = []
        for t in timestep_times[:n_timesteps]:
            if isinstance(t, str) and "T" in t:
                if not t.endswith("Z"):
                    t = t + "Z"
                clean_times.append(t)
            else:
                # It's a relative time string like "2026-04-14T06:00"
                try:
                    dt = datetime.fromisoformat(t)
                    clean_times.append(dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
                except (ValueError, TypeError):
                    clean_times.append(
                        (base_time + timedelta(hours=len(clean_times) * timestep_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    )
        timestep_times = clean_times

    cells = []

    for i in range(n_cells):
        props = features[i]["properties"]
        cell_P = final_P[i, :]  # (n_timesteps,)

        # Timeseries
        timeseries = []
        for t_idx in range(n_timesteps):
            p = float(cell_P[t_idx])
            lev = _probability_to_level(p, levels_cfg)
            timeseries.append({
                "t": timestep_times[t_idx],
                "p": round(p, 4),
                "level": lev,
            })

        # Peak over entire horizon
        peak_idx = int(np.argmax(cell_P))
        peak_p = float(cell_P[peak_idx])
        peak_level = _probability_to_level(peak_p, levels_cfg)
        peak_time = timestep_times[peak_idx] if peak_p > 0 else None

        # Window peaks
        window_peaks = {}
        for w_hours in windows:
            w_steps = min(w_hours // timestep_hours, n_timesteps)
            if w_steps > 0:
                window_slice = cell_P[:w_steps]
                wp = float(window_slice.max())
            else:
                wp = 0.0
            wl = _probability_to_level(wp, levels_cfg)
            window_peaks[f"{w_hours}h"] = {"p": round(wp, 4), "level": wl}

        cells.append({
            "grid_id": props["grid_id"],
            "commune_id": props.get("commune_id"),
            "commune_name": props.get("commune_name"),
            "centroid": [props["centroid_lat"], props["centroid_lon"]],
            "bbox": props.get("bbox", []),
            "static": {
                "hand_m": props.get("hand_m"),
                "elevation_m": props.get("elevation_m"),
                "cn": props.get("assigned_cn"),
            },
            "peak_level": peak_level,
            "peak_probability": round(peak_p, 4),
            "peak_time": peak_time,
            "window_peaks": window_peaks,
            "timeseries": timeseries,
        })

    # Log summary
    level_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for c in cells:
        level_counts[c["peak_level"]] += 1
    logger.info(
        "Classification: L1=%d, L2=%d, L3=%d, L4=%d",
        level_counts[1], level_counts[2], level_counts[3], level_counts[4],
    )

    return cells
