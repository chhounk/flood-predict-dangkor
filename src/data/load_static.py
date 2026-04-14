"""Download and process static terrain data: DEM, HAND, flow accumulation, land cover.

Downloads Copernicus DEM GLO-30 and ESA WorldCover 2021, clips to Dangkor bbox,
computes flow routing and HAND using pysheds, and assigns CN values per grid cell.

All static products are saved to data/static/ and only recomputed if missing.

Usage:
    python -m src.data.load_static
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.mask import mask as rasterio_mask
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.transform import from_bounds
from shapely.geometry import shape, box, mapping

from src.config import PROJECT_ROOT, STATIC_DIR, load_district, load_cn_lookup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def download_dem(bbox: list[float]) -> Path:
    """Download Copernicus DEM GLO-30 for the given bounding box.

    Uses the AWS Open Data mirror (free, no auth).
    Falls back to SRTM 30m from OpenTopography if needed.

    Args:
        bbox: [west, south, east, north] in WGS84 degrees.

    Returns:
        Path to the clipped DEM GeoTIFF.
    """
    import requests

    clipped_path = STATIC_DIR / "dem_clipped.tif"
    if clipped_path.exists():
        logger.info("DEM already exists: %s", clipped_path)
        return clipped_path

    raw_dir = STATIC_DIR / "dem_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    west, south, east, north = bbox

    # Copernicus DEM tile naming: Copernicus_DSM_COG_10_N{lat}_00_E{lon}_00_DEM
    # Tiles are 1x1 degree, named by SW corner
    tile_lat = int(np.floor(south))
    tile_lon = int(np.floor(west))

    # Try multiple tile naming conventions
    lat_str = f"N{tile_lat:02d}" if tile_lat >= 0 else f"S{abs(tile_lat):02d}"
    lon_str = f"E{tile_lon:03d}" if tile_lon >= 0 else f"W{abs(tile_lon):03d}"

    tile_name = f"Copernicus_DSM_COG_10_{lat_str}_00_{lon_str}_00_DEM"
    raw_path = raw_dir / f"{tile_name}.tif"

    if not raw_path.exists():
        # Try AWS S3 public bucket
        s3_url = f"https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/{tile_name}/{tile_name}.tif"
        logger.info("Downloading DEM tile from %s", s3_url)

        try:
            resp = requests.get(s3_url, timeout=300, stream=True)
            resp.raise_for_status()
            with open(raw_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info("DEM tile downloaded: %s (%.1f MB)", raw_path, raw_path.stat().st_size / 1e6)
        except Exception as e:
            logger.warning("Copernicus DEM download failed: %s. Trying SRTM fallback...", e)
            return _download_srtm_fallback(bbox, raw_dir, clipped_path)

    # Clip to bbox
    _clip_raster(raw_path, clipped_path, bbox)
    logger.info("DEM clipped to bbox: %s", clipped_path)
    return clipped_path


def _download_srtm_fallback(bbox: list[float], raw_dir: Path, clipped_path: Path) -> Path:
    """Download SRTM 30m from OpenTopography as fallback.

    Args:
        bbox: [west, south, east, north]
        raw_dir: Directory for raw downloads.
        clipped_path: Output path for clipped DEM.

    Returns:
        Path to clipped DEM.
    """
    import requests

    west, south, east, north = bbox
    # OpenTopography Global DEM API (free, no auth for SRTM)
    url = "https://portal.opentopography.org/API/globaldem"
    params = {
        "demtype": "SRTMGL1",
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": "GTiff",
    }
    logger.info("Downloading SRTM from OpenTopography...")
    resp = requests.get(url, params=params, timeout=300)
    resp.raise_for_status()

    srtm_path = raw_dir / "srtm_dangkor.tif"
    with open(srtm_path, "wb") as f:
        f.write(resp.content)
    logger.info("SRTM downloaded: %s (%.1f MB)", srtm_path, srtm_path.stat().st_size / 1e6)

    _clip_raster(srtm_path, clipped_path, bbox)
    return clipped_path


def _clip_raster(src_path: Path, dst_path: Path, bbox: list[float]) -> None:
    """Clip a raster to a bounding box.

    Args:
        src_path: Input raster path.
        dst_path: Output clipped raster path.
        bbox: [west, south, east, north]
    """
    west, south, east, north = bbox
    clip_geom = [mapping(box(west, south, east, north))]

    with rasterio.open(src_path) as src:
        out_image, out_transform = rasterio_mask(src, clip_geom, crop=True)
        out_meta = src.meta.copy()
        out_meta.update({
            "driver": "GTiff",
            "height": out_image.shape[1],
            "width": out_image.shape[2],
            "transform": out_transform,
            "compress": "deflate",
        })

        with rasterio.open(dst_path, "w", **out_meta) as dst:
            dst.write(out_image)


def compute_flow_routing(dem_path: Path) -> dict[str, Path]:
    """Compute filled DEM, D8 flow direction, and flow accumulation using pysheds.

    Args:
        dem_path: Path to the clipped DEM GeoTIFF.

    Returns:
        Dict of paths: {'filled': ..., 'fdir': ..., 'acc': ...}
    """
    from pysheds.grid import Grid

    filled_path = STATIC_DIR / "dem_filled.tif"
    fdir_path = STATIC_DIR / "flow_direction.tif"
    acc_path = STATIC_DIR / "flow_accumulation.tif"

    if filled_path.exists() and fdir_path.exists() and acc_path.exists():
        logger.info("Flow routing files already exist")
        return {"filled": filled_path, "fdir": fdir_path, "acc": acc_path}

    t0 = time.time()
    logger.info("Computing flow routing from DEM...")

    grid = Grid.from_raster(str(dem_path))
    dem = grid.read_raster(str(dem_path))

    # Fill depressions
    filled = grid.fill_depressions(dem)
    logger.info("  Filled depressions (%.1fs)", time.time() - t0)

    # Resolve flats
    inflated = grid.resolve_flats(filled)
    logger.info("  Resolved flats (%.1fs)", time.time() - t0)

    # D8 flow direction
    fdir = grid.flowdir(inflated)
    logger.info("  Flow direction computed (%.1fs)", time.time() - t0)

    # Flow accumulation (cell counts)
    acc = grid.accumulation(fdir)
    logger.info("  Flow accumulation computed (%.1fs)", time.time() - t0)

    # Save as GeoTIFFs
    _save_pysheds_raster(grid, filled, filled_path, dem_path)
    _save_pysheds_raster(grid, fdir, fdir_path, dem_path, dtype="int32")
    _save_pysheds_raster(grid, acc, acc_path, dem_path, dtype="float64")

    logger.info("Flow routing complete (%.1fs)", time.time() - t0)
    return {"filled": filled_path, "fdir": fdir_path, "acc": acc_path}


def compute_hand(dem_path: Path, fdir_path: Path, acc_path: Path) -> Path:
    """Compute Height Above Nearest Drainage (HAND) raster.

    HAND = elevation at cell - elevation at the nearest drainage cell
    (following D8 flow direction downstream until flow accumulation > threshold).

    Args:
        dem_path: Path to filled DEM.
        fdir_path: Path to flow direction raster.
        acc_path: Path to flow accumulation raster.

    Returns:
        Path to the HAND raster GeoTIFF.
    """
    hand_path = STATIC_DIR / "hand.tif"
    if hand_path.exists():
        logger.info("HAND raster already exists: %s", hand_path)
        return hand_path

    t0 = time.time()
    logger.info("Computing HAND raster...")

    with rasterio.open(dem_path) as dem_src:
        dem = dem_src.read(1).astype(np.float64)
        meta = dem_src.meta.copy()

    with rasterio.open(acc_path) as acc_src:
        acc = acc_src.read(1).astype(np.float64)

    with rasterio.open(fdir_path) as fdir_src:
        fdir = fdir_src.read(1).astype(np.int32)

    rows, cols = dem.shape

    # Define drainage threshold: cells with accumulation > threshold are "drainage"
    # Threshold of 100 cells ≈ 100 * 30m * 30m ≈ 0.09 km² contributing area
    drainage_threshold = 100
    is_drainage = acc > drainage_threshold

    # D8 direction offsets (pysheds convention: 1=E, 2=SE, 4=S, 8=SW, 16=W, 32=NW, 64=N, 128=NE)
    d8_offsets = {
        1: (0, 1),    # E
        2: (1, 1),    # SE
        4: (1, 0),    # S
        8: (1, -1),   # SW
        16: (0, -1),  # W
        32: (-1, -1), # NW
        64: (-1, 0),  # N
        128: (-1, 1), # NE
    }

    # For each cell, follow flow direction to nearest drainage cell
    hand = np.full_like(dem, np.nan)
    max_steps = rows * cols  # safety limit

    for r in range(rows):
        for c in range(cols):
            if is_drainage[r, c]:
                hand[r, c] = 0.0
                continue

            # Trace downstream
            cr, cc = r, c
            steps = 0
            found = False
            while steps < max_steps:
                d = fdir[cr, cc]
                if d not in d8_offsets:
                    break
                dr, dc = d8_offsets[d]
                nr, nc = cr + dr, cc + dc
                if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                    break
                if is_drainage[nr, nc]:
                    hand[r, c] = max(0.0, dem[r, c] - dem[nr, nc])
                    found = True
                    break
                cr, cc = nr, nc
                steps += 1

            if not found:
                # Assign high HAND for cells that don't reach drainage
                hand[r, c] = max(0.0, dem[r, c] - np.nanmin(dem))

    # Replace NaN with max HAND
    hand = np.nan_to_num(hand, nan=np.nanmax(hand[~np.isnan(hand)]) if np.any(~np.isnan(hand)) else 10.0)

    # Save
    meta.update({"dtype": "float64", "compress": "deflate"})
    with rasterio.open(hand_path, "w", **meta) as dst:
        dst.write(hand, 1)

    logger.info(
        "HAND computed: min=%.1fm, max=%.1fm, mean=%.1fm (%.1fs)",
        hand.min(), hand.max(), hand.mean(), time.time() - t0,
    )
    return hand_path


def download_worldcover(bbox: list[float]) -> Path:
    """Download ESA WorldCover 2021 for the given bounding box.

    Uses GDAL's /vsicurl/ to read only the needed window from the COG
    on S3, avoiding downloading the full ~900MB tile.

    Args:
        bbox: [west, south, east, north] in WGS84 degrees.

    Returns:
        Path to the clipped WorldCover GeoTIFF.
    """
    from rasterio.windows import from_bounds as window_from_bounds

    clipped_path = STATIC_DIR / "worldcover_clipped.tif"
    if clipped_path.exists():
        logger.info("WorldCover already exists: %s", clipped_path)
        return clipped_path

    west, south, east, north = bbox

    # ESA WorldCover tile naming: 3x3 degree tiles
    tile_lat = (int(np.floor(south)) // 3) * 3
    tile_lon = (int(np.floor(west)) // 3) * 3

    lat_str = f"N{tile_lat:02d}" if tile_lat >= 0 else f"S{abs(tile_lat):02d}"
    lon_str = f"E{tile_lon:03d}" if tile_lon >= 0 else f"W{abs(tile_lon):03d}"

    tile_name = f"ESA_WorldCover_10m_2021_v200_{lat_str}{lon_str}_Map"
    s3_url = f"/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/{tile_name}.tif"

    logger.info("Reading WorldCover COG window from S3: %s", tile_name)

    try:
        with rasterio.open(s3_url) as src:
            # Compute the pixel window for our bbox
            window = window_from_bounds(west, south, east, north, src.transform)
            # Read only that window
            data = src.read(1, window=window)
            win_transform = src.window_transform(window)

            meta = src.meta.copy()
            meta.update({
                "driver": "GTiff",
                "height": data.shape[0],
                "width": data.shape[1],
                "transform": win_transform,
                "compress": "deflate",
            })

            with rasterio.open(clipped_path, "w", **meta) as dst:
                dst.write(data, 1)

        logger.info(
            "WorldCover clipped: %dx%d pixels (%.1f MB)",
            data.shape[1], data.shape[0], clipped_path.stat().st_size / 1e6,
        )
    except Exception as e:
        logger.warning("WorldCover COG read failed: %s", e)
        logger.info("Creating synthetic land cover as fallback...")
        return _create_synthetic_worldcover(bbox, clipped_path)

    return clipped_path


def _create_synthetic_worldcover(bbox: list[float], output_path: Path) -> Path:
    """Create a synthetic WorldCover raster as fallback.

    Assigns class 50 (Built-up) for urban Dangkor as a conservative default.
    """
    west, south, east, north = bbox
    # Create a ~250m resolution raster
    res = 0.0025  # ~250m in degrees
    width = int((east - west) / res)
    height = int((north - south) / res)

    transform = from_bounds(west, south, east, north, width, height)

    # Default to mixed: 50 (built-up) — conservative for flood modeling
    data = np.full((1, height, width), 50, dtype=np.uint8)

    meta = {
        "driver": "GTiff",
        "dtype": "uint8",
        "width": width,
        "height": height,
        "count": 1,
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
    }

    with rasterio.open(output_path, "w", **meta) as dst:
        dst.write(data)

    logger.info("Synthetic WorldCover created: %s", output_path)
    return output_path


def _save_pysheds_raster(
    grid: Any,
    data: Any,
    output_path: Path,
    reference_path: Path,
    dtype: str = "float32",
) -> None:
    """Save a pysheds raster to GeoTIFF using reference raster metadata.

    Args:
        grid: pysheds Grid object.
        data: pysheds Raster data.
        output_path: Output file path.
        reference_path: Reference raster for CRS and transform.
        dtype: Output data type.
    """
    with rasterio.open(reference_path) as ref:
        meta = ref.meta.copy()

    arr = np.array(data).astype(dtype)
    meta.update({
        "dtype": dtype,
        "height": arr.shape[0],
        "width": arr.shape[1],
        "compress": "deflate",
    })

    with rasterio.open(output_path, "w", **meta) as dst:
        dst.write(arr, 1)


def assign_cell_attributes(grid_path: Path, dem_path: Path, hand_path: Path, lc_path: Path) -> Path:
    """Assign static terrain attributes (elevation, HAND, land cover, CN) to grid cells.

    Updates grid.geojson in-place with populated attribute fields.

    Args:
        grid_path: Path to grid.geojson.
        dem_path: Path to clipped DEM raster.
        hand_path: Path to HAND raster.
        lc_path: Path to clipped WorldCover raster.

    Returns:
        Path to the updated grid.geojson.
    """
    t0 = time.time()
    cn_config = load_cn_lookup()
    cn_table = cn_config["cn_table"]
    soil_group = cn_config["default_soil_group"]

    with open(grid_path, "r") as f:
        grid = json.load(f)

    # Open rasters
    dem_src = rasterio.open(dem_path)
    hand_src = rasterio.open(hand_path)
    lc_src = rasterio.open(lc_path)

    for feat in grid["features"]:
        props = feat["properties"]
        lat = props["centroid_lat"]
        lon = props["centroid_lon"]

        # Sample elevation at centroid
        try:
            row, col = dem_src.index(lon, lat)
            dem_data = dem_src.read(1)
            if 0 <= row < dem_data.shape[0] and 0 <= col < dem_data.shape[1]:
                props["elevation_m"] = round(float(dem_data[row, col]), 1)
            else:
                props["elevation_m"] = None
        except Exception:
            props["elevation_m"] = None

        # Sample HAND at centroid
        try:
            row, col = hand_src.index(lon, lat)
            hand_data = hand_src.read(1)
            if 0 <= row < hand_data.shape[0] and 0 <= col < hand_data.shape[1]:
                props["hand_m"] = round(float(hand_data[row, col]), 2)
            else:
                props["hand_m"] = None
        except Exception:
            props["hand_m"] = None

        # Sample land cover at centroid
        try:
            row, col = lc_src.index(lon, lat)
            lc_data = lc_src.read(1)
            if 0 <= row < lc_data.shape[0] and 0 <= col < lc_data.shape[1]:
                lc_class = int(lc_data[row, col])
                props["dominant_landcover"] = lc_class

                # Look up CN
                if lc_class in cn_table:
                    props["assigned_cn"] = cn_table[lc_class].get(soil_group, 85)
                else:
                    props["assigned_cn"] = 85  # default
            else:
                props["dominant_landcover"] = None
                props["assigned_cn"] = 85
        except Exception:
            props["dominant_landcover"] = None
            props["assigned_cn"] = 85

    dem_src.close()
    hand_src.close()
    lc_src.close()

    # Save updated grid
    with open(grid_path, "w") as f:
        json.dump(grid, f)

    logger.info("Grid attributes assigned (%d cells, %.1fs)", len(grid["features"]), time.time() - t0)
    return grid_path


def process_all_static() -> dict[str, Path]:
    """Run the full static data processing pipeline.

    Returns:
        Dict of paths to all static data products.
    """
    t0 = time.time()
    district = load_district()
    bbox = district["bbox"]

    logger.info("=== Static Data Processing ===")
    logger.info("BBox: %s", bbox)

    # 1. DEM
    dem_path = download_dem(bbox)

    # 2. Flow routing
    routing = compute_flow_routing(dem_path)

    # 3. HAND
    hand_path = compute_hand(routing["filled"], routing["fdir"], routing["acc"])

    # 4. WorldCover
    lc_path = download_worldcover(bbox)

    # 5. Assign attributes to grid cells
    grid_path = STATIC_DIR / "grid.geojson"
    assign_cell_attributes(grid_path, dem_path, hand_path, lc_path)

    logger.info("=== Static processing complete (%.1fs) ===", time.time() - t0)

    return {
        "dem": dem_path,
        "filled": routing["filled"],
        "fdir": routing["fdir"],
        "acc": routing["acc"],
        "hand": hand_path,
        "worldcover": lc_path,
        "grid": grid_path,
    }


if __name__ == "__main__":
    process_all_static()
