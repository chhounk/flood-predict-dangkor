"""Layer 1: Fetch multi-model rainfall forecasts and historical archive from Open-Meteo.

Open-Meteo API docs: https://open-meteo.com/en/docs
- Forecast: hourly precipitation for multiple NWP models
- Archive: historical hourly precipitation for antecedent moisture

All rainfall values are in mm (millimeters).
"""

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import requests

logger = logging.getLogger(__name__)

# Open-Meteo API endpoints (free, no auth required)
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Retry config — Open-Meteo free tier rate-limits bursts. Long historical
# backfill loops WILL hit 429s; we need to survive them.
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 5
_BASE_BACKOFF_SEC = 2.0


def _get_with_retry(url: str, params: dict, timeout: int = 90) -> dict:
    """GET with exponential backoff on rate-limit / 5xx. Raises on final failure."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code in _RETRY_STATUSES:
                wait = _BASE_BACKOFF_SEC * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
                logger.warning(
                    "Open-Meteo %d on attempt %d/%d — sleeping %.1fs",
                    resp.status_code, attempt, _MAX_ATTEMPTS, wait,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            wait = _BASE_BACKOFF_SEC * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
            logger.warning(
                "Open-Meteo %s on attempt %d/%d — sleeping %.1fs",
                type(e).__name__, attempt, _MAX_ATTEMPTS, wait,
            )
            time.sleep(wait)
    raise RuntimeError(
        f"Open-Meteo exhausted {_MAX_ATTEMPTS} attempts for {url} "
        f"(last exc: {last_exc})"
    )


def fetch_forecasts(
    sample_points: list[dict[str, float]],
    forecast_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Fetch 72h hourly rainfall forecasts from multiple NWP models.

    Queries Open-Meteo for each sample point across all configured models.
    Returns per-point, per-model, per-hour rainfall arrays.

    Args:
        sample_points: List of {lat, lon} dicts for query locations.
        forecast_cfg: Dict with 'models', 'horizon_hours' keys.

    Returns:
        Dict with keys:
            'points': list of {lat, lon} dicts
            'models_used': list of model names that returned data
            'models_data': dict[model_name] -> {
                'times': list of ISO strings,
                'rainfall_mm': np.ndarray shape (n_points, n_times)
            }
    """
    t0 = time.time()
    models = forecast_cfg["models"]
    horizon_hours = forecast_cfg["horizon_hours"]

    logger.info(
        "Fetching forecasts: %d points, %d models, %dh horizon",
        len(sample_points), len(models), horizon_hours,
    )

    models_data: dict[str, dict] = {}
    models_used: list[str] = []

    for model in models:
        try:
            model_result = _fetch_model_forecast(sample_points, model, horizon_hours)
            models_data[model] = model_result
            models_used.append(model)
            logger.info("  %s: OK (%d times)", model, len(model_result["times"]))
        except Exception as e:
            logger.warning("  %s: FAILED — %s", model, e)

    if not models_used:
        raise RuntimeError("All forecast models failed — cannot proceed")

    logger.info(
        "Forecast fetch complete: %d/%d models (%.1fs)",
        len(models_used), len(models), time.time() - t0,
    )

    return {
        "points": sample_points,
        "models_used": models_used,
        "models_data": models_data,
    }


def _fetch_model_forecast(
    points: list[dict[str, float]],
    model: str,
    horizon_hours: int,
) -> dict[str, Any]:
    """Fetch forecast for all points from a single model.

    Args:
        points: List of {lat, lon} dicts.
        model: Open-Meteo model name (e.g. 'ecmwf_ifs04').
        horizon_hours: Forecast horizon in hours.

    Returns:
        Dict with 'times' (list[str]) and 'rainfall_mm' (ndarray shape n_points x n_times).
    """
    lats = ",".join(str(p["lat"]) for p in points)
    lons = ",".join(str(p["lon"]) for p in points)

    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": "precipitation",
        "models": model,
        "forecast_hours": horizon_hours,
        "timezone": "UTC",
    }

    data = _get_with_retry(FORECAST_URL, params, timeout=60)

    # Open-Meteo returns a list when multiple coordinates are given
    if isinstance(data, list):
        results = data
    else:
        results = [data]

    # Extract times from first result
    times = results[0]["hourly"]["time"]

    # Build rainfall array: (n_points, n_times)
    rainfall = np.zeros((len(points), len(times)), dtype=np.float64)
    for i, result in enumerate(results):
        precip = result["hourly"]["precipitation"]
        rainfall[i, :] = [v if v is not None else 0.0 for v in precip]

    return {"times": times, "rainfall_mm": rainfall}


