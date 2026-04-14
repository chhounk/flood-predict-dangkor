"""Monte Carlo ensemble — runs N scenarios of the physics chain.

For each scenario:
1. Randomly select one NWP model forecast
2. Perturb rainfall (±10% lognormal)
3. Perturb CN (±10% normal)
4. Perturb drainage capacity (±20% normal)
5. Compute SCS runoff → flow accumulation → inundation flagging
6. Record which cells are flooded

After all scenarios: physics_P = (# scenarios flooded) / N per cell per timestep.
"""

import json
import logging
import time
from typing import Any

import numpy as np

from src.config import STATIC_DIR, load_cn_lookup
from src.data.fetch_open_meteo import aggregate_to_timesteps, interpolate_idw
from src.physics.hand import load_hand_values
from src.physics.inundation import flag_inundation
from src.physics.routing import estimate_accumulated_runoff, load_flow_accumulation_weights
from src.physics.runoff_scs import compute_runoff, get_amc_factor

logger = logging.getLogger(__name__)


def run_ensemble(
    grid: dict,
    forecasts: dict[str, Any] | None,
    archive: dict[str, Any] | None,
    thresholds: dict[str, Any],
    n_scenarios: int = 30,
    n_timesteps: int = 13,
    timestep_hours: int = 6,
) -> dict[str, Any]:
    """Run Monte Carlo ensemble of the physics chain.

    Args:
        grid: Grid GeoJSON dict with 'features' list.
        forecasts: Layer 1 forecast data (from fetch_forecasts), or None.
        archive: Layer 1 archive data (from fetch_archive), or None.
        thresholds: Thresholds config dict.
        n_scenarios: Number of Monte Carlo scenarios (default 30).
        n_timesteps: Number of timesteps in the forecast horizon.
        timestep_hours: Hours per timestep.

    Returns:
        Dict with:
            'physics_P': ndarray shape (n_cells, n_timesteps), probability 0-1.
            'timestep_times': list of ISO time strings for each timestep.
    """
    t0 = time.time()
    features = grid["features"]
    n_cells = len(features)
    ensemble_cfg = thresholds["ensemble"]

    logger.info("Monte Carlo ensemble: %d scenarios, %d cells, %d timesteps", n_scenarios, n_cells, n_timesteps)

    # --- Load static data ---
    # Cell centroids
    centroids = np.array([
        [f["properties"]["centroid_lat"], f["properties"]["centroid_lon"]]
        for f in features
    ])

    # CN values per cell
    cn_base = np.array([
        f["properties"].get("assigned_cn") or 85
        for f in features
    ], dtype=np.float64)

    # HAND values per cell
    hand_m = load_hand_values(features)

    # Flow accumulation weights
    flow_acc = load_flow_accumulation_weights(centroids)

    # Cell area
    cell_area_m2 = features[0]["properties"].get("area_m2", 62500.0)

    # --- Prepare rainfall data ---
    if forecasts is None:
        logger.warning("No forecast data — using zero rainfall")
        rainfall_by_model = {"zero": np.zeros((n_cells, n_timesteps))}
        model_names = ["zero"]
        timestep_times = [f"T+{i*timestep_hours}h" for i in range(n_timesteps)]
    else:
        rainfall_by_model, model_names, timestep_times = _prepare_rainfall(
            forecasts, centroids, n_timesteps, timestep_hours,
        )

    # --- AMC factor ---
    if archive is not None:
        mean_5day = float(archive["five_day_total_mm"].mean())
    else:
        mean_5day = 0.0

    cn_config = load_cn_lookup()
    amc_factor = get_amc_factor(mean_5day, cn_config["amc"])
    logger.info("AMC: 5-day rain=%.1fmm, factor=%.2f", mean_5day, amc_factor)

    # --- Run scenarios ---
    flood_counts = np.zeros((n_cells, n_timesteps), dtype=np.int32)
    rng = np.random.default_rng(seed=42)

    hand_threshold = thresholds["hand_threshold_m"]
    drainage_cap = thresholds["drainage_capacity_m3"]
    storage_cap = thresholds["local_storage_capacity_m3"]

    rain_sigma = ensemble_cfg["rainfall_perturbation_sigma"]
    cn_sigma = ensemble_cfg["cn_perturbation_sigma"]
    drain_sigma = ensemble_cfg["drainage_perturbation_sigma"]

    for s in range(n_scenarios):
        # 1. Sample a model
        model_idx = rng.integers(0, len(model_names))
        model_name = model_names[model_idx]
        rain_base = rainfall_by_model[model_name]  # (n_cells, n_timesteps)

        # 2. Perturb rainfall (lognormal multiplicative)
        rain_mult = rng.lognormal(mean=0.0, sigma=rain_sigma, size=(n_cells, 1))
        rain_scenario = rain_base * rain_mult

        # 3. Perturb CN
        cn_perturb = cn_base * (1.0 + rng.normal(0, cn_sigma, size=n_cells))
        cn_perturb = np.clip(cn_perturb, 30.0, 98.0)

        # 4. Perturb drainage capacity
        drain_perturb = drainage_cap * (1.0 + rng.normal(0, drain_sigma))
        drain_perturb = max(drain_perturb, 50.0)

        # 5. Compute runoff
        runoff_mm = compute_runoff(rain_scenario, cn_perturb, amc_factor)

        # 6. Flow accumulation
        accumulated_m3 = estimate_accumulated_runoff(runoff_mm, flow_acc, cell_area_m2)
        local_m3 = runoff_mm * cell_area_m2 / 1000.0

        # 7. Inundation flagging
        flooded = flag_inundation(
            hand_m=hand_m,
            accumulated_runoff_m3=accumulated_m3,
            local_runoff_m3=local_m3,
            hand_threshold_m=hand_threshold,
            drainage_capacity_m3=drain_perturb,
            local_storage_m3=storage_cap,
        )

        flood_counts += flooded.astype(np.int32)

    # physics_P = fraction of scenarios each cell was flooded
    physics_P = flood_counts.astype(np.float64) / n_scenarios

    elapsed = time.time() - t0
    flooded_any = (physics_P.max(axis=1) > 0).sum()
    logger.info(
        "Ensemble complete: %d/%d cells with any flood signal, max P=%.2f (%.1fs)",
        flooded_any, n_cells, physics_P.max(), elapsed,
    )

    return {
        "physics_P": physics_P,
        "timestep_times": timestep_times,
    }


