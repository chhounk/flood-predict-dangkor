"""JAXA GSMaP (Global Satellite Mapping of Precipitation) signal — STUB.

GSMaP is an independent satellite-rainfall product from JAXA, combining data from
the GPM/GCOM-W1 microwave constellation with Himawari-8/9 geostationary IR. It
is algorithmically and organizationally independent of NASA GPM IMERG, which
makes agreement between the two a genuine confidence signal (not a duplication).

Advantages vs. GPM IMERG:
- GSMaP NRT (Near-Real-Time): ~4 hour latency
- GSMaP MVK (standard): ~3 day latency, higher quality
- Independent microwave-IR fusion algorithm

TODO (v1.5): Implement live fetch.
- Endpoint: https://sharaku.eorc.jaxa.jp/GSMaP/
- NRT FTP: ftp://hokusai.eorc.jaxa.jp/
- Requires free JAXA G-Portal / GSMaP user registration
- Product: hourly 0.1° global grids

Fusion role in Layer 3:
- Compare forecast rainfall to observed GSMaP over last 24-72h window
- If GSMaP confirms rainfall > forecast → amplifier boost (~1.15-1.25)
- If GSMaP diverges strongly from forecast → lower confidence flag
- If GSMaP and IMERG both agree with forecast → high confidence
"""

import logging
from typing import Any

from src.signals.base import RegionalSignal

logger = logging.getLogger(__name__)


class JAXAGSMaPSignal(RegionalSignal):
    """JAXA GSMaP near-real-time rainfall agreement signal. STUB in v1."""

    name = "jaxa_gsmap"

    def fetch(self, district_config: dict[str, Any]) -> float | None:
        """Fetch GSMaP-based confidence signal.

        STUB — returns None in v1.

        Args:
            district_config: District configuration dict.

        Returns:
            Float in [0.5, 1.0] representing confidence, or None if unavailable.
        """
        logger.info("JAXA GSMaP: STUB — not implemented in v1")
        return None
