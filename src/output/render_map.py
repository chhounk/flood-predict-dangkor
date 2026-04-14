"""Render a static PNG map of flood risk for README and quick visual checks."""

import json
import logging
from pathlib import Path

from src.config import OUTPUT_DIR, STATIC_DIR

logger = logging.getLogger(__name__)


def render_risk_map() -> Path:
    """Render the latest prediction as a static PNG map.

    Returns:
        Path to the saved PNG file.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import geopandas as gpd

    geojson_path = OUTPUT_DIR / "latest.geojson"
    boundary_path = STATIC_DIR / "dangkor_boundary.geojson"

    if not geojson_path.exists():
        logger.warning("No latest.geojson — skipping map render")
        return None

    gdf = gpd.read_file(geojson_path)
    boundary = gpd.read_file(boundary_path)

    colors = {1: "#d9d9d9", 2: "#fee08b", 3: "#f46d43", 4: "#d73027"}
    gdf["color"] = gdf["peak_level"].map(colors)

    fig, ax = plt.subplots(1, 1, figsize=(12, 10))
    boundary.plot(ax=ax, facecolor="none", edgecolor="black", linewidth=1.5)
    gdf.plot(ax=ax, color=gdf["color"], edgecolor="gray", linewidth=0.2, alpha=0.7)

    # Legend
    patches = [
        mpatches.Patch(color="#d9d9d9", label="L1 Safe (P < 0.10)"),
        mpatches.Patch(color="#fee08b", label="L2 Low (0.10-0.40)"),
        mpatches.Patch(color="#f46d43", label="L3 Moderate (0.40-0.85)"),
        mpatches.Patch(color="#d73027", label="L4 High (>= 0.85)"),
    ]
    ax.legend(handles=patches, loc="lower left", fontsize=10)

    # Load metadata for title
    json_path = OUTPUT_DIR / "latest.json"
    if json_path.exists():
        with open(json_path) as f:
            meta = json.load(f)
        title = f"Dangkor Flood Risk — {meta['run_id']}"
    else:
        title = "Dangkor Flood Risk"

    ax.set_title(title, fontsize=14)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

    png_path = OUTPUT_DIR / "risk_map.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Risk map saved: %s", png_path)
    return png_path
