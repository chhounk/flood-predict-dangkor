"""Tests for risk classification."""

import numpy as np

from src.config import load_thresholds
from src.risk.classify import _probability_to_level


def test_level_1_safe():
    """P < 0.10 must classify as L1 Safe."""
    levels = load_thresholds()["risk_levels"]
    assert _probability_to_level(0.0, levels) == 1
    assert _probability_to_level(0.05, levels) == 1
    assert _probability_to_level(0.099, levels) == 1


def test_level_2_low():
    """0.10 <= P < 0.40 must classify as L2 Low."""
    levels = load_thresholds()["risk_levels"]
    assert _probability_to_level(0.10, levels) == 2
    assert _probability_to_level(0.25, levels) == 2
    assert _probability_to_level(0.399, levels) == 2


def test_level_3_moderate():
    """0.40 <= P < 0.85 must classify as L3 Moderate."""
    levels = load_thresholds()["risk_levels"]
    assert _probability_to_level(0.40, levels) == 3
    assert _probability_to_level(0.60, levels) == 3
    assert _probability_to_level(0.849, levels) == 3


def test_level_4_high():
    """P >= 0.85 must classify as L4 High."""
    levels = load_thresholds()["risk_levels"]
    assert _probability_to_level(0.85, levels) == 4
    assert _probability_to_level(0.95, levels) == 4
    assert _probability_to_level(1.0, levels) == 4


def test_boundary_values():
    """Test exact boundary values between levels."""
    levels = load_thresholds()["risk_levels"]
    assert _probability_to_level(0.10, levels) == 2  # boundary L1/L2
    assert _probability_to_level(0.40, levels) == 3  # boundary L2/L3
    assert _probability_to_level(0.85, levels) == 4  # boundary L3/L4


def test_full_range():
    """All probabilities 0-1 must map to a valid level 1-4."""
    levels = load_thresholds()["risk_levels"]
    for p in np.arange(0, 1.01, 0.01):
        level = _probability_to_level(p, levels)
        assert level in (1, 2, 3, 4), f"Invalid level {level} for P={p}"
