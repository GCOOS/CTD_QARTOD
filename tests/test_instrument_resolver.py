from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from instrument_resolver import (
    extract_instruments,
    load_sensor_specs,
    resolve_gross_ranges,
)
from qc_config import SENSOR_SPECS_JSON, VARIABLE_SENSOR_MAP_JSON


def test_extract_instruments_empty():
    ds = xr.Dataset({"temperature": xr.DataArray([1.0])})
    assert extract_instruments(ds) == []


def test_extract_instruments_reads_long_name():
    ds = xr.Dataset(
        {
            "instrument": xr.DataArray(
                np.array("x", dtype=object),
                attrs={"long_name": "Temperature Sensor 1"},
            )
        }
    )
    inst = extract_instruments(ds)
    assert len(inst) == 1
    assert "Temperature Sensor 1" in inst[0]["long_name"]


def test_resolve_gross_ranges_with_project_configs():
    specs = SENSOR_SPECS_JSON
    vmap = VARIABLE_SENSOR_MAP_JSON
    if not specs.exists() or not vmap.exists():
        pytest.skip("config json missing")

    ds = xr.Dataset(
        {
            "instrument": xr.DataArray(
                np.array("x", dtype=object),
                attrs={"long_name": "Temperature Sensor 1"},
            ),
            "sea_water_temperature": xr.DataArray(
                np.array([20.0]), attrs={"units": "degree_C"}
            ),
        }
    )
    resolved = resolve_gross_ranges(ds, specs_path=specs, variable_map_path=vmap)
    assert "sea_water_temperature" in resolved
    assert "fail_span" in resolved["sea_water_temperature"]
    lo, hi = resolved["sea_water_temperature"]["fail_span"]
    assert lo < hi
