from __future__ import annotations

import numpy as np
import xarray as xr

from qc_config import QC_FLAGS


def flat_line_test(
    data: xr.DataArray | np.ndarray,
    rep_cnt_suspect: int = 3,
    rep_cnt_fail: int = 5,
    eps: float = 0.05,
) -> np.ndarray:
    """
    QARTOD Flat Line Test (count-based).

    A point n is flagged when the current value is equal (within eps) to a
    configured number of immediately preceding observations:
      - FAIL(4): equal to previous rep_cnt_fail observations
      - SUSPECT(3): equal to previous rep_cnt_suspect observations
      - PASS(1): otherwise
      - MISSING(9): NaN at current point
    """
    arr = np.asarray(data, dtype=float)
    flags = np.full(arr.shape, QC_FLAGS["PASS"], dtype=int)

    if rep_cnt_suspect < 1 or rep_cnt_fail < 1:
        raise ValueError("rep_cnt_suspect and rep_cnt_fail must be >= 1")
    if rep_cnt_fail < rep_cnt_suspect:
        raise ValueError("rep_cnt_fail must be >= rep_cnt_suspect")
    if eps < 0:
        raise ValueError("eps must be >= 0")

    if arr.ndim == 1:
        arr_2d = arr[np.newaxis, :]
        flags_2d = flags[np.newaxis, :]
    else:
        leading = int(np.prod(arr.shape[:-1]))
        arr_2d = arr.reshape(leading, arr.shape[-1])
        flags_2d = flags.reshape(leading, flags.shape[-1])

    n_samples = arr_2d.shape[1]

    for row in range(arr_2d.shape[0]):
        series = arr_2d[row]
        out = flags_2d[row]
        for n in range(n_samples):
            curr = series[n]
            if np.isnan(curr):
                out[n] = QC_FLAGS["MISSING"]
                continue

            fail = False
            if n >= rep_cnt_fail:
                prev = series[n - rep_cnt_fail : n]
                fail = not np.isnan(prev).any() and np.all(np.abs(curr - prev) < eps)
            if fail:
                out[n] = QC_FLAGS["FAIL"]
                continue

            suspect = False
            if n >= rep_cnt_suspect:
                prev = series[n - rep_cnt_suspect : n]
                suspect = not np.isnan(prev).any() and np.all(np.abs(curr - prev) < eps)
            if suspect:
                out[n] = QC_FLAGS["SUSPECT"]

    return flags.astype(int)
