"""
QC test implementations wrapping ioos_qc plus custom tests.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

import numpy as np
import xarray as xr
from ioos_qc import qartod
from ioos_qc.qartod import ClimatologyConfig

from qc_config import LOCATION_TOLERANCE, QC_FLAGS


def _to_int_flags(masked: np.ma.MaskedArray) -> np.ndarray:
    """
    Convert a masked array of flags to a plain numpy int array with MISSING(4) for masked.
    """
    return np.ma.filled(masked, QC_FLAGS["MISSING"]).astype(int)


def gap_test(data: xr.DataArray | np.ndarray) -> np.ndarray:
    """
    Mark everything as NOT_EVALUATED(2).
    """
    return np.full(np.asarray(data).shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)


def syntax_test(data: xr.DataArray | np.ndarray) -> np.ndarray:
    """
    Mark everything as NOT_EVALUATED(2).
    """
    return np.full(np.asarray(data).shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)


def location_qc(
    lon: Sequence[float],
    lat: Sequence[float],
    expected_lon: float,
    expected_lat: float,
    tolerance: float = LOCATION_TOLERANCE,
) -> np.ndarray:
    """
    PASS when both lon/lat are within tolerance of the expected location.
    """
    lon_arr = np.asarray(lon, dtype=float)
    lat_arr = np.asarray(lat, dtype=float)

    missing_mask = np.isnan(lon_arr) | np.isnan(lat_arr)
    within_lon = np.abs(lon_arr - expected_lon) <= tolerance
    within_lat = np.abs(lat_arr - expected_lat) <= tolerance

    flags = np.full(lon_arr.shape, QC_FLAGS["FAIL"], dtype=int)
    flags = np.where(within_lon & within_lat, QC_FLAGS["PASS"], flags)
    flags = np.where(missing_mask, QC_FLAGS["MISSING"], flags)
    return flags.astype(int)


def gross_range_qc(
    data: xr.DataArray | np.ndarray,
    fail_span: tuple[float, float],
    suspect_span: tuple[float, float] | None = None,
) -> np.ndarray:
    """
    Run the ioos_qc gross range test.
    """
    flags = qartod.gross_range_test(np.asarray(data), fail_span=fail_span, suspect_span=suspect_span)
    return _to_int_flags(flags)


def climatology_qc(
    data: xr.DataArray | np.ndarray,
    time: xr.DataArray | np.ndarray,
    depth: xr.DataArray | np.ndarray,
    config: Sequence[Mapping] | ClimatologyConfig,
) -> np.ndarray:
    """
    Run the ioos_qc climatology test.
    """
    if config is None:
        return np.full(np.asarray(data).shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)

    if isinstance(config, ClimatologyConfig):
        cfg = config
    else:
        cfg = ClimatologyConfig()
        for c in config:
            cfg.add(
                tspan=c.get("tspan"),
                vspan=c.get("vspan"),
                fspan=c.get("fspan"),
                zspan=c.get("zspan"),
                period=c.get("period"),
            )

    flags = qartod.climatology_test(config=cfg, inp=np.asarray(data), tinp=np.asarray(time), zinp=np.asarray(depth))
    return _to_int_flags(flags)


def decreasing_radiance_test(
    data: xr.DataArray | np.ndarray,
    depth: xr.DataArray | np.ndarray | None = None,
    non_increasing: bool = True,
) -> np.ndarray:
    """
    #ISSUE: some may have the testing range(stay in one range for a long time), may
    #need to determine this before doing the test

    Check that optical/bio-optical values decrease when depth increases,
    and increase when depth decreases.

    Args:
        data: Variable values
        depth: Corresponding depth values (must match data shape)

    Flags:
        PASS   when depth increases and value decreases (or vice versa)
        FAIL   when depth and value change in the same direction
        MISSING when data or depth is NaN
    """
    arr = np.asarray(data, dtype=float)
    if depth is None:
        return np.full(arr.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)

    depth_arr = np.asarray(depth, dtype=float)

    # Compute differences along the last axis
    value_diffs = np.diff(arr, axis=-1)
    depth_diffs = np.diff(depth_arr, axis=-1)

    # depth_diffs > 0 and value_diffs > 0 → FAIL (deeper but value increased)
    # depth_diffs < 0 and value_diffs < 0 → FAIL (shallower but value decreased)
    violations = (depth_diffs > 0) & (value_diffs > 0)
    violations |= (depth_diffs < 0) & (value_diffs < 0)

    # Build flags array
    flags = np.full(arr.shape, QC_FLAGS["PASS"], dtype=int)

    # Shift violations by one to align with the second point of each pair
    if violations.ndim == 1:
        flags[1:] = np.where(violations, QC_FLAGS["FAIL"], QC_FLAGS["PASS"])
    else:
        fail_mask = np.pad(violations, pad_width=[(0, 0)] * (violations.ndim - 1) + [(1, 0)], constant_values=False)
        flags = np.where(fail_mask, QC_FLAGS["FAIL"], flags)

    # Missing values (NaN in data or depth) -> MISSING
    missing_data = np.isnan(arr)
    missing_depth = np.isnan(depth_arr)
    flags = np.where(missing_data | missing_depth, QC_FLAGS["MISSING"], flags)

    return flags.astype(int)


