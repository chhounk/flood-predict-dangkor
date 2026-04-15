"""Enrich the existing grid.geojson with commune names (GADM level-3) and centroids.

Idempotent: preserves existing grid_id values. Run once after build_grid.py, or
whenever the GADM-3 snapshot changes.

Usage:
    python -m src.grid.enrich_grid
"""

import json
import logging
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import Point, shape

from src.config import STATIC_DIR, load_district

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GADM3_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_KHM_3.json"
COMMUNES_PATH = STATIC_DIR / "dangkor_communes.geojson"
GRID_PATH = STATIC_DIR / "grid.geojson"


def download_communes() -> Path:
    """Download GADM level-3 and extract Dangkor communes (sangkats)."""
    if COMMUNES_PATH.exists():
        logger.info("Commune file already exists: %s", COMMUNES_PATH)
        return COMMUNES_PATH

    logger.info("Downloading GADM level-3 for Cambodia...")
    resp = requests.get(GADM3_URL, timeout=180)
    resp.raise_for_status()
    gadm = resp.json()
    logger.info("Downloaded %d level-3 features", len(gadm["features"]))

    # Filter to Dangkor District (NAME_2 match)
    communes = []
    for feat in gadm["features"]:
        props = feat["properties"]
        name2 = (props.get("NAME_2") or "").lower()
        varname2 = (props.get("VARNAME_2") or "").lower()
        if "dangk" in name2 or "dangk" in varname2:
            communes.append(feat)

    logger.info("Found %d communes in Dangkor District", len(communes))
    if not communes:
        raise ValueError("No Dangkor communes found in GADM level-3 data")

    out = {"type": "FeatureCollection", "features": communes}
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    with open(COMMUNES_PATH, "w") as f:
        json.dump(out, f)
    logger.info("Saved %s", COMMUNES_PATH)
    return COMMUNES_PATH


def enrich_grid() -> Path:
    """Add centroid_lat/lon and commune_name/commune_id to every cell in grid.geojson."""
    if not GRID_PATH.exists():
        raise FileNotFoundError(
            f"{GRID_PATH} missing — run build_grid.py first"
        )

    # Ensure communes are downloaded
    if not COMMUNES_PATH.exists():
        download_communes()

    communes_gdf = gpd.read_file(COMMUNES_PATH)
    # Canonical commune names from district config
    cfg_communes = load_district().get("communes", [])

    # Build a name list; prefer NAME_3 with VARNAME_3 fallback
    commune_records = []
    for idx, row in communes_gdf.iterrows():
        name3 = row.get("NAME_3") or row.get("VARNAME_3") or f"Commune_{idx}"
        # Build stable commune_id from name
        commune_id = name3.upper().replace(" ", "_").replace("'", "")
        commune_records.append({
            "commune_id": commune_id,
            "commune_name": name3,
            "geometry": row.geometry,
        })

    logger.info(
        "Commune list: %s",
        ", ".join(r["commune_name"] for r in commune_records),
    )

    # Load grid
    with open(GRID_PATH) as f:
        grid = json.load(f)

    n_matched = 0
    for feat in grid["features"]:
        props = feat["properties"]
        # Centroid from bbox if already stored, else compute
        if props.get("centroid_lat") is None or props.get("centroid_lon") is None:
            bbox = props.get("bbox")
            if bbox and len(bbox) == 4:
                props["centroid_lon"] = round((bbox[0] + bbox[2]) / 2, 6)
                props["centroid_lat"] = round((bbox[1] + bbox[3]) / 2, 6)
            else:
                geom = shape(feat["geometry"])
                c = geom.centroid
                props["centroid_lon"] = round(c.x, 6)
                props["centroid_lat"] = round(c.y, 6)

        # Assign commune via point-in-polygon on centroid
        pt = Point(props["centroid_lon"], props["centroid_lat"])
        matched = None
        for rec in commune_records:
            if rec["geometry"].contains(pt):
                matched = rec
                break
        if matched is None:
            # Fallback: nearest commune
            distances = [(rec, rec["geometry"].distance(pt)) for rec in commune_records]
            matched = min(distances, key=lambda x: x[1])[0]
        else:
            n_matched += 1

        props["commune_id"] = matched["commune_id"]
        props["commune_name"] = matched["commune_name"]

    # Drop helpful summary
    logger.info(
        "Enriched %d cells (%d direct-match, %d nearest-fallback)",
        len(grid["features"]), n_matched, len(grid["features"]) - n_matched,
    )

    with open(GRID_PATH, "w") as f:
        json.dump(grid, f)
    logger.info("Saved enriched grid to %s", GRID_PATH)

    # Also emit a small commune summary for frontend convenience
    from collections import Counter
    counts = Counter(f["properties"]["commune_name"] for f in grid["features"])
    summary = {
        "communes": [
            {"commune_id": r["commune_id"], "commune_name": r["commune_name"],
             "cell_count": counts.get(r["commune_name"], 0)}
            for r in commune_records
        ],
        "total_cells": len(grid["features"]),
    }
    summary_path = STATIC_DIR / "communes_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    logger.info("Saved commune summary to %s", summary_path)

    return GRID_PATH


if __name__ == "__main__":
    download_communes()
    enrich_grid()
