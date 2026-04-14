"""Tests for grid building."""

import json
from pathlib import Path

from src.config import STATIC_DIR


def test_grid_exists():
    """Grid GeoJSON file must exist in data/static/."""
    grid_path = STATIC_DIR / "grid.geojson"
    assert grid_path.exists(), f"Grid file not found: {grid_path}"


def test_grid_has_features():
    """Grid must contain at least 1000 cells for Dangkor."""
    grid_path = STATIC_DIR / "grid.geojson"
    with open(grid_path) as f:
        grid = json.load(f)
    assert grid["type"] == "FeatureCollection"
    assert len(grid["features"]) > 1000


def test_grid_ids_unique():
    """All grid IDs must be unique."""
    grid_path = STATIC_DIR / "grid.geojson"
    with open(grid_path) as f:
        grid = json.load(f)
    ids = [f["properties"]["grid_id"] for f in grid["features"]]
    assert len(ids) == len(set(ids)), "Duplicate grid IDs found"


def test_grid_id_format():
    """Grid IDs must follow D-XXXXX format."""
    grid_path = STATIC_DIR / "grid.geojson"
    with open(grid_path) as f:
        grid = json.load(f)
    for feat in grid["features"]:
        gid = feat["properties"]["grid_id"]
        assert gid.startswith("D-"), f"Invalid grid_id prefix: {gid}"
        assert len(gid) == 7, f"Invalid grid_id length: {gid}"


def test_grid_has_centroids():
    """Every cell must have centroid_lat and centroid_lon."""
    grid_path = STATIC_DIR / "grid.geojson"
    with open(grid_path) as f:
        grid = json.load(f)
    for feat in grid["features"]:
        props = feat["properties"]
        assert props["centroid_lat"] is not None
        assert props["centroid_lon"] is not None
        assert 11.0 < props["centroid_lat"] < 12.0, "Latitude out of range"
        assert 104.0 < props["centroid_lon"] < 105.0, "Longitude out of range"


def test_grid_has_terrain_attributes():
    """Grid cells should have terrain attributes after static processing."""
    grid_path = STATIC_DIR / "grid.geojson"
    with open(grid_path) as f:
        grid = json.load(f)
    # Check first cell has elevation
    first = grid["features"][0]["properties"]
    assert first["elevation_m"] is not None, "Missing elevation_m"
    assert first["assigned_cn"] is not None, "Missing assigned_cn"
