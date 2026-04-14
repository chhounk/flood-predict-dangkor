"""Inundation flagging — determines which cells are flooded per scenario.

A cell is flagged as flooded if EITHER condition is met:
1. HAND < threshold AND accumulated upstream runoff > drainage capacity
2. Local runoff accumulation > local storage capacity (bathtub model)

The bathtub fallback is essential for Dangkor's flat floodplain where
HAND is ambiguous and local ponding is the primary flood mechanism.

All volumes in m³ unless otherwise noted.
"""

import numpy as np


def flag_inundation(
    hand_m: np.ndarray,
    accumulated_runoff_m3: np.ndarray,
    local_runoff_m3: np.ndarray,
    hand_threshold_m: float,
    drainage_capacity_m3: float,
    local_storage_m3: float,
) -> np.ndarray:
    """Flag cells as flooded (1) or not (0) per timestep.

    Args:
        hand_m: HAND per cell, shape (n_cells,). Units: meters.
        accumulated_runoff_m3: Upstream accumulated runoff per cell per timestep,
                               shape (n_cells, n_timesteps). Units: m³.
        local_runoff_m3: Local runoff volume per cell per timestep,
                         shape (n_cells, n_timesteps). Units: m³.
        hand_threshold_m: HAND threshold below which cells are flood-susceptible. Units: meters.
        drainage_capacity_m3: Drainage capacity per cell per timestep. Units: m³.
        local_storage_m3: Local ponding storage capacity per cell. Units: m³.

    Returns:
        Boolean array shape (n_cells, n_timesteps), True = flooded.
    """
    # Condition 1: Low-lying cell with upstream runoff exceeding drainage
    low_hand = hand_m[:, np.newaxis] < hand_threshold_m  # (n_cells, 1) broadcast to (n_cells, n_timesteps)
    exceeds_drainage = accumulated_runoff_m3 > drainage_capacity_m3

    condition_1 = low_hand & exceeds_drainage

    # Condition 2: Local ponding exceeds storage (bathtub model)
    # Cumulative local runoff over time vs storage
    cumulative_local = np.cumsum(local_runoff_m3, axis=1)
    condition_2 = cumulative_local > local_storage_m3

    # Flooded if either condition is met
    flooded = condition_1 | condition_2

    return flooded
