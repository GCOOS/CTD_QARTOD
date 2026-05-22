"""Utilities for loading NetCDF data and identifying variables to QC."""

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import json
import xarray as xr

from dataset_profile import MetadataConfig, default_profile


def load_nc_file(path: Path | str) -> xr.Dataset:
    """
    Open a NetCDF file and return the xarray Dataset.

    Uses decode_cf=False 
    """
    return xr.open_dataset(
        Path(path),
        decode_cf=False,
        mask_and_scale=True,
    )


def load_mapping(json_path: Path | str) -> Dict[str, List[str]]:
    """
    Load the walton_mapping.json file into a dict of category -> variable names.
    """
    with open(Path(json_path), "r", encoding="utf-8") as f:
        return json.load(f)


def flatten_mapping(mapping: Dict[str, Iterable[str]]) -> Set[str]:
    """
    Flatten the mapping values into a set of variable names.
    """
    names: Set[str] = set()
    for _, var_list in mapping.items():
        names.update(var_list)
    return names


def get_qc_variables(ds: xr.Dataset, mapping: Dict[str, Iterable[str]]) -> List[str]:
    """
    Return variables present in both the dataset and the mapping.
    """
    mapping_vars = flatten_mapping(mapping)
    present = set(ds.variables.keys())
    return sorted(mapping_vars.intersection(present))


def get_variable_category(var_name: str, mapping: Dict[str, Iterable[str]]) -> Optional[str]:
    """
    Return the mapping category name that contains the variable, if any.
    """
    for category, vars_list in mapping.items():
        if var_name in vars_list:
            return category
    return None


def _metadata_or_default(metadata: MetadataConfig | None) -> MetadataConfig:
    return metadata or default_profile().metadata


def _first_value(value: object) -> object:
    if hasattr(value, "flat"):
        value = value.flat[0]
    if hasattr(value, "item"):
        value = value.item()
    return value


def get_scalar_var(ds: xr.Dataset, names: str | Iterable[str]) -> Optional[str]:
    """
    Return the first value from the first matching variable name.
    """
    candidates = (names,) if isinstance(names, str) else tuple(names)
    for name in candidates:
        if name not in ds:
            continue
        value = _first_value(ds[name].values)
        text = str(value).strip()
        return text or None
    return None


def get_lon_lat(
    ds: xr.Dataset,
    metadata: MetadataConfig | None = None,
) -> tuple[Optional[xr.DataArray], Optional[xr.DataArray]]:
    """
    Attempt to retrieve longitude and latitude arrays from configured variable names.
    """
    meta = _metadata_or_default(metadata)
    lon = None
    lat = None
    for candidate in meta.longitude:
        if candidate in ds:
            lon = ds[candidate]
            break
    for candidate in meta.latitude:
        if candidate in ds:
            lat = ds[candidate]
            break
    return lon, lat


def get_station_id(ds: xr.Dataset, metadata: MetadataConfig | None = None) -> Optional[str]:
    """
    Return the station ID from the configured variable inside the NetCDF file.

    The variable may be scalar or an array (all values identical).  We take
    the first element, strip whitespace, lower-case it and drop a trailing
    ".0" that some numeric stations acquire during conversion.
    """
    meta = _metadata_or_default(metadata)
    station_id = get_scalar_var(ds, meta.station)
    if not station_id:
        return None
    station_id = station_id.lower()
    if station_id.endswith(".0"):
        station_id = station_id[:-2]

    return station_id or None


def get_cruise_id(ds: xr.Dataset, metadata: MetadataConfig | None = None) -> Optional[str]:
    """
    Return the cruise ID from the configured variable inside the NetCDF file.

    Works analogously to :func:`get_station_id`.
    """
    meta = _metadata_or_default(metadata)
    return get_scalar_var(ds, meta.cruise_id)


def get_coord_for_var(
    ds: xr.Dataset,
    data_var: xr.DataArray,
    names: Iterable[str],
) -> Optional[xr.DataArray]:
    """
    Return a configured coordinate/variable for *data_var*, preferring coordinates.
    """
    for name in names:
        if name in data_var.coords:
            return data_var.coords[name]
        if name in ds:
            return ds[name]
    return None
