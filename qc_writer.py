"""
Helpers to write QC results back into NetCDF datasets.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import xarray as xr

from dataset_profile import DatasetProfile
from qc_config import QC_FLAGS

logger = logging.getLogger(__name__)

QC_TEST_METADATA = {
    "gap_test": ("gap", "gap_test_quality_flag", "Gap Test"),
    "syntax_test": ("syntax", "syntax_test_quality_flag", "Syntax Test"),
    "location_test": ("location", "location_test_quality_flag", "Location Test"),
    "gross_range_test": ("gross_range", "gross_range_test_quality_flag", "Gross Range Test"),
    "decreasing_radiance_test": (
        "decreasing_radiance_test",
        "decreasing_radiance_test_quality_flag",
        "Decreasing Radiance Test",
    ),
    "climatology_test": ("climatology", "climatology_test_quality_flag", "Climatology Test"),
    "flat_line_test": ("flat_line", "flat_line_test_quality_flag", "Flat Line Test"),
    "spike_test": ("spike", "spike_test_quality_flag", "Spike Test"),
    "rate_of_change_test": (
        "rate_of_change",
        "rate_of_change_test_quality_flag",
        "Rate Of Change Test",
    ),
}


def _flag_values() -> np.ndarray:
    return np.asarray(
        [QC_FLAGS[k] for k in ("PASS", "NOT_EVALUATED", "SUSPECT", "FAIL", "MISSING")],
        dtype=np.int8,
    )


def _display_name(data_var: xr.DataArray) -> str:
    raw_name = (
        str(data_var.attrs.get("long_name") or "").strip()
        or str(data_var.attrs.get("standard_name") or "").strip()
        or str(data_var.name or "").strip()
    )
    return raw_name.replace("_", " ").title()


def qc_variable_name(var_name: str, test_name: str) -> str:
    """Return the NetCDF variable name for a pipeline QC test."""
    if test_name not in QC_TEST_METADATA:
        raise KeyError(f"Unknown QC test metadata: {test_name}")
    suffix, _, _ = QC_TEST_METADATA[test_name]
    return f"{var_name}_qc_{suffix}"


def aggregate_qc_variable_name(var_name: str) -> str:
    """Return the NetCDF variable name for a pipeline aggregate QC flag."""
    return f"{var_name}_qc_agg"


def qc_standard_name(test_name: str) -> str:
    """Return the CF-style standard_name for a pipeline QC test."""
    if test_name == "aggregate":
        return "aggregate_quality_flag"
    if test_name not in QC_TEST_METADATA:
        raise KeyError(f"Unknown QC test metadata: {test_name}")
    return QC_TEST_METADATA[test_name][1]


def _qc_attrs(data_var: xr.DataArray, test_name: str) -> dict:
    """Standard attributes for a per-test QC variable."""
    _, standard_name, label = QC_TEST_METADATA[test_name]
    return {
        "long_name": f"{_display_name(data_var)} {label} Quality Flag",
        "standard_name": standard_name,
        "flag_values": _flag_values(),
        "flag_meanings": "PASS NOT_EVALUATED SUSPECT FAIL MISSING",
        "units": "1",
        "conventions": "IOOS_QC QARTOD",
    }


def _json_default(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot encode {type(value).__name__} as QC configuration JSON")


def write_qc_results(
    ds: xr.Dataset,
    var_name: str,
    test_name: str,
    flags: Iterable[int],
    *,
    applied_config: Mapping[str, object] | None = None,
    config_source: str = "built-in",
) -> xr.Dataset:
    """
    Add a per-test QC variable to the dataset.
    """
    if var_name not in ds:
        raise KeyError(f"Variable {var_name} not found in dataset")

    data_var = ds[var_name]
    flag_array = np.asarray(flags, dtype=np.int8)
    qc_name = qc_variable_name(var_name, test_name)
    
    # Calculate flag summary for logging
    unique, counts = np.unique(flag_array, return_counts=True)
    flag_names = {1: "pass", 2: "not_eval", 3: "suspect", 4: "fail", 9: "missing"}
    summary_parts = [f"{flag_names.get(u, str(u))}={c}" for u, c in zip(unique, counts)]
    logger.debug("    %s: %s", qc_name, ", ".join(summary_parts))

    attrs = _qc_attrs(data_var, test_name)
    attrs.update(
        {
            "ioos_qc_test": test_name,
            "ioos_qc_target": var_name,
            "ioos_qc_module": f"qc_tests.{test_name}",
            "ioos_qc_config": json.dumps(
                dict(applied_config or {}),
                default=_json_default,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "ioos_qc_config_source": config_source,
        }
    )
    ds[qc_name] = xr.DataArray(
        flag_array,
        coords=data_var.coords,
        dims=data_var.dims,
        attrs=attrs,
    )
    return ds


def aggregate_qc_flags(flags: Sequence[Iterable[int]], data: xr.DataArray | np.ndarray | None = None) -> np.ndarray:
    """
    Aggregate per-test QARTOD flags by severity.

    FAIL outranks SUSPECT, which outranks PASS. NOT_EVALUATED is used when all
    tests are not evaluated. MISSING is preserved when all tests are missing or
    the source data sample itself is missing.
    """
    if not flags:
        raise ValueError("Cannot aggregate an empty set of QC flags")

    stack = np.stack([np.asarray(arr, dtype=np.int8) for arr in flags], axis=0)
    agg = np.full(stack.shape[1:], QC_FLAGS["NOT_EVALUATED"], dtype=np.int8)

    any_pass = np.any(stack == QC_FLAGS["PASS"], axis=0)
    any_suspect = np.any(stack == QC_FLAGS["SUSPECT"], axis=0)
    any_fail = np.any(stack == QC_FLAGS["FAIL"], axis=0)
    all_missing = np.all(stack == QC_FLAGS["MISSING"], axis=0)

    agg[any_pass] = QC_FLAGS["PASS"]
    agg[any_suspect] = QC_FLAGS["SUSPECT"]
    agg[any_fail] = QC_FLAGS["FAIL"]
    agg[all_missing] = QC_FLAGS["MISSING"]

    if data is not None:
        values = np.asarray(data)
        try:
            missing_data = np.isnan(values)
        except TypeError:
            missing_data = np.zeros(values.shape, dtype=bool)
        agg[missing_data] = QC_FLAGS["MISSING"]

    return agg


def write_aggregate_qc_results(
    ds: xr.Dataset,
    var_name: str,
    flags: Iterable[int],
) -> xr.Dataset:
    """Add an aggregate QC variable with name {var_name}_qc_agg."""
    if var_name not in ds:
        raise KeyError(f"Variable {var_name} not found in dataset")

    data_var = ds[var_name]
    flag_array = np.asarray(flags, dtype=np.int8)
    qc_name = aggregate_qc_variable_name(var_name)
    ds[qc_name] = xr.DataArray(
        flag_array,
        coords=data_var.coords,
        dims=data_var.dims,
        attrs={
            "long_name": f"{_display_name(data_var)} Aggregate Quality Flag",
            "standard_name": qc_standard_name("aggregate"),
            "flag_values": _flag_values(),
            "flag_meanings": "PASS NOT_EVALUATED SUSPECT FAIL MISSING",
            "units": "1",
            "conventions": "IOOS_QC QARTOD",
        },
    )
    return ds


def set_ancillary_variables(ds: xr.Dataset, var_name: str, qc_names: Sequence[str]) -> xr.Dataset:
    """Point a data variable at the pipeline-generated QC variables."""
    if var_name not in ds:
        raise KeyError(f"Variable {var_name} not found in dataset")
    ds[var_name].attrs["ancillary_variables"] = " ".join(qc_names)
    return ds


def resolve_output_path(input_path: Path | str, profile: DatasetProfile, data_root: Path | str) -> Path:
    """
    Resolve where a QC'd NetCDF file should be written for the profile output mode.
    """
    path = Path(input_path)
    if profile.output.mode == "in_place":
        return path

    data_root_path = Path(data_root)
    try:
        rel_path = path.resolve().relative_to(data_root_path.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Input path is outside data root: {path} (root: {data_root_path})"
        ) from exc
    return profile.output.directory / rel_path


def _netcdf_encoding(ds: xr.Dataset) -> dict[str, dict]:
    """Build encoding so xarray does not re-encode CF time variables as datetimes."""
    encoding: dict[str, dict] = {}
    for name in ds.variables:
        var = ds[name]
        enc: dict = {}
        if np.issubdtype(var.dtype, np.floating):
            enc["dtype"] = "float64"
        elif np.issubdtype(var.dtype, np.integer):
            enc["dtype"] = str(var.dtype)
        encoding[name] = enc
    return encoding


def append_qc_history(
    ds: xr.Dataset,
    *,
    timestamp: str | None = None,
) -> xr.Dataset:
    """Append a UTC QC processing entry without replacing source history."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    entry = f"{timestamp}: QARTOD QC applied by CTD_QARTOD"
    existing = str(ds.attrs.get("history") or "").strip()
    ds.attrs["history"] = f"{existing}\n{entry}" if existing else entry
    return ds


def save_dataset(ds: xr.Dataset, path: Path | str) -> None:
    """
    Save dataset to NetCDF, ensuring the file handle is properly closed.
    Uses a temp file to avoid conflicts with open read handles.
    Cleans up conflicting fill value attributes before saving.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    
    # Write to temp file first (explicit encoding keeps time/time_elapsed numeric + units)
    ds_clean.to_netcdf(temp_path, mode="w", encoding=_netcdf_encoding(ds_clean))
    
    # Replace original file
    temp_path.replace(path)
    
    logger.debug("Saved %s with %d QC variables", path.name, len(qc_vars))