def fetch_archive(
    sample_points: list[dict[str, float]],
    forecast_cfg: dict[str, Any],
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Fetch historical rainfall for the N days preceding reference_time.

    Args:
        sample_points: List of {lat, lon} dicts.
        forecast_cfg: Dict with 'archive_days' key.
        reference_time: UTC anchor (default: now). Archive covers the
            `archive_days` days immediately preceding this time.

    Returns:
        Dict with:
            'points': list of {lat, lon}
            'times': list of ISO strings
            'rainfall_mm': ndarray shape (n_points, n_times)
            'five_day_total_mm': ndarray shape (n_points,) — last 5 days total per point
    """
    t0 = time.time()
    archive_days = forecast_cfg.get("archive_days", 7)

    if reference_time is None:
        reference_time = datetime.now(timezone.utc)
    end_date = reference_time.date() - timedelta(days=1)
    start_date = end_date - timedelta(days=archive_days - 1)

    lats = ",".join(str(p["lat"]) for p in sample_points)
    lons = ",".join(str(p["lon"]) for p in sample_points)

    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": "precipitation",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "timezone": "UTC",
    }

    data = _get_with_retry(ARCHIVE_URL, params, timeout=60)

    if isinstance(data, list):
        results = data
    else:
        results = [data]

    times = results[0]["hourly"]["time"]
    n_points = len(sample_points)
    n_times = len(times)

    rainfall = np.zeros((n_points, n_times), dtype=np.float64)
    for i, result in enumerate(results):
        precip = result["hourly"]["precipitation"]
        rainfall[i, :] = [v if v is not None else 0.0 for v in precip]

    # Compute 5-day total (last 120 hours)
    hours_5d = min(120, n_times)
    five_day_total = rainfall[:, -hours_5d:].sum(axis=1)  # mm per point

    logger.info(
        "Archive fetch: %s to %s, %d hours, mean 5-day total=%.1fmm (%.1fs)",
        start_date, end_date, n_times,
        five_day_total.mean(), time.time() - t0,
    )

    return {
        "points": sample_points,
        "times": times,
        "rainfall_mm": rainfall,
        "five_day_total_mm": five_day_total,
    }


def fetch_historical_forecast(
    sample_points: list[dict[str, float]],
    forecast_cfg: dict[str, Any],
    reference_time: datetime,
) -> dict[str, Any]:
    """Fetch historical rainfall as a 'forecast' anchored to a past date.

    Uses the Open-Meteo ARCHIVE endpoint (ERA5 reanalysis) to retrieve the
    rainfall that actually occurred from reference_time to reference_time+horizon.
    Mirrors the return shape of fetch_forecasts() so the rest of the pipeline
    treats a historical run identically to a live run.

    Args:
        sample_points: List of {lat, lon} dicts.
        forecast_cfg: Dict with 'horizon_hours' key (72 typical).
        reference_time: UTC datetime anchor for the 72h "forecast" window.

    Returns:
        Same shape as fetch_forecasts — but models_used=['era5_reanalysis']
        because archive data is a single reanalysis, not an NWP ensemble.
        Monte Carlo perturbation remains the uncertainty source.
    """
    t0 = time.time()
    horizon_hours = forecast_cfg["horizon_hours"]

    # Archive API needs whole-day date range; we fetch 2 days and slice to hour.
    start_date = reference_time.date()
    end_date = (reference_time + timedelta(hours=horizon_hours)).date()

    lats = ",".join(str(p["lat"]) for p in sample_points)
    lons = ",".join(str(p["lon"]) for p in sample_points)

    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": "precipitation",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "timezone": "UTC",
    }

    data = _get_with_retry(ARCHIVE_URL, params, timeout=90)

    results = data if isinstance(data, list) else [data]
    times_all = results[0]["hourly"]["time"]
    n_points = len(sample_points)

    # Find the index in `times_all` corresponding to reference_time
    target_iso = reference_time.strftime("%Y-%m-%dT%H:00")
    try:
        start_idx = times_all.index(target_iso)
    except ValueError:
        # Fallback: nearest hour
        start_idx = 0
        for i, t in enumerate(times_all):
            if t >= target_iso:
                start_idx = i
                break

    end_idx = min(start_idx + horizon_hours, len(times_all))
    times_slice = times_all[start_idx:end_idx]

    rainfall = np.zeros((n_points, len(times_slice)), dtype=np.float64)
    for i, result in enumerate(results):
        precip_all = result["hourly"]["precipitation"]
        precip_slice = precip_all[start_idx:end_idx]
        rainfall[i, :] = [v if v is not None else 0.0 for v in precip_slice]

    logger.info(
        "Historical forecast fetch: %s +%dh, %d hours (%.1fs)",
        reference_time.isoformat(), horizon_hours, len(times_slice), time.time() - t0,
    )

    return {
        "points": sample_points,
        "models_used": ["era5_reanalysis"],
        "models_data": {
            "era5_reanalysis": {
                "times": times_slice,
                "rainfall_mm": rainfall,
            }
        },
    }


def aggregate_to_timesteps(
    hourly_rainfall: np.ndarray,
    hourly_times: list[str],
    timestep_hours: int,
) -> tuple[np.ndarray, list[str]]:
    """Aggregate hourly rainfall to coarser timesteps (e.g. 6-hourly).

    Args:
        hourly_rainfall: ndarray shape (n_points, n_hourly_times), mm/hour.
        hourly_times: List of ISO time strings for each hourly step.
        timestep_hours: Target timestep in hours (e.g. 6).

    Returns:
        Tuple of (aggregated rainfall ndarray shape (n_points, n_timesteps) in mm/timestep,
                  list of timestep start times as ISO strings).
    """
    n_points, n_hours = hourly_rainfall.shape
    n_timesteps = n_hours // timestep_hours

    # Reshape and sum each timestep block
    trimmed = hourly_rainfall[:, :n_timesteps * timestep_hours]
    reshaped = trimmed.reshape(n_points, n_timesteps, timestep_hours)
    agg = reshaped.sum(axis=2)  # mm per timestep

    # Pick the start time of each block
    agg_times = [hourly_times[i * timestep_hours] for i in range(n_timesteps)]

    return agg, agg_times


def interpolate_idw(
    point_values: np.ndarray,
    point_coords: list[dict[str, float]],
    cell_centroids: np.ndarray,
    power: float = 2.0,
) -> np.ndarray:
    """Inverse Distance Weighting interpolation from sample points to grid cells.

    Args:
        point_values: ndarray shape (n_points, n_timesteps), values at sample points.
        point_coords: List of {lat, lon} dicts for sample points.
        cell_centroids: ndarray shape (n_cells, 2) with [lat, lon] per cell.
        power: IDW power parameter (default 2.0).

    Returns:
        ndarray shape (n_cells, n_timesteps) with interpolated values.
    """
    n_points = len(point_coords)
    n_cells = cell_centroids.shape[0]

    # Build point coordinate array
    pts = np.array([[p["lat"], p["lon"]] for p in point_coords])  # (n_points, 2)

    # Compute distances: (n_cells, n_points)
    # Using simple Euclidean on lat/lon — acceptable for small area (~15km)
    diffs = cell_centroids[:, np.newaxis, :] - pts[np.newaxis, :, :]  # (n_cells, n_points, 2)
    dists = np.sqrt((diffs ** 2).sum(axis=2))  # (n_cells, n_points)

    # Avoid division by zero — if cell is at a sample point, use that point's value
    min_dist = 1e-8
    dists = np.maximum(dists, min_dist)

    # IDW weights: w_i = 1/d_i^p / sum(1/d_j^p)
    inv_d_p = 1.0 / (dists ** power)  # (n_cells, n_points)
    weights = inv_d_p / inv_d_p.sum(axis=1, keepdims=True)  # (n_cells, n_points)

    # Interpolate: (n_cells, n_points) @ (n_points, n_timesteps) -> (n_cells, n_timesteps)
    interpolated = weights @ point_values

    return interpolated
