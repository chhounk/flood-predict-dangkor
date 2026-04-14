"""Sentinel-1 SAR flood extent comparison — STUB.

TODO: Implement Sentinel-1 SAR-based flood detection for validation.

Implementation plan:
1. Query Copernicus Open Access Hub for Sentinel-1 GRD IW scenes
   covering Dangkor District within the validation period.
   - API: https://scihub.copernicus.eu/dhus/
   - Free account required (Copernicus Open Access Hub)

2. Download VV-polarization backscatter for pre-flood and during-flood dates.

3. Apply change detection:
   - Compute ratio: during_flood_VV / pre_flood_VV
   - Threshold ratio < -3 dB → classified as flooded
   - This is the standard approach (Twele et al., 2016)

4. Resample SAR flood extent to the 250m grid.

5. Compare against predicted flood extent:
   - For each grid cell, compare predicted level vs observed (flooded/not flooded)
   - Compute confusion matrix and standard verification metrics:
     * POD (Probability of Detection) = TP / (TP + FN)
     * FAR (False Alarm Ratio) = FP / (TP + FP)
     * CSI (Critical Success Index) = TP / (TP + FP + FN)
     * Frequency Bias = (TP + FP) / (TP + FN)

6. Alternative data sources to consider:
   - Sentinel-1 via Google Earth Engine (free tier)
   - UNOSAT flood maps (when available for Cambodia events)
   - Copernicus Emergency Management Service activations

Dependencies needed (not installed in v1):
   - sentinelsat (for querying Copernicus Hub)
   - snappy or SNAP GPT (for SAR processing)
   - OR: use Google Earth Engine Python API
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def fetch_sentinel1_flood_extent(
    bbox: list[float],
    start_date: str,
    end_date: str,
) -> dict[str, Any] | None:
    """Fetch and process Sentinel-1 SAR data for flood detection.

    STUB — returns None in v1.

    Args:
        bbox: [west, south, east, north] bounding box.
        start_date: ISO date string for period start.
        end_date: ISO date string for period end.

    Returns:
        None in v1. Future: dict with flood extent grid and metadata.
    """
    logger.info(
        "Sentinel-1 flood detection: STUB — not implemented in v1 "
        "(bbox=%s, period=%s to %s)",
        bbox, start_date, end_date,
    )
    return None
