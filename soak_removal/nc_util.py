"""
NetCDF utility functions for converted CTD profile files.

Shared by remove_surface_soak.py and review_app.py. Variable and dimension
names come from the dataset profile ``metadata`` section.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import xarray as xr

__all__ = [
    "ProfileLayout",
    "add_layout_arguments",
    "layout_from_args",
    "safe_depth_1d",
    "scan_dimension",
    "sanitize_encodings_for_netcdf",
]


@dataclass(frozen=True)
class ProfileLayout:
    """Depth variable names (tried in order) and the per-scan dimension."""

    depth_names: tuple[str, ...]
    sample_dimension: str


def add_layout_arguments(parser) -> None:
    parser.add_argument(
        "--depth-variable",
        action="append",
        required=True,
        help="Depth variable name to try, in order (repeatable; profile metadata.depth)",
    )
    parser.add_argument("--sample-dimension", required=True, help="Per-scan dimension (profile metadata.sample_dimension)")


def layout_from_args(args) -> ProfileLayout:
    return ProfileLayout(tuple(args.depth_variable), args.sample_dimension)


def safe_depth_1d(ds: xr.Dataset, depth_names: Sequence[str]) -> np.ndarray:
    """
    Return depth as a 1D float array (NaNs removed).

    Converted NetCDFs store depth with dims (profile, z); the first configured
    depth name present in the file is flattened to run the soak detection logic.
    """
    name = next((candidate for candidate in depth_names if candidate in ds.variables), None)
    if name is None:
        raise KeyError(f"Dataset has none of the configured depth variables: {', '.join(depth_names)}")
    depth = np.asarray(ds[name].values).astype("float64", copy=False).ravel()
    depth = depth[~np.isnan(depth)]
    if depth.size == 0:
        raise ValueError(f"Depth variable {name!r} contains only NaNs (no valid scans)")
    return depth


def scan_dimension(ds: xr.Dataset, sample_dimension: str) -> str:
    """Return the configured per-scan dimension (the one to slice)."""
    if sample_dimension not in ds.dims:
        raise ValueError(f"Sample dimension {sample_dimension!r} is not in dims={dict(ds.sizes)}")
    return sample_dimension


def sanitize_encodings_for_netcdf(ds: xr.Dataset) -> xr.Dataset:
    """
    Make dataset encodings safe to write with xarray.to_netcdf().

    Some converted files include both `missing_value` and `_FillValue` in
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
