from __future__ import annotations

from typing import Sequence

import numpy as np

from qc_config import QC_FLAGS

DEFAULT_LOCATION_TOLERANCE = 0.01


def location_test(
    lon: Sequence[float],
    lat: Sequence[float],
    expected_lon: float,
    expected_lat: float,
    tolerance: float = DEFAULT_LOCATION_TOLERANCE,
) -> np.ndarray:
    """PASS when both lon/lat are within tolerance of the expected location."""
    lon_arr = np.asarray(lon, dtype=float)
    lat_arr = np.asarray(lat, dtype=float)

    missing_mask = np.isnan(lon_arr) | np.isnan(lat_arr)
    within_lon = np.abs(lon_arr - expected_lon) <= tolerance
    within_lat = np.abs(lat_arr - expected_lat) <= tolerance

    flags = np.full(lon_arr.shape, QC_FLAGS["FAIL"], dtype=int)
    flags = np.where(within_lon & within_lat, QC_FLAGS["PASS"], flags)
    flags = np.where(missing_mask, QC_FLAGS["MISSING"], flags)
    return flags.astype(int)
