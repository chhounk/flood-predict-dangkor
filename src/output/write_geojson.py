"""Write GeoJSON output for the dashboard map layer."""

import json
import logging
from pathlib import Path
from typing import Any

from src.config import OUTPUT_DIR, STATIC_DIR

logger = logging.getLogger(__name__)


def write_latest_geojson(cells: list[dict[str, Any]]) -> Path:
    """Write grid cells with risk properties to latest.geojson.

    Loads the static grid geometry and attaches prediction properties.

    Args:
        cells: List of cell prediction dicts (must have grid_id, peak_level, etc.)

    Returns:
        Path to the written latest.geojson file.
    """
    # Load static grid for geometries
    grid_path = STATIC_DIR / "grid.geojson"
    with open(grid_path, "r") as f:
        grid = json.load(f)

    # Index cells by grid_id
    cell_lookup = {c["grid_id"]: c for c in cells}

    features = []
    for feat in grid["features"]:
        grid_id = feat["properties"]["grid_id"]
        cell_data = cell_lookup.get(grid_id, {})

        props = {
            "grid_id": grid_id,
            "commune": cell_data.get("commune", feat["properties"].get("commune_name")),
            "peak_level": cell_data.get("peak_level", 1),
            "peak_probability": cell_data.get("peak_probability", 0.0),
            "peak_time": cell_data.get("peak_time"),
        }

        # Add window peaks
        window_peaks = cell_data.get("window_peaks", {})
        for w in ["6h", "12h", "24h", "48h", "72h"]:
            wp = window_peaks.get(w, {"p": 0.0, "level": 1})
            props[f"wp_{w}_p"] = wp.get("p", 0.0)
            props[f"wp_{w}_level"] = wp.get("level", 1)

        features.append({
            "type": "Feature",
            "geometry": feat["geometry"],
            "properties": props,
        })

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    geojson_path = OUTPUT_DIR / "latest.geojson"
    with open(geojson_path, "w") as f:
        json.dump(geojson, f)
    logger.info("Wrote %s (%d features)", geojson_path, len(features))

    return geojson_path
