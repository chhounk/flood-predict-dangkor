"""GloFAS signal — STUB for river flood forecasts.

TODO: Implement live GloFAS data fetch from Copernicus Emergency Management Service.
API endpoint: https://cds.climate.copernicus.eu/api
Dataset: 'cems-glofas-forecast'
Variables: river discharge forecast for Mekong/Bassac near Phnom Penh

When implemented, this plugin should:
1. Fetch 10-day river discharge forecast for stations near Dangkor
2. Compare forecast discharge against flood thresholds
3. Return a scalar 0-1 risk factor (0=normal, 1=major flood)
4. The fusion layer uses this to boost regional_amplifier up to 1.3

For now, returns None (signal unavailable).
"""

import logging
from typing import Any

from src.signals.base import RegionalSignal

logger = logging.getLogger(__name__)


class GloFASSignal(RegionalSignal):
    """GloFAS river flood forecast signal — STUB."""

    @property
    def name(self) -> str:
        return "GloFAS River Forecast"

    def fetch(self, district_config: dict[str, Any]) -> float | None:
        """Stub — returns None (signal unavailable in v1).

        Args:
            district_config: District configuration dict.

        Returns:
            None — GloFAS integration is not implemented in v1.
        """
        logger.info("GloFAS signal: STUB — not implemented in v1")
        return None
