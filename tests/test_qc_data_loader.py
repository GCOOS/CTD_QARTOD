from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr

from dataset_profile import MetadataConfig
from qc_data_loader import (
    get_coord_for_var,
    get_cruise_id,
    get_lon_lat,
    get_qc_variables,
    get_scalar_var,
    get_station_id,
    get_variable_category,
    load_mapping,
)


def test_get_station_id_scalar_string():
    ds = xr.Dataset({"station": xr.DataArray("WS99")})
    assert get_station_id(ds) == "ws99"


def test_get_station_id_strips_trailing_dot_zero():
    ds = xr.Dataset({"station": xr.DataArray("42.0")})
    assert get_station_id(ds) == "42"


def test_get_cruise_id():
    ds = xr.Dataset({"cruiseID": xr.DataArray("CRUISE_1")})
    assert get_cruise_id(ds) == "CRUISE_1"


def test_get_lon_lat_prefers_longitude():
    ds = xr.Dataset({"longitude": xr.DataArray(-80.0), "latitude": xr.DataArray(25.0)})
    lon, lat = get_lon_lat(ds)
    assert float(lon) == -80.0
    assert float(lat) == 25.0


def test_metadata_config_custom_names():
    metadata = MetadataConfig(
        station="station_name",
        cruise_id="deployment",
        longitude=("x",),
        latitude=("y",),
    )
    ds = xr.Dataset(
        {
            "station_name": xr.DataArray(["7.0"], dims=("z",)),
            "deployment": xr.DataArray("CRUISE_2"),
            "x": xr.DataArray(-81.0),
            "y": xr.DataArray(26.0),
        }
    )
    assert get_station_id(ds, metadata) == "7"
    assert get_cruise_id(ds, metadata) == "CRUISE_2"
    lon, lat = get_lon_lat(ds, metadata)
    assert float(lon) == -81.0
    assert float(lat) == 26.0


def test_get_scalar_var_returns_first_array_value():
    ds = xr.Dataset({"station": xr.DataArray([["A", "B"]], dims=("profile", "z"))})
    assert get_scalar_var(ds, "station") == "A"


def test_get_coord_for_var_prefers_data_var_coord():
    depth_coord = xr.DataArray([1.0, 2.0], dims=("z",), name="depth")
    data = xr.DataArray([10.0, 11.0], dims=("z",), coords={"depth": depth_coord})
    ds = xr.Dataset({"temperature": data})
    out = get_coord_for_var(ds, ds["temperature"], ("depth",))
    assert out is not None
    assert np.array_equal(out.values, depth_coord.values)


def test_get_qc_variables_intersection(walton_mapping_path: Path):
    mapping = load_mapping(walton_mapping_path)
    ds = xr.Dataset({"sea_water_temperature": xr.DataArray([1.0]), "unknown": xr.DataArray([2.0])})
    v = get_qc_variables(ds, mapping)
    assert "sea_water_temperature" in v
    assert "unknown" not in v


def test_get_variable_category(walton_mapping_path: Path):
    mapping = load_mapping(walton_mapping_path)
    cat = get_variable_category("sea_water_temperature", mapping)
    assert cat == "temperature"
