"""GPM IMERG data fetch — wrapper for Layer 3 GPM signal.

This module exists as the data-fetch counterpart to signals/gpm_signal.py.
In v1, the actual GPM comparison uses Open-Meteo archive as a proxy.

TODO: Direct NASA GPM IMERG Late Run access via:
- GES DISC OPeNDAP: https://gpm1.gesdisc.eosdis.nasa.gov/opendap/
- PMM API: https://pmmpublisher.pps.eosdis.nasa.gov/
Both require free Earthdata account for authenticated access.
"""

import logging

logger = logging.getLogger(__name__)


def fetch_gpm_imerg_late(bbox: list[float], date: str) -> dict | None:
    """Fetch GPM IMERG Late Run precipitation for a bounding box and date.

    STUB — returns None in v1. The GPM signal in signals/gpm_signal.py
    uses Open-Meteo archive as a proxy instead.

    Args:
        bbox: [west, south, east, north] bounding box.
        date: ISO date string.

    Returns:
        None in v1.
    """
    logger.info("GPM IMERG fetch: STUB — using Open-Meteo archive proxy instead")
    return None
