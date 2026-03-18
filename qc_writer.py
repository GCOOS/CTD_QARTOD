"""
Helpers to write QC results back into NetCDF datasets.
"""

import logging
from pathlib import Path
from typing import Iterable

import numpy as np
import xarray as xr

from qc_config import QC_FLAGS

logger = logging.getLogger(__name__)


def _qc_attrs(test_name: str) -> dict:
    """
    Standard QC variable attributes.
    """
    flag_values = [QC_FLAGS[k] for k in ("PASS", "NOT_EVALUATED", "SUSPECT", "FAIL", "MISSING")]
    flag_meanings = "pass not_evaluated suspect fail missing"
    return {
        "long_name": f"QC results for {test_name}",
        "standard_name": "status_flag",
        "flag_values": flag_values,
        "flag_meanings": flag_meanings,
        "conventions": "IOOS_QC QARTOD",
    }


def write_qc_results(ds: xr.Dataset, var_name: str, test_name: str, flags: Iterable[int]) -> xr.Dataset:
    """
    Add a QC variable to the dataset with name {var_name}_qc_{test_name}.
    """
    if var_name not in ds:
        raise KeyError(f"Variable {var_name} not found in dataset")

    data_var = ds[var_name]
    flag_array = np.asarray(flags, dtype=int)
    qc_name = f"{var_name}_qc_{test_name}"
    
    # Calculate flag summary for logging
    unique, counts = np.unique(flag_array, return_counts=True)
    flag_names = {1: "pass", 2: "not_eval", 3: "suspect", 4: "fail", 9: "missing"}
    summary_parts = [f"{flag_names.get(u, str(u))}={c}" for u, c in zip(unique, counts)]
    logger.debug("    %s: %s", qc_name, ", ".join(summary_parts))

    ds[qc_name] = xr.DataArray(
        flag_array,
        coords=data_var.coords,
        dims=data_var.dims,
        attrs=_qc_attrs(test_name),
    )
    return ds


def save_dataset(ds: xr.Dataset, path: Path | str) -> None:
    """
    Save dataset to NetCDF, ensuring the file handle is properly closed.
    Uses a temp file to avoid conflicts with open read handles.
    Cleans up conflicting fill value attributes before saving.
    """
    path = Path(path)
    logger.debug("Saving dataset to %s", path.name)
    
    # Use .qctmp extension to avoid glob("*.nc") picking up temp files
    temp_path = path.with_name(path.stem + ".qctmp")
    
    # Load data fully into memory and close file handles
    ds = ds.load()
    
    # Count QC variables being added
    qc_vars = [v for v in ds.variables if "_qc_" in v]
    
    # Clean up conflicting fill value attributes by creating new variables
    data_vars = {}
    coords = {}
    
    for var_name in ds.variables:
        var = ds[var_name]
        attrs = dict(var.attrs)  # Make a copy
        
        fill_val = attrs.get("_FillValue")
        missing_val = attrs.get("missing_value")
        
        # If both exist and differ, keep only _FillValue
        if fill_val is not None and missing_val is not None:
            try:
                if float(fill_val) != float(missing_val):
                    attrs.pop("missing_value", None)
            except (ValueError, TypeError):
                # If comparison fails, drop missing_value to be safe
                attrs.pop("missing_value", None)
        
        # Create new DataArray with cleaned attributes
        new_var = xr.DataArray(
            var.values,
            dims=var.dims,
            attrs=attrs,
            name=var_name
        )
        
        if var_name in ds.coords:
            coords[var_name] = new_var
        else:
            data_vars[var_name] = new_var
    
    # Create new clean dataset
    ds_clean = xr.Dataset(data_vars, coords=coords, attrs=ds.attrs)
    
    # Write to temp file first
    ds_clean.to_netcdf(temp_path, mode='w')
    
    # Replace original file
    temp_path.replace(path)
    
    logger.debug("Saved %s with %d QC variables", path.name, len(qc_vars))


