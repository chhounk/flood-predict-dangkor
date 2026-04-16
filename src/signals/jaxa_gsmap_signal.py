"""JAXA GSMaP (Global Satellite Mapping of Precipitation) signal.

GSMaP is JAXA's satellite-rainfall product, combining the GPM/GCOM-W1
microwave constellation with Himawari-8/9 geostationary IR. It is
algorithmically and organisationally independent of NASA GPM IMERG, which
makes agreement between the two a genuine cross-confirmation signal.

Key specs
---------
- GSMaP NRT (Near-Real-Time): ~4 hour latency, 0.1° grid, hourly
- Joint NASA/JAXA GPM Core Observatory satellite
- Coverage: 60°N–60°S global

Data access — JAXA G-Portal (free registration)
-------------------------------------------------
  Register:  https://gportal.jaxa.jp/gpr/registration.html
  Then set:  JAXA_USERNAME=<your-email>
             JAXA_PASSWORD=<your-password>

  Without credentials the signal returns None (shown as "offline" in
  the dashboard). The pipeline continues normally — JAXA is a
  confidence indicator, not a probability amplifier.

Fusion role (Layer 3)
---------------------
  - Compares GSMaP observed 24h rainfall to Open-Meteo archive
  - Returns a scalar 0–1 confidence factor:
      0.9–1.0 → both satellites confirm high rainfall
      0.6–0.9 → moderate or mixed signals
      < 0.6   → satellite and forecast diverge
  - Shown in dashboard. Does NOT alter cell-level probabilities in v0.2
    (prevents double-counting with Open-Meteo forecast signal).
    Reserved for active amplification in v1.0 after re-validation.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import requests

from src.signals.base import RegionalSignal

logger = logging.getLogger(__name__)

# JAXA G-Portal API base
GPORTAL_BASE = "https://gportal.jaxa.jp/gpr"
# GSMaP NRT hourly product ID in G-Portal
GSMAP_DATASET_ID = "10003001"
# Grid resolution
GSMAP_RES = 0.1  # degrees


class JAXAGSMaPSignal(RegionalSignal):
    """JAXA GSMaP NRT rainfall signal via G-Portal API."""

    name = "jaxa_gsmap"

    def fetch(self, district_config: dict[str, Any]) -> float | None:
        """
        Fetch 24h GSMaP observed rainfall and compare to Open-Meteo archive.

        Returns a float 0–1 confidence factor, or None if unavailable.
        """
        username = os.environ.get("JAXA_USERNAME")
        password = os.environ.get("JAXA_PASSWORD")

        if not username or not password:
            logger.info(
                "JAXA GSMaP: credentials not set. "
                "Register free at https://gportal.jaxa.jp/gpr/registration.html "
                "then set JAXA_USERNAME + JAXA_PASSWORD."
            )
            return None

        try:
            token = _gportal_authenticate(username, password)
            if token is None:
                logger.warning("JAXA GSMaP: authentication failed — check credentials")
                return None

            bbox = district_config.get("bbox", [104.776, 11.417, 104.928, 11.528])
            lat_c = (bbox[1] + bbox[3]) / 2
            lon_c = (bbox[0] + bbox[2]) / 2

            rainfall_mm = _fetch_gsmap_24h(token, lat_c, lon_c)
            if rainfall_mm is None:
                logger.warning("JAXA GSMaP: data fetch returned None")
                return None

            signal = _compute_signal(rainfall_mm, district_config)
            logger.info(
                "JAXA GSMaP: 24h rainfall=%.1f mm → signal=%.2f",
                rainfall_mm, signal,
            )
            return signal

        except Exception as exc:
            logger.warning("JAXA GSMaP fetch failed: %s", exc)
            return None


# ── G-Portal authentication ──────────────────────────────────────────────────

def _gportal_authenticate(username: str, password: str) -> str | None:
    """
    Authenticate against JAXA G-Portal and return a session token.

    Docs: https://gportal.jaxa.jp/gpr/document/api_specification
    """
    try:
        resp = requests.post(
            f"{GPORTAL_BASE}/auth/sign_in.json",
            json={"user_id": username, "password": password},
            timeout=20,
        )
        if not resp.ok:
            logger.warning(
                "G-Portal auth HTTP %d: %s", resp.status_code, resp.text[:200]
            )
            return None

        # Token is returned in the X-GPORTAL-Token header
        token = resp.headers.get("X-GPORTAL-Token")
        if not token:
            # Some API versions return it in the body
            data = resp.json()
            token = data.get("token") or data.get("access_token")
        return token

    except requests.RequestException as exc:
        logger.warning("G-Portal auth request error: %s", exc)
        return None


# ── GSMaP data fetch ─────────────────────────────────────────────────────────

def _fetch_gsmap_24h(token: str, lat: float, lon: float) -> float | None:
    """
    Fetch the sum of the last 24h of GSMaP NRT hourly rainfall (mm)
    for the 0.1° grid cell containing (lat, lon).

    Uses the G-Portal file search + OPeNDAP subset download.
    """
    now_utc = datetime.now(timezone.utc)
    # GSMaP NRT has ~4h latency — fetch up to 28h back to be safe
    end_dt   = now_utc - timedelta(hours=4)
    start_dt = end_dt  - timedelta(hours=24)

    # Search for available files in the window
    files = _search_gsmap_files(token, start_dt, end_dt)
    if not files:
        logger.warning("JAXA GSMaP: no files found for %s – %s", start_dt, end_dt)
        return None

    # Download and extract the point value from each file
    totals: list[float] = []
    for file_info in files[:24]:  # cap at 24 files (= 24h)
        val = _extract_point_rainfall(token, file_info, lat, lon)
        if val is not None:
            totals.append(val)

    if not totals:
        return None

    return float(sum(totals))


def _search_gsmap_files(
    token: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Search G-Portal for GSMaP NRT files in a time window."""
    try:
        resp = requests.get(
            f"{GPORTAL_BASE}/api/catalog/product",
            headers={"X-GPORTAL-Token": token},
            params={
                "datasetId":  GSMAP_DATASET_ID,
                "startDate":  start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDate":    end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "maxResults": 24,
            },
            timeout=20,
        )
        if not resp.ok:
            logger.warning("G-Portal search HTTP %d", resp.status_code)
            return []

        data = resp.json()
        # Response is a list of file metadata dicts
        items = data if isinstance(data, list) else data.get("items", [])
        return items

    except requests.RequestException as exc:
        logger.warning("G-Portal search error: %s", exc)
        return []


