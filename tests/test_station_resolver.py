from __future__ import annotations

import pytest

from station_resolver import load_station_coords, normalize_station_coord, resolve_coords_by_station_id


@pytest.fixture(autouse=True)
def clear_station_cache():
    load_station_coords.cache_clear()
    yield
    load_station_coords.cache_clear()


def test_normalize_station_coord():
    assert normalize_station_coord("  AbC.0 ") == "abc"


def test_resolve_coords_from_csv(tmp_path):
    csv = tmp_path / "coords.csv"
    csv.write_text("station,lat_mean,lon_mean\n1,25.5,-80.2\n", encoding="utf-8")
    out = resolve_coords_by_station_id("1", station_csv=csv)
    assert out is not None
    lat, lon = out
    assert abs(lat - 25.5) < 1e-9
    assert abs(lon + 80.2) < 1e-9


def test_resolve_coords_none_station():
    assert resolve_coords_by_station_id(None, station_csv=__file__) is None
