"""SCS Curve Number runoff calculation.

Converts rainfall to runoff using the USDA Soil Conservation Service
Curve Number method (TR-55, 1986).

All values in millimeters (mm) unless otherwise noted.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)


def compute_runoff(
    rainfall_mm: np.ndarray,
    cn: np.ndarray,
    amc_factor: float = 1.0,
) -> np.ndarray:
    """Compute surface runoff using the SCS Curve Number method.

    Q = (P - Ia)^2 / (P - Ia + S)  when P > Ia, else Q = 0

    Where:
        S = (25400 / CN) - 254   (maximum retention, mm)
        Ia = 0.2 * S             (initial abstraction, mm)

    Args:
        rainfall_mm: Rainfall depth per cell per timestep, shape (n_cells, n_timesteps). Units: mm.
        cn: Curve Number per cell, shape (n_cells,). Dimensionless, range 1-100.
        amc_factor: Antecedent moisture condition multiplier for CN.
                    0.85 = dry (AMC-I), 1.0 = normal (AMC-II), 1.15 = wet (AMC-III).

    Returns:
        Surface runoff per cell per timestep, shape (n_cells, n_timesteps). Units: mm.
    """
    # Adjust CN for antecedent moisture condition
    cn_adj = np.clip(cn * amc_factor, 1.0, 98.0)  # Cap at 98 to avoid division issues

    # Maximum retention S (mm)
    s = (25400.0 / cn_adj) - 254.0  # (n_cells,)

    # Initial abstraction Ia (mm) — standard ratio Ia = 0.2 * S
    ia = 0.2 * s  # (n_cells,)

    # Broadcast for vectorized computation
    s_2d = s[:, np.newaxis]     # (n_cells, 1)
    ia_2d = ia[:, np.newaxis]   # (n_cells, 1)

    # Runoff Q (mm)
    excess = rainfall_mm - ia_2d
    excess = np.maximum(excess, 0.0)

    denominator = rainfall_mm - ia_2d + s_2d
    # Avoid division by zero where denominator <= 0
    denominator = np.maximum(denominator, 1e-6)

    runoff = np.where(
        rainfall_mm > ia_2d,
        (excess ** 2) / denominator,
        0.0,
    )

    return runoff


def get_amc_factor(five_day_rain_mm: float, amc_config: dict) -> float:
    """Determine AMC adjustment factor from 5-day antecedent rainfall.

    Args:
        five_day_rain_mm: Total rainfall in the preceding 5 days (mm).
        amc_config: Dict with 'dry', 'normal', 'wet' keys, each having
                    'threshold_mm' and 'cn_factor'.

    Returns:
        CN multiplication factor (0.85, 1.0, or 1.15 typically).
    """
    if five_day_rain_mm < amc_config["dry"]["threshold_mm"]:
        return amc_config["dry"]["cn_factor"]
    elif five_day_rain_mm < amc_config["wet"]["threshold_mm"]:
        if five_day_rain_mm < amc_config["normal"]["threshold_mm"]:
            return amc_config["normal"]["cn_factor"]
        else:
            return amc_config["wet"]["cn_factor"]
    else:
        return amc_config["wet"]["cn_factor"]
