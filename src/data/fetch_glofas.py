"""GloFAS data fetch — STUB for river discharge forecasts.

TODO: Implement GloFAS forecast retrieval from Copernicus CDS.
API: https://cds.climate.copernicus.eu/api
Dataset: 'cems-glofas-forecast'

Requires free CDS account and API key.
Target stations near Dangkor:
- Phnom Penh (Chaktomuk confluence)
- Bassac River downstream gauge
"""

import logging

logger = logging.getLogger(__name__)


def fetch_glofas_forecast(station_id: str = "phnom_penh") -> dict | None:
    """Fetch GloFAS river discharge forecast.

    STUB — returns None in v1.

    Args:
        station_id: GloFAS station identifier.

    Returns:
        None in v1.
    """
    logger.info("GloFAS fetch: STUB — not implemented in v1")
    return None
