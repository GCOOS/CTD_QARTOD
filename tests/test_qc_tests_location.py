from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS
from qc_tests import location_test


def test_location_test_pass_within_tolerance():
    lon = np.array([-80.1, -80.11])
    lat = np.array([25.6, 25.61])
    f = location_test(lon, lat, expected_lon=-80.1, expected_lat=25.6, tolerance=0.05)
    assert np.all(f == QC_FLAGS["PASS"])


def test_location_test_fail_outside():
    lon = np.array([-100.0])
    lat = np.array([25.6])
    f = location_test(lon, lat, expected_lon=-80.0, expected_lat=25.6, tolerance=0.01)
    assert f[0] == QC_FLAGS["FAIL"]


def test_location_test_missing_nan():
    lon = np.array([np.nan])
    lat = np.array([25.6])
    f = location_test(lon, lat, expected_lon=-80.0, expected_lat=25.6)
    assert f[0] == QC_FLAGS["MISSING"]
