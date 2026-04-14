"""HAND (Height Above Nearest Drainage) utilities.

HAND values are pre-computed in load_static.py and stored per grid cell.
This module provides helper functions for using HAND in inundation flagging.
"""

import numpy as np


def load_hand_values(grid_features: list[dict]) -> np.ndarray:
    """Extract HAND values from grid cell properties.

    Args:
        grid_features: List of GeoJSON feature dicts with 'hand_m' in properties.

    Returns:
        ndarray shape (n_cells,) with HAND values in meters.
        Cells with missing HAND get a default of 10.0m (conservative — not flood-prone).
    """
    hand = np.array([
        f["properties"].get("hand_m") if f["properties"].get("hand_m") is not None else 10.0
        for f in grid_features
    ], dtype=np.float64)

    return hand
