"""
NetCDF utility functions for SFER_CTD file handling.

Shared by sfer_ctd_remove_surface_soak.py and review_app.py.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

__all__ = [
    "safe_depth_1d",
    "guess_scan_dim",
    "sanitize_encodings_for_netcdf",
]


def safe_depth_1d(ds: xr.Dataset) -> np.ndarray:
    """
    Return depth as a 1D float array (NaNs removed).

    SFER_CTD NetCDFs store depth as a coordinate with dims (profile, z).
    We flatten to 1D to run the soak detection logic.
    """
    if "depth" not in ds.coords:
        raise KeyError("Dataset is missing required coordinate: 'depth'")
    depth = np.asarray(ds.coords["depth"].values).astype("float64", copy=False).ravel()
    depth = depth[~np.isnan(depth)]
    if depth.size == 0:
        raise ValueError("Depth coordinate contains only NaNs (no valid scans)")
    return depth


def guess_scan_dim(ds: xr.Dataset) -> str:
    """
    Determine which dimension corresponds to scan index (the one to slice).

    For SFER_CTD files this is typically 'z'. We keep this generic-ish in case
    of slight variation.
    """
    for cand in ("z", "scan", "obs", "time", "depth"):
        if cand in ds.dims:
            return cand
    # Fallback: pick the longest dim that isn't 'profile'
    dims = list(ds.dims)
    non_profile = [d for d in dims if d != "profile"]
    if not non_profile:
        raise ValueError(f"Cannot determine scan dimension from dims={dict(ds.dims)}")
    return max(non_profile, key=lambda d: int(ds.dims[d]))


def sanitize_encodings_for_netcdf(ds: xr.Dataset) -> xr.Dataset:
    """
    Make dataset encodings safe to write with xarray.to_netcdf().

    Some SFER_CTD files include both `missing_value` and `_FillValue` in
    per-variable encodings with conflicting values (e.g., `time` has
    `_FillValue=NaN` and `missing_value=-9.99e-29`). xarray refuses to encode
    such variables.

    Policy:
    - For object/datetime variables: drop both `_FillValue` and `missing_value`
    - For numeric variables: if both are present and conflict, drop `missing_value`
    """
    for name in ds.variables:
        var = ds[name]
        enc = var.encoding

        # Object-like variables (including string/object and datetime64)
        if var.dtype.kind in ("O", "M", "U", "S"):
            enc.pop("_FillValue", None)
            enc.pop("missing_value", None)
            continue

        fill = enc.get("_FillValue", None)
        missing = enc.get("missing_value", None)
        if ("_FillValue" in enc) and ("missing_value" in enc):
            # If they don't match, prefer _FillValue and drop missing_value
            try:
                conflict = not np.array_equal(fill, missing)
            except Exception:
                conflict = fill != missing
            if conflict:
                enc.pop("missing_value", None)

        # NaN fill values on non-float types can cause encoding issues
        if ("_FillValue" in enc) and isinstance(fill, float) and np.isnan(fill):
            if var.dtype.kind not in ("f",):  # not float
                enc.pop("_FillValue", None)

    return ds
