"""Pytest fixtures and shared paths for the CTD_QARTOD test suite."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from qc_config import VARIABLE_MAPPING_JSON

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def minimal_profile_ds() -> xr.Dataset:
    """SFER-like single-profile cast: profile, z, temperature + lon/lat."""
    n = 5
    profile = xr.DataArray([0], dims=("profile",), name="profile")
    z = xr.DataArray(np.arange(n, dtype=int), dims=("z",), name="z")
    depth = xr.DataArray(
        np.arange(n, dtype=float).reshape(1, n) * 10.0,
        dims=("profile", "z"),
        coords={"profile": profile, "z": z},
        name="depth",
    )
    base = np.datetime64("2024-01-15T00:00:00")
    time = xr.DataArray([base], dims=("profile",), coords={"profile": profile}, name="time")
    temp = xr.DataArray(
        [[20.0, 20.1, 25.0, 20.2, 20.0]],
        dims=("profile", "z"),
        coords={"profile": profile, "z": z},
        name="sea_water_temperature",
        attrs={"units": "degree_C"},
    )
    lon = xr.DataArray([-80.1], dims=("profile",), coords={"profile": profile}, name="longitude")
    lat = xr.DataArray([25.6], dims=("profile",), coords={"profile": profile}, name="latitude")
    station = xr.DataArray([["1"] * n], dims=("profile", "z"), coords={"profile": profile, "z": z}, name="station")
    cruise = xr.DataArray(
        [["WS24139"] * n],
        dims=("profile", "z"),
        coords={"profile": profile, "z": z},
        name="cruiseID",
    )
    return xr.Dataset(
        {
            "profile": profile,
            "z": z,
            "depth": depth,
            "time": time,
            "sea_water_temperature": temp,
            "longitude": lon,
            "latitude": lat,
            "station": station,
            "cruiseID": cruise,
        }
    )


@pytest.fixture
def walton_mapping_path() -> Path:
    if not VARIABLE_MAPPING_JSON.exists():
        pytest.skip(f"missing {VARIABLE_MAPPING_JSON}")
    return VARIABLE_MAPPING_JSON


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running integration tests (full SFER dataset)")


@pytest.fixture
def sfer_data_root_path(repo_root: Path) -> Path:
    root = repo_root / "datasets" / "SFER_CTD_SOAK_REMOVED"
    if not root.is_dir() or not any(root.glob("*/*.nc")):
        pytest.skip("datasets/SFER_CTD_SOAK_REMOVED not available")
    return root
