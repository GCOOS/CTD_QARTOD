from __future__ import annotations

import numpy as np
import xarray as xr

from qc_config import QC_FLAGS


def gap_test(data: xr.DataArray | np.ndarray) -> np.ndarray:
    """Mark everything as NOT_EVALUATED(2)."""
    return np.full(np.asarray(data).shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
