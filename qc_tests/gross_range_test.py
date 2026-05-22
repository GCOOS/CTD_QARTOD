from __future__ import annotations

import numpy as np
import xarray as xr
from ioos_qc import qartod

from .flags import to_int_flags


def gross_range_test(
    data: xr.DataArray | np.ndarray,
    fail_span: tuple[float, float],
    suspect_span: tuple[float, float] | None = None,
) -> np.ndarray:
    """Run the ioos_qc gross range test."""
    flags = qartod.gross_range_test(np.asarray(data), fail_span=fail_span, suspect_span=suspect_span)
    return to_int_flags(flags)
