"""Configuration loader — single source of truth for all YAML config files."""

from pathlib import Path
from typing import Any

import yaml


# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
STATIC_DIR = DATA_DIR / "static"
OUTPUT_DIR = DATA_DIR / "outputs"
DOCS_DIR = PROJECT_ROOT / "docs"

# Engine version — bump on meaningful changes
ENGINE_VERSION = "engine-v0.1"


def _load_yaml(filename: str) -> dict[str, Any]:
    """Load a YAML config file from the config directory."""
    path = CONFIG_DIR / filename
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_district() -> dict[str, Any]:
    """Load district configuration (boundary, grid, sample points, communes)."""
    return _load_yaml("district.yaml")


def load_thresholds() -> dict[str, Any]:
    """Load risk classification thresholds and ensemble parameters."""
    return _load_yaml("thresholds.yaml")


def load_cn_lookup() -> dict[str, Any]:
    """Load SCS Curve Number lookup table (land cover × soil group → CN)."""
    return _load_yaml("cn_lookup.yaml")


def get_bbox() -> list[float]:
    """Return bounding box [west, south, east, north] in WGS84 degrees."""
    return load_district()["bbox"]


def get_sample_points() -> list[dict[str, float]]:
    """Return list of {lat, lon} dicts for Open-Meteo forecast queries."""
    return load_district()["sample_points"]


def get_grid_config() -> dict[str, Any]:
    """Return grid configuration (cell_size_m, id_prefix, crs)."""
    return load_district()["grid"]


def get_forecast_config() -> dict[str, Any]:
    """Return forecast configuration (horizon, timestep, models, archive days)."""
    return load_district()["forecast"]