def _prepare_rainfall(
    forecasts: dict[str, Any],
    centroids: np.ndarray,
    n_timesteps: int,
    timestep_hours: int,
) -> tuple[dict[str, np.ndarray], list[str], list[str]]:
    """Prepare per-model, per-cell rainfall grids from forecast data.

    1. Aggregate hourly forecast to 6-hourly timesteps
    2. IDW interpolate from sample points to grid cells

    Returns:
        Tuple of (rainfall_by_model, model_names, timestep_times).
        rainfall_by_model: dict[model_name] -> ndarray (n_cells, n_timesteps) in mm.
    """
    points = forecasts["points"]
    models_data = forecasts["models_data"]
    model_names = list(models_data.keys())

    rainfall_by_model = {}
    timestep_times = None

    for model_name, model_data in models_data.items():
        hourly_rain = model_data["rainfall_mm"]  # (n_points, n_hours)
        hourly_times = model_data["times"]

        # Aggregate to timestep resolution
        agg_rain, agg_times = aggregate_to_timesteps(hourly_rain, hourly_times, timestep_hours)

        # Trim or pad to n_timesteps
        actual_ts = agg_rain.shape[1]
        if actual_ts >= n_timesteps:
            agg_rain = agg_rain[:, :n_timesteps]
            agg_times = agg_times[:n_timesteps]
        else:
            # Pad with zeros
            pad = np.zeros((agg_rain.shape[0], n_timesteps - actual_ts))
            agg_rain = np.hstack([agg_rain, pad])
            agg_times = agg_times + [f"T+{i*timestep_hours}h" for i in range(actual_ts, n_timesteps)]

        # IDW interpolation to grid cells
        cell_rain = interpolate_idw(agg_rain, points, centroids)  # (n_cells, n_timesteps)
        rainfall_by_model[model_name] = cell_rain

        if timestep_times is None:
            timestep_times = agg_times

    logger.info(
        "Prepared rainfall: %d models, %d cells, %d timesteps",
        len(model_names), centroids.shape[0], n_timesteps,
    )

    return rainfall_by_model, model_names, timestep_times
