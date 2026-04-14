"""Flow routing — uses pre-computed static flow accumulation weights.

The key optimization: pysheds flow direction and accumulation are computed ONCE
(in load_static.py) and saved as rasters. At runtime, we use the static
flow accumulation values as a proxy for upstream contributing area.

For each Monte Carlo scenario, we:
1. Compute runoff per cell (from SCS CN)
2. Estimate accumulated upstream runoff using static flow accumulation weights
3. This avoids re-running pysheds per scenario (saving ~10 min)

Limitation: this assumes the spatial pattern of runoff is uniform enough that
static weights are a reasonable approximation. Documented in LIMITATIONS.md.
"""

import logging

import numpy as np
import rasterio

from src.config import STATIC_DIR

logger = logging.getLogger(__name__)


def load_flow_accumulation_weights(cell_centroids: np.ndarray) -> np.ndarray:
    """Load pre-computed flow accumulation values for each grid cell.

    The flow accumulation raster gives the count of upstream cells for each
    pixel. We sample this at each grid cell centroid.

    Args:
        cell_centroids: ndarray shape (n_cells, 2) with [lat, lon] per cell.

    Returns:
        ndarray shape (n_cells,) with flow accumulation (upstream cell count) per cell.
    """
    acc_path = STATIC_DIR / "flow_accumulation.tif"

    with rasterio.open(acc_path) as src:
        acc_data = src.read(1).astype(np.float64)
        weights = np.zeros(cell_centroids.shape[0], dtype=np.float64)

        for i in range(cell_centroids.shape[0]):
            lat, lon = cell_centroids[i]
            try:
                row, col = src.index(lon, lat)
                if 0 <= row < acc_data.shape[0] and 0 <= col < acc_data.shape[1]:
                    weights[i] = acc_data[row, col]
                else:
                    weights[i] = 1.0
            except Exception:
                weights[i] = 1.0

    logger.info(
        "Flow accumulation weights: min=%.0f, max=%.0f, mean=%.0f cells",
        weights.min(), weights.max(), weights.mean(),
    )
    return weights


def estimate_accumulated_runoff(
    runoff_mm: np.ndarray,
    flow_acc_weights: np.ndarray,
    cell_area_m2: float = 62500.0,
) -> np.ndarray:
    """Estimate accumulated upstream runoff at each cell using static weights.

    Accumulated runoff ≈ local_runoff_volume × (1 + log(upstream_cells))

    The log scaling prevents extreme values at high-accumulation cells while
    still reflecting the upstream contribution. This is an approximation —
    true routing would solve the full drainage network.

    Args:
        runoff_mm: Surface runoff per cell per timestep, shape (n_cells, n_timesteps). Units: mm.
        flow_acc_weights: Upstream cell count per cell, shape (n_cells,). Dimensionless.
        cell_area_m2: Area of each grid cell in m². Default: 250m × 250m = 62,500 m².

    Returns:
        Accumulated runoff volume per cell per timestep, shape (n_cells, n_timesteps). Units: m³.
    """
    # Convert runoff from mm to m3: mm * m2 / 1000 = m3
    local_volume_m3 = runoff_mm * cell_area_m2 / 1000.0  # (n_cells, n_timesteps)

    # Scale by upstream contributing area (log-dampened)
    # Add 1 to avoid log(0); minimum weight is 1 (the cell itself)
    upstream_factor = 1.0 + np.log1p(np.maximum(flow_acc_weights, 1.0))  # (n_cells,)

    accumulated_m3 = local_volume_m3 * upstream_factor[:, np.newaxis]  # (n_cells, n_timesteps)

    return accumulated_m3
