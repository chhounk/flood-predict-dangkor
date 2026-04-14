"""Layer 4 fusion — combines physics probability with regional signals.

final_P = clip(physics_P × regional_amplifier, 0, 1)

The regional_amplifier starts at 1.0 and is adjusted by active Layer-3 plugins.
In v1 with only GPM, the amplifier stays at 1.0 but the GPM agreement factor
is logged and displayed as a confidence indicator.
"""

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def fuse(
    physics_results: dict[str, Any],
    regional_signals: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    """Apply regional signal amplification to physics probability.

    Args:
        physics_results: Dict with 'physics_P' ndarray (n_cells, n_timesteps)
                         and 'timestep_times' list.
        regional_signals: Dict of signal name -> value (float or None).
        thresholds: Thresholds config dict.

    Returns:
        Dict with 'final_P' ndarray (n_cells, n_timesteps)
                  and 'timestep_times' list.
    """
    physics_P = physics_results["physics_P"]
    amp_config = thresholds["regional_amplifier"]

    # Start with base amplifier
    amplifier = amp_config["base"]  # 1.0

    # GloFAS boost (when implemented and signaling flood)
    glofas_value = regional_signals.get("glofas")
    if glofas_value is not None and glofas_value > 0.5:
        amplifier = amp_config["glofas_flood_boost"]  # 1.3
        logger.info("GloFAS active: amplifier boosted to %.1f", amplifier)

    # Apply amplifier
    final_P = np.clip(physics_P * amplifier, 0.0, 1.0)

    # Log GPM agreement (confidence indicator, not used in probability)
    gpm = regional_signals.get("gpm_agreement")
    if gpm is not None:
        logger.info("GPM agreement: %.2f (confidence indicator only)", gpm)

    logger.info(
        "Fusion: amplifier=%.2f, max physics_P=%.3f, max final_P=%.3f",
        amplifier, physics_P.max(), final_P.max(),
    )

    return {
        "final_P": final_P,
        "timestep_times": physics_results["timestep_times"],
    }
