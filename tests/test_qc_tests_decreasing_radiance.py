from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS
from qc_tests import decreasing_radiance_test


def test_decreasing_radiance_no_depth_not_evaluated():
    x = np.array([1.0, 2.0])
    f = decreasing_radiance_test(x, depth=None)
    assert np.all(f == QC_FLAGS["NOT_EVALUATED"])


def test_decreasing_radiance_monotonic_down_ok():
    """Deeper → lower radiance is PASS on interior points."""
    depth = np.array([0.0, 10.0, 20.0, 30.0])
    rad = np.array([100.0, 80.0, 60.0, 40.0])
    f = decreasing_radiance_test(rad, depth=depth)
    assert f[0] == QC_FLAGS["PASS"]
    assert np.all(f[1:] == QC_FLAGS["PASS"])


def test_decreasing_radiance_violation_fail():
    depth = np.array([0.0, 10.0, 20.0])
    rad = np.array([10.0, 20.0, 5.0])  # increases with depth at second point
    f = decreasing_radiance_test(rad, depth=depth)
    assert f[1] == QC_FLAGS["FAIL"]