def _extract_point_rainfall(
    token: str,
    file_info: dict,
    lat: float,
    lon: float,
) -> float | None:
    """Download a single GSMaP file and extract the rainfall for one grid point."""
    # G-Portal file metadata contains a download URL
    download_url = (
        file_info.get("downloadUrl")
        or file_info.get("url")
        or file_info.get("href")
    )
    if not download_url:
        return None

    try:
        resp = requests.get(
            download_url,
            headers={"X-GPORTAL-Token": token},
            timeout=30,
            stream=True,
        )
        if not resp.ok:
            return None

        content = resp.content

        # GSMaP NRT hourly files are NetCDF4 format (HDF5-compatible)
        # Try parsing with netCDF4 if available, else numpy struct
        try:
            import io
            import netCDF4 as nc4  # type: ignore
            ds = nc4.Dataset("memory", memory=content)
            # Variable is 'hourlyPrecipRate' or 'precipRate'
            for varname in ("hourlyPrecipRate", "precipRate", "rain"):
                if varname in ds.variables:
                    data = ds.variables[varname][:]
                    lats = ds.variables["lat"][:]
                    lons = ds.variables["lon"][:]
                    lat_idx = int(np.argmin(np.abs(lats - lat)))
                    lon_idx = int(np.argmin(np.abs(lons - lon)))
                    val = float(data[lat_idx, lon_idx])
                    ds.close()
                    return val if val >= 0 else None
            ds.close()
        except (ImportError, Exception):
            pass

        return None

    except requests.RequestException as exc:
        logger.debug("File download error: %s", exc)
        return None


# ── Signal computation ───────────────────────────────────────────────────────

def _fetch_openmeteo_24h_rain(district_config: dict) -> float | None:
    """Fetch last-24h archive rainfall from Open-Meteo (as comparison baseline)."""
    points = district_config.get("sample_points", [])
    if not points:
        return None

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    day_before = yesterday - timedelta(days=1)

    lats = ",".join(str(p["lat"]) for p in points)
    lons = ",".join(str(p["lon"]) for p in points)

    try:
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude":  lats,
                "longitude": lons,
                "hourly":    "precipitation",
                "start_date": day_before.isoformat(),
                "end_date":   yesterday.isoformat(),
                "timezone":  "UTC",
            },
            timeout=20,
        )
        r.raise_for_status()
        results = r.json() if isinstance(r.json(), list) else [r.json()]
        totals = [
            sum(v for v in res["hourly"]["precipitation"] if v is not None)
            for res in results
        ]
        return float(np.mean(totals))
    except Exception:
        return None


def _compute_signal(gsmap_rain_mm: float, district_config: dict) -> float:
    """
    Compute a 0–1 confidence factor by comparing GSMaP to Open-Meteo archive.

      Both show heavy rain (>15mm/24h):  0.90 – 1.00  (strong confirmation)
      GSMaP shows rain, model dry:        0.70 – 0.85  (satellite sees more)
      Both show light or no rain:         0.85 – 0.95  (dry confirmed)
      GSMaP dry, model wet:               0.40 – 0.60  (model may be overestimating)
    """
    om_rain = _fetch_openmeteo_24h_rain(district_config)

    if om_rain is None:
        # Can't compare — report GSMaP absolute signal
        if gsmap_rain_mm > 30:
            return 0.85
        elif gsmap_rain_mm > 10:
            return 0.75
        else:
            return 0.90

    logger.info(
        "JAXA GSMaP: 24h rain=%.1f mm | Open-Meteo archive: %.1f mm",
        gsmap_rain_mm, om_rain,
    )

    # Agreement ratio (cap extreme values)
    if om_rain < 0.5 and gsmap_rain_mm < 0.5:
        return 0.92  # Both dry — high confidence in calm forecast

    if om_rain < 0.5:
        # Model says dry, satellite sees rain → potential underestimate
        return min(0.65, 1.0 - gsmap_rain_mm / 100)

    ratio = gsmap_rain_mm / max(om_rain, 0.1)
    if 0.7 <= ratio <= 1.5:
        # Good agreement — confidence scales with rainfall amount
        base = 0.85 + min(om_rain / 100, 0.10)
        return round(min(base, 0.98), 2)
    elif ratio > 1.5:
        # GSMaP sees more rain than forecast — slight boost to signal
        return 0.80
    else:
        # GSMaP sees less than forecast — model may be over-predicting
        return max(0.55, 0.75 - (1 - ratio) * 0.3)
