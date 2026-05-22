from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import xarray as xr
from ioos_qc import qartod
from ioos_qc.qartod import ClimatologyConfig

from qc_config import QC_FLAGS
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

    flags = qartod.climatology_test(config=cfg, inp=np.asarray(data), tinp=np.asarray(time), zinp=np.asarray(depth))
    return to_int_flags(flags)
