from __future__ import annotations

import numpy as np
import xarray as xr

from qc_config import QC_FLAGS


def decreasing_radiance_test(
    data: xr.DataArray | np.ndarray,
    depth: xr.DataArray | np.ndarray | None = None,
) -> np.ndarray:
    """
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

    value_diffs = np.diff(arr, axis=-1)
    depth_diffs = np.diff(depth_arr, axis=-1)

    violations = (depth_diffs > 0) & (value_diffs > 0)
    violations |= (depth_diffs < 0) & (value_diffs < 0)

    flags = np.full(arr.shape, QC_FLAGS["PASS"], dtype=int)

    if violations.ndim == 1:
        flags[1:] = np.where(violations, QC_FLAGS["FAIL"], QC_FLAGS["PASS"])
    else:
        fail_mask = np.pad(violations, pad_width=[(0, 0)] * (violations.ndim - 1) + [(1, 0)], constant_values=False)
        flags = np.where(fail_mask, QC_FLAGS["FAIL"], flags)

    missing_data = np.isnan(arr)
    missing_depth = np.isnan(depth_arr)
    flags = np.where(missing_data | missing_depth, QC_FLAGS["MISSING"], flags)

    return flags.astype(int)
