"""Tests for flow routing and accumulated runoff estimation."""

import numpy as np

from src.physics.routing import estimate_accumulated_runoff


def test_zero_runoff_zero_accumulation():
    """Zero runoff must produce zero accumulated volume."""
    runoff_mm = np.zeros((10, 5))
    flow_acc = np.ones(10)
    result = estimate_accumulated_runoff(runoff_mm, flow_acc)
    np.testing.assert_array_equal(result, 0.0)


def test_higher_flow_acc_more_volume():
    """Cells with higher flow accumulation should get more accumulated volume."""
    runoff_mm = np.full((3, 1), 10.0)  # 10mm uniform runoff
    flow_acc = np.array([1.0, 100.0, 10000.0])
    result = estimate_accumulated_runoff(runoff_mm, flow_acc)
    # Higher flow_acc → higher accumulated volume
    assert result[1, 0] > result[0, 0]
    assert result[2, 0] > result[1, 0]


def test_output_shape():
    """Output shape must match (n_cells, n_timesteps)."""
    runoff_mm = np.ones((50, 13))
    flow_acc = np.ones(50) * 10
    result = estimate_accumulated_runoff(runoff_mm, flow_acc)
    assert result.shape == (50, 13)


def test_volume_units():
    """Accumulated volume should be in m³.

    10mm over 62500m² cell = 10 * 62500 / 1000 = 625 m³ local volume.
    With flow_acc=1: factor = 1 + log(2) ≈ 1.69, so ~1059 m³.
    """
    runoff_mm = np.array([[10.0]])
    flow_acc = np.array([1.0])
    result = estimate_accumulated_runoff(runoff_mm, flow_acc, cell_area_m2=62500.0)
    local_vol = 10.0 * 62500.0 / 1000.0  # 625 m³
    assert result[0, 0] > local_vol, "Accumulated volume should exceed local volume"
    assert result[0, 0] < local_vol * 10, "Accumulated volume unreasonably high"
