from __future__ import annotations

import numpy as np
import xarray as xr
from ioos_qc import qartod
from qc_config import QC_FLAGS

from .flags import to_int_flags


def spike_test(
    data: xr.DataArray | np.ndarray,
    suspect_threshold: float,
    fail_threshold: float,
) -> np.ndarray:
    """Mark everything as NOT_EVALUATED(2)."""
    return np.full(np.asarray(data).shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)


# def spike_test(
#     data: xr.DataArray | np.ndarray,
#     suspect_threshold: float,
#     fail_threshold: float,
# ) -> np.ndarray:
#     """Run the ioos_qc spike test (QARTOD average / neighbor-midpoint method)."""
#     s = float(suspect_threshold)
#     f = float(fail_threshold)
#     if s > f:
#         s, f = f, s
#     flags = qartod.spike_test(
#         np.asarray(data),
#         suspect_threshold=s,
#         fail_threshold=f,
#         method="average",
#     )
#     return to_int_flags(flags)
