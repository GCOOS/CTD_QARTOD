from __future__ import annotations

import numpy as np
import xarray as xr

from qc_config import QC_FLAGS


def _rate_of_change_count_1d(series: np.ndarray, threshold: float) -> np.ndarray:
    """
    Count-based step test on a 1-D series: compare |x[i] - x[i-1]| to *threshold*.

    Index 0 has no previous sample → NOT_EVALUATED (unless NaN → MISSING).
    SUSPECT when the step exceeds *threshold*; PASS otherwise; MISSING if
    either endpoint of the step is non-finite.
    """
    n = int(series.size)
    out = np.full(n, QC_FLAGS["PASS"], dtype=int)
    if n == 0:
        return out
    if not np.isfinite(series.flat[0]):
        out[0] = QC_FLAGS["MISSING"]
    else:
        out[0] = QC_FLAGS["NOT_EVALUATED"]
    if n == 1:
        return out
    prev = series[:-1]
    curr = series[1:]
    valid = np.isfinite(prev) & np.isfinite(curr)
    step = np.abs(curr - prev.astype(np.float64, copy=False))
    out[1:] = np.where(~valid, QC_FLAGS["MISSING"], QC_FLAGS["PASS"])
    out[1:] = np.where(valid & (step > threshold), QC_FLAGS["SUSPECT"], out[1:])
    return out


def rate_of_change_test(
    data: xr.DataArray | np.ndarray,
    threshold: float,
) -> np.ndarray:
    """
    Count-based rate-of-change: max |Δvalue| between consecutive samples along the
    last dimension (no clock). *threshold* is in the same units as *data* per
    adjacent sample (profile / cast order in the file).
    """
    th = float(threshold)
    if not np.isfinite(th) or th <= 0:
        return np.full(np.asarray(data).shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)

    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim == 1:
        return _rate_of_change_count_1d(arr, th)

    leading = int(np.prod(arr.shape[:-1]))
    n_last = arr.shape[-1]
    arr_2d = arr.reshape(leading, n_last)
    out_2d = np.empty_like(arr_2d, dtype=int)
    for row in range(leading):
        out_2d[row] = _rate_of_change_count_1d(arr_2d[row], th)
    return out_2d.reshape(arr.shape)
