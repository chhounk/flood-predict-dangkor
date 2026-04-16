"""Supabase sink — push a latest.json payload into Postgres.

Non-fatal: if credentials are missing or the network fails, logs a warning and
returns False. The pipeline must never fail because of DB issues.

Environment variables:
    SUPABASE_URL          — e.g. https://xxxx.supabase.co
    SUPABASE_SERVICE_KEY  — service_role key (bypasses RLS, SERVER ONLY)

Usage:
    from src.db.supabase_sink import ingest_run
    ok = ingest_run(latest_json_dict)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Batch size for prediction inserts — Supabase REST has a row-size cap per
# request. 1956 cells split into batches of ~300 stays well under limits.
BATCH_SIZE = 300


def _get_client():
    """Return a Supabase client, or None if credentials are missing."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        logger.info("Supabase sink: SUPABASE_URL/SUPABASE_SERVICE_KEY not set — skipping")
        return None
    try:
        from supabase import create_client
    except ImportError:
        logger.warning("Supabase sink: supabase-py not installed — skipping")
        return None
    try:
        return create_client(url, key)
    except Exception as e:
        logger.warning("Supabase sink: client init failed: %s", e)
        return None


def ingest_run(latest: dict[str, Any], skip_cells: bool = False) -> bool:
    """Upsert one pipeline run (latest.json shape) into Supabase.

    Writes to three tables: runs, predictions, commune_predictions.
    Safe to call for the same run_id — ON CONFLICT behavior via upsert.

    Args:
        latest: The full latest.json payload.
        skip_cells: If True, write only runs + commune_predictions (no per-cell
            rows). Used for historical backfill to stay within free-tier storage.
            ~1956 rows × 600 runs saved = ~1.2M rows skipped per 2 seasons.

    Returns:
        True on success, False otherwise. Never raises.
    """
    client = _get_client()
    if client is None:
        return False

    try:
        return _do_ingest(client, latest, skip_cells=skip_cells)
    except Exception:
        logger.exception("Supabase sink: ingest failed (pipeline continues)")
        return False


def _do_ingest(client, latest: dict[str, Any], skip_cells: bool = False) -> bool:
    run_id: str = latest["run_id"]
    issued_at: str = latest.get("forecast_issued_at") or run_id

    # 1) runs row ------------------------------------------------------------
    run_row = {
        "run_id": run_id,
        "engine_version": latest.get("engine_version", "unknown"),
        "forecast_issued_at": issued_at,
        "horizon_hours": latest.get("horizon_hours", 72),
        "timestep_hours": latest.get("timestep_hours", 6),
        "ensemble_size": latest.get("ensemble_size", 0),
        "models_used": latest.get("models_used", []),
        "regional_signals": latest.get("regional_signals", {}),
        "summary": latest.get("summary", {}),
    }
    client.table("runs").upsert(run_row, on_conflict="run_id").execute()
    logger.info("Supabase: upserted run %s", run_id)

    # 2) predictions rows ---------------------------------------------------
    if skip_cells:
        logger.info("Supabase: skip_cells=True — not writing predictions table")
        cells = []
    else:
        cells = latest.get("cells", [])
    pred_rows = []
    for c in cells:
        centroid = c.get("centroid") or [None, None]
        pred_rows.append({
            "run_id": run_id,
            "grid_id": c["grid_id"],
            "commune_id": c.get("commune_id"),
            "commune_name": c.get("commune_name"),
            "centroid_lat": centroid[0] if len(centroid) > 0 else None,
            "centroid_lon": centroid[1] if len(centroid) > 1 else None,
            "peak_level": c.get("peak_level", 1),
            "peak_probability": c.get("peak_probability", 0.0),
            "peak_time": c.get("peak_time"),
            "window_peaks": c.get("window_peaks", {}),
            "timeseries": c.get("timeseries", []),
        })

    # Batch insert
    inserted = 0
    for i in range(0, len(pred_rows), BATCH_SIZE):
        batch = pred_rows[i : i + BATCH_SIZE]
        client.table("predictions").upsert(batch, on_conflict="run_id,grid_id").execute()
        inserted += len(batch)
    logger.info("Supabase: upserted %d predictions", inserted)

    # 3) commune_predictions rows -------------------------------------------
    communes = latest.get("communes", [])
    if communes:
        commune_rows = [{
            "run_id": run_id,
            "commune_id": c["commune_id"],
            "commune_name": c.get("commune_name", ""),
            "cell_count": c.get("cell_count", 0),
            "peak_level": c.get("peak_level", 1),
            "peak_probability": c.get("peak_probability", 0.0),
            "cells_l2_plus": c.get("cells_l2_plus", 0),
            "cells_l3_plus": c.get("cells_l3_plus", 0),
            "cells_l4": c.get("cells_l4", 0),
            "affected_pct_l2_plus": c.get("affected_pct_l2_plus", 0.0),
            "affected_pct_l3_plus": c.get("affected_pct_l3_plus", 0.0),
        } for c in communes]
        client.table("commune_predictions").upsert(
            commune_rows, on_conflict="run_id,commune_id"
        ).execute()
        logger.info("Supabase: upserted %d commune rows", len(commune_rows))

    return True
