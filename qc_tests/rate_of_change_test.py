from __future__ import annotations

import numpy as np
import xarray as xr

from qc_config import QC_FLAGS


def rate_of_change_test(
    data: xr.DataArray | np.ndarray,
    threshold: float,
) -> np.ndarray:
    """
    Flag adjacent-sample changes above *threshold* along the last dimension.
    """
    limit = float(threshold)
    arr = np.asarray(data, dtype=np.float64)
    if not np.isfinite(limit) or limit <= 0:
        return np.full(arr.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)

    rows = arr.reshape(-1, arr.shape[-1])
    flags = np.full(rows.shape, QC_FLAGS["PASS"], dtype=int)
    flags[:, 0] = np.where(
        np.isfinite(rows[:, 0]),
        QC_FLAGS["NOT_EVALUATED"],
        QC_FLAGS["MISSING"],
    )
    if rows.shape[1] > 1:
        valid = np.isfinite(rows[:, :-1]) & np.isfinite(rows[:, 1:])
        flags[:, 1:] = np.where(valid, QC_FLAGS["PASS"], QC_FLAGS["MISSING"])
        flags[:, 1:] = np.where(
            valid & (np.abs(np.diff(rows, axis=1)) > limit),
            QC_FLAGS["SUSPECT"],
            flags[:, 1:],
        )
    return flags.reshape(arr.shape)
