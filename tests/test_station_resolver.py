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
    assert normalize_station_coord(" 57_2 ") == "57.2"


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


@pytest.mark.parametrize(
    ("station", "reference"),
    [
        ("21LK", "21LK"),
        ("21_5", "21.5"),
        ("57_1", "57.1"),
        ("57_2", "57.2"),
        ("57_3", "57.3"),
        ("9_5", "9.5"),
        ("MR-2", "MR-2"),
        ("54-2", "54-2"),
    ],
)
def test_stored_station_matches_named_reference(tmp_path, station, reference):
    csv = tmp_path / "coords.csv"
    csv.write_text(
        f"station,lat_mean,lon_mean\n{reference},24.5,-81.4\n", encoding="utf-8"
    )
    assert resolve_coords_by_station_id(station, station_csv=csv) == (24.5, -81.4)


def test_source_aliases_are_not_applied_during_qc(tmp_path):
    csv = tmp_path / "coords.csv"
    csv.write_text("station,lat_mean,lon_mean\n21LK,24.5,-81.4\n", encoding="utf-8")
    assert resolve_coords_by_station_id("21", station_csv=csv) is None
