from __future__ import annotations

import numpy as np
import xarray as xr
from ioos_qc import qartod

from .flags import to_int_flags


def spike_test(
    data: xr.DataArray | np.ndarray,
    suspect_threshold: float,
    fail_threshold: float,
) -> np.ndarray:
    """Run the QARTOD average / neighbor-midpoint spike test."""
    suspect = float(suspect_threshold)
    fail = float(fail_threshold)
    if suspect > fail:
        suspect, fail = fail, suspect
    flags = qartod.spike_test(
        np.asarray(data),
        suspect_threshold=suspect,
        fail_threshold=fail,
        method="average",
    )
    return to_int_flags(flags)
