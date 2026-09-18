from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import xarray as xr
from ioos_qc import qartod
from ioos_qc.qartod import ClimatologyConfig

from qc_config import QC_FLAGS
from qc_time import decode_qc_time
from .flags import to_int_flags


def climatology_test(
    data: xr.DataArray | np.ndarray,
    time: xr.DataArray | np.ndarray,
    depth: xr.DataArray | np.ndarray,
    config: Sequence[Mapping] | ClimatologyConfig,
) -> np.ndarray:
    """Run the ioos_qc climatology test."""
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

    time_values = np.asarray(decode_qc_time(time) if isinstance(time, xr.DataArray) else time)
    if not np.issubdtype(time_values.dtype, np.datetime64):
        raise ValueError("Climatology requires decoded dates, not numeric elapsed time without CF units")
    values, times, depths = np.broadcast_arrays(np.asarray(data), time_values, np.asarray(depth))
    valid_time = ~np.isnat(times)
    flags = np.full(values.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
    if np.any(valid_time):
        flags[valid_time] = to_int_flags(qartod.climatology_test(
            config=cfg, inp=values[valid_time], tinp=times[valid_time], zinp=depths[valid_time],
        ))
    flags[~np.isfinite(values)] = QC_FLAGS["MISSING"]
    return flags
