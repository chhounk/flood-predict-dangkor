"""Build the 250m grid for Dangkor District, clipped to the admin boundary.

Generates data/static/grid.geojson with stable grid IDs (D-XXXXX).
Also renders a verification PNG showing the grid over the boundary.

Usage:
    python -m src.grid.build_grid
"""

import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from shapely.geometry import box, mapping

from src.config import PROJECT_ROOT, STATIC_DIR, load_district

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def download_boundary() -> Path:
    """Download GADM level-2 boundary for Cambodia and extract Dangkor District.

    Returns:
        Path to the saved boundary GeoJSON file.
    """
    import requests

    boundary_path = STATIC_DIR / "dangkor_boundary.geojson"
    if boundary_path.exists():
        logger.info("Boundary file already exists: %s", boundary_path)
        return boundary_path

    logger.info("Downloading GADM level-2 boundary for Cambodia...")
    # GADM 4.1 GeoJSON for Cambodia level 2
    url = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_KHM_2.json"
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()

    gadm = resp.json()
    logger.info("Downloaded %d features from GADM", len(gadm["features"]))

    # Find Dangkor District (also spelled Dangkao / Dangkor)
    dangkor_features = []
    for feat in gadm["features"]:
        props = feat["properties"]
        name2 = props.get("NAME_2", "").lower()
        varname = props.get("VARNAME_2", "").lower()
        # Match various spellings
        if any(k in name2 or k in varname for k in ["dangk", "dang k"]):
            dangkor_features.append(feat)
            logger.info(
                "Found: NAME_2=%s, VARNAME_2=%s, TYPE_2=%s",
                props.get("NAME_2"), props.get("VARNAME_2"), props.get("TYPE_2"),
            )

    if not dangkor_features:
        # List all Phnom Penh districts for debugging
        pp_features = [
            f for f in gadm["features"]
            if "phnom penh" in f["properties"].get("NAME_1", "").lower()
        ]
        logger.warning(
            "Dangkor not found. Phnom Penh districts: %s",
            [f["properties"]["NAME_2"] for f in pp_features],
        )
        raise ValueError("Could not find Dangkor District in GADM data")

    # Save as GeoJSON
    boundary_geojson = {
        "type": "FeatureCollection",
        "features": dangkor_features,
    }
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    with open(boundary_path, "w") as f:
        json.dump(boundary_geojson, f)

    logger.info("Saved boundary to %s", boundary_path)
    return boundary_path


