"""
Utilities for loading NetCDF data and identifying variables to QC.

Station and cruise identifiers are read from the ``station`` and ``cruiseID``
variables embedded in each NetCDF file (SFER_CTD dataset format).
"""

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import json
import xarray as xr


def load_nc_file(path: Path | str) -> xr.Dataset:
    """
    Open a NetCDF file and return the xarray Dataset.
    """
    return xr.open_dataset(Path(path))


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


def get_lon_lat(ds: xr.Dataset) -> tuple[Optional[xr.DataArray], Optional[xr.DataArray]]:
    """
    Attempt to retrieve longitude and latitude arrays from common variable names.
    """
    lon = None
    lat = None
    #should be lon and lat, try other name just in case
    for candidate in ("longitude", "lon", "LONGITUDE", "LON"):
        if candidate in ds:
            lon = ds[candidate]
            break
    for candidate in ("latitude", "lat", "LATITUDE", "LAT"):
        if candidate in ds:
            lat = ds[candidate]
            break
    return lon, lat


def get_station_id(ds: xr.Dataset) -> Optional[str]:
    """
    Return the station ID from the ``station`` variable inside the NetCDF file.

    The variable may be scalar or an array (all values identical).  We take
    the first element, strip whitespace, lower-case it and drop a trailing
    ".0" that some numeric stations acquire during conversion.
    """
    if "station" not in ds:
        return None

    station_value = ds["station"].values
    # Flatten to a single value (take the first element for array variables)
    if hasattr(station_value, "flat"):
        station_value = station_value.flat[0]
    if hasattr(station_value, "item"):
        station_value = station_value.item()

    station_id = str(station_value).strip().lower()
    if station_id.endswith(".0"):
        station_id = station_id[:-2]

    return station_id or None


def get_cruise_id(ds: xr.Dataset) -> Optional[str]:
    """
    Return the cruise ID from the ``cruiseID`` variable inside the NetCDF file.

    Works analogously to :func:`get_station_id`.
    """
    if "cruiseID" not in ds:
        return None

    cruise_value = ds["cruiseID"].values
    if hasattr(cruise_value, "flat"):
        cruise_value = cruise_value.flat[0]
    if hasattr(cruise_value, "item"):
        cruise_value = cruise_value.item()

    cruise_id = str(cruise_value).strip()
    return cruise_id or None
