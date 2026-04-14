"""Tests for SCS Curve Number runoff calculation."""

import numpy as np

from src.physics.runoff_scs import compute_runoff, get_amc_factor


def test_no_rain_no_runoff():
    """Zero rainfall must produce zero runoff."""
    rainfall = np.zeros((5, 3))
    cn = np.array([80, 85, 90, 75, 95], dtype=np.float64)
    runoff = compute_runoff(rainfall, cn)
    np.testing.assert_array_equal(runoff, 0.0)


def test_light_rain_below_ia():
    """Rainfall below initial abstraction (Ia) must produce zero runoff.

    For CN=80: S = (25400/80) - 254 = 63.5 mm; Ia = 0.2*S = 12.7 mm.
    So 10mm of rain should produce no runoff.
    """
    rainfall = np.array([[10.0, 10.0, 10.0]])  # mm
    cn = np.array([80.0])
    runoff = compute_runoff(rainfall, cn)
    np.testing.assert_array_equal(runoff, 0.0)


def test_heavy_rain_produces_runoff():
    """Heavy rainfall must produce positive runoff.

    For CN=90: S = (25400/90) - 254 = 28.2 mm; Ia = 5.6 mm.
    50mm rain: excess = 44.4mm, Q = 44.4^2 / (50 - 5.6 + 28.2) = 27.1mm
    """
    rainfall = np.array([[50.0]])  # mm
    cn = np.array([90.0])
    runoff = compute_runoff(rainfall, cn)
    assert runoff[0, 0] > 20.0, f"Expected >20mm runoff, got {runoff[0, 0]}"
    assert runoff[0, 0] < 50.0, "Runoff cannot exceed rainfall"


def test_higher_cn_more_runoff():
    """Higher CN (more impervious) must produce more runoff."""
    rainfall = np.array([[40.0, 40.0]])  # mm
    cn_low = np.array([60.0])
    cn_high = np.array([95.0])
    runoff_low = compute_runoff(rainfall, cn_low)
    runoff_high = compute_runoff(rainfall, cn_high)
    assert runoff_high[0, 0] > runoff_low[0, 0]


def test_runoff_never_exceeds_rainfall():
    """Runoff must never exceed rainfall (mass balance)."""
    rng = np.random.default_rng(42)
    rainfall = rng.uniform(0, 200, size=(100, 10))
    cn = rng.uniform(50, 98, size=100)
    runoff = compute_runoff(rainfall, cn)
    assert np.all(runoff <= rainfall + 1e-6), "Runoff exceeds rainfall"


def test_amc_dry():
    """Dry AMC (< 12.7mm in 5 days) should return factor 0.85."""
    amc_config = {
        "dry": {"threshold_mm": 12.7, "cn_factor": 0.85},
        "normal": {"threshold_mm": 38.1, "cn_factor": 1.0},
        "wet": {"threshold_mm": 999.0, "cn_factor": 1.15},
    }
    assert get_amc_factor(5.0, amc_config) == 0.85


def test_amc_wet():
    """Wet AMC (> 38.1mm in 5 days) should return factor 1.15."""
    amc_config = {
        "dry": {"threshold_mm": 12.7, "cn_factor": 0.85},
        "normal": {"threshold_mm": 38.1, "cn_factor": 1.0},
        "wet": {"threshold_mm": 999.0, "cn_factor": 1.15},
    }
    assert get_amc_factor(50.0, amc_config) == 1.15


def test_vectorized_shape():
    """Output shape must match input shape."""
    rainfall = np.ones((200, 15))
    cn = np.full(200, 85.0)
    runoff = compute_runoff(rainfall, cn)
    assert runoff.shape == (200, 15)