def build_grid() -> Path:
    """Build a 250m rectangular grid clipped to the Dangkor boundary.

    Returns:
        Path to the saved grid GeoJSON file.
    """
    import time

    t0 = time.time()
    district_cfg = load_district()
    grid_cfg = district_cfg["grid"]
    cell_size_m: int = grid_cfg["cell_size_m"]
    id_prefix: str = grid_cfg["id_prefix"]
    bbox = district_cfg["bbox"]  # [west, south, east, north]

    # Load boundary
    boundary_path = STATIC_DIR / "dangkor_boundary.geojson"
    if not boundary_path.exists():
        boundary_path = download_boundary()

    boundary_gdf = gpd.read_file(boundary_path)
    boundary_union = boundary_gdf.geometry.union_all()

    # Convert bbox extents to a projected CRS for meter-based grid
    # Use UTM zone 48N (EPSG:32648) which covers Cambodia
    utm_crs = "EPSG:32648"
    transformer_to_utm = Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
    transformer_to_wgs = Transformer.from_crs(utm_crs, "EPSG:4326", always_xy=True)

    west, south, east, north = bbox
    x_min, y_min = transformer_to_utm.transform(west, south)
    x_max, y_max = transformer_to_utm.transform(east, north)

    logger.info(
        "Grid extent (UTM): %.0f-%.0f E, %.0f-%.0f N (%.0f x %.0f m)",
        x_min, x_max, y_min, y_max, x_max - x_min, y_max - y_min,
    )

    # Generate grid cells
    cols = int(np.ceil((x_max - x_min) / cell_size_m))
    rows = int(np.ceil((y_max - y_min) / cell_size_m))
    logger.info("Grid dimensions: %d cols x %d rows = %d cells (before clipping)", cols, rows, cols * rows)

    # Project boundary to UTM for clipping
    boundary_utm = boundary_gdf.to_crs(utm_crs).geometry.union_all()

    cells = []
    grid_id_counter = 0

    for row in range(rows):
        for col in range(cols):
            # Cell bounds in UTM
            cx_min = x_min + col * cell_size_m
            cx_max = cx_min + cell_size_m
            cy_min = y_min + row * cell_size_m
            cy_max = cy_min + cell_size_m

            cell_utm = box(cx_min, cy_min, cx_max, cy_max)

            # Check if cell intersects boundary
            if not cell_utm.intersects(boundary_utm):
                continue

            # Convert corners back to WGS84
            lon_min, lat_min = transformer_to_wgs.transform(cx_min, cy_min)
            lon_max, lat_max = transformer_to_wgs.transform(cx_max, cy_max)
            centroid_lon = (lon_min + lon_max) / 2
            centroid_lat = (lat_min + lat_max) / 2

            # Cell geometry in WGS84
            cell_wgs = box(lon_min, lat_min, lon_max, lat_max)

            grid_id = f"{id_prefix}-{grid_id_counter:05d}"
            grid_id_counter += 1

            # Compute area in m2 (use UTM cell)
            area_m2 = cell_size_m * cell_size_m

            cells.append({
                "type": "Feature",
                "geometry": mapping(cell_wgs),
                "properties": {
                    "grid_id": grid_id,
                    "centroid_lat": round(centroid_lat, 6),
                    "centroid_lon": round(centroid_lon, 6),
                    "bbox": [
                        round(lon_min, 6),
                        round(lat_min, 6),
                        round(lon_max, 6),
                        round(lat_max, 6),
                    ],
                    "area_m2": area_m2,
                    # Placeholders — filled by static terrain processing
                    "elevation_m": None,
                    "hand_m": None,
                    "dominant_landcover": None,
                    "assigned_cn": None,
                    "commune_name": None,
                },
            })

    logger.info("Grid cells after clipping: %d (from %d total)", len(cells), cols * rows)

    # Save grid
    grid_geojson = {
        "type": "FeatureCollection",
        "features": cells,
    }
    grid_path = STATIC_DIR / "grid.geojson"
    with open(grid_path, "w") as f:
        json.dump(grid_geojson, f)

    elapsed = time.time() - t0
    logger.info("Grid saved to %s (%.1fs)", grid_path, elapsed)

    return grid_path


def render_verification_png(grid_path: Path, boundary_path: Path) -> Path:
    """Render a PNG showing the grid overlaid on the boundary for visual verification.

    Args:
        grid_path: Path to grid.geojson
        boundary_path: Path to dangkor_boundary.geojson

    Returns:
        Path to the saved PNG file.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    grid_gdf = gpd.read_file(grid_path)
    boundary_gdf = gpd.read_file(boundary_path)

    fig, ax = plt.subplots(1, 1, figsize=(12, 10))
    boundary_gdf.plot(ax=ax, facecolor="none", edgecolor="red", linewidth=2, label="Boundary")
    grid_gdf.plot(ax=ax, facecolor="lightblue", edgecolor="gray", linewidth=0.3, alpha=0.5, label="Grid cells")
    ax.set_title(f"Dangkor District Grid — {len(grid_gdf)} cells (250m)", fontsize=14)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.legend()

    png_path = STATIC_DIR / "grid_verification.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Verification PNG saved to %s", png_path)
    return png_path


if __name__ == "__main__":
    boundary = download_boundary()
    grid = build_grid()
    render_verification_png(grid, boundary)
