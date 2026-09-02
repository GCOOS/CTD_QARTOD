from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from dataset_profile import OutputConfig, default_profile
from qc_config import (
    QC_FLAGS,
    RATE_OF_CHANGE_THRESHOLDS_JSON,
    SPIKE_THRESHOLDS_JSON,
    STATION_COORDS_CSV,
    load_rate_of_change_thresholds,
    load_spike_thresholds,
)
from qc_runner import _run_common_tests, run_qc_for_all, run_qc_for_directory
from qc_runner import run_qc_for_file, run_single_test
from station_resolver import load_station_coords, resolve_coords_by_station_id


@pytest.fixture(autouse=True)
def clear_station_cache():
    load_station_coords.cache_clear()
    yield
    load_station_coords.cache_clear()


def _profile(data_root: Path, output_root: Path, **path_overrides):
    profile = default_profile()
    paths = replace(profile.paths, **path_overrides) if path_overrides else profile.paths
    return replace(
        profile,
        data_root=data_root,
        output=OutputConfig(mode="duplicate", directory=output_root),
        paths=paths,
    )


def test_run_common_tests_writes_expected_qc_vars(minimal_profile_ds):
    if not STATION_COORDS_CSV.exists():
        pytest.skip("Station_Mean_Coords.csv missing")

    spike_thr = load_spike_thresholds(SPIKE_THRESHOLDS_JSON)
    roc_thr = load_rate_of_change_thresholds(RATE_OF_CHANGE_THRESHOLDS_JSON)

    expected = resolve_coords_by_station_id("1", station_csv=STATION_COORDS_CSV)
    if expected is None:
        pytest.skip("station 1 not in coords csv")

    ds = minimal_profile_ds.copy()
    gross = {
        "sea_water_temperature": {
            "fail_span": (-2.0, 40.0),
            "suspect_span": (0.0, 35.0),
        },
    }
    out = _run_common_tests(
        ds,
        "sea_water_temperature",
        "temperature",
        gross_ranges=gross,
        climatology_config=None,
        expected_location=expected,
        location_tolerance=0.05,
        spike_thresholds=spike_thr,
        rate_of_change_thresholds=roc_thr,
    )
    for suffix in (
        "gap",
        "syntax",
        "location",
        "gross_range",
        "climatology",
        "flat_line",
        "spike",
        "rate_of_change",
    ):
        assert f"sea_water_temperature_qc_{suffix}" in out.variables
    assert "sea_water_temperature_qc_agg" in out.variables
    ancillary = out["sea_water_temperature"].attrs["ancillary_variables"]
    assert ancillary.startswith("sea_water_temperature_qc_agg ")
    assert "sea_water_temperature_qc_spike" in ancillary
    assert "sea_water_temperature_qc " not in f"{ancillary} "


def test_run_qc_for_file_duplicate_output_preserves_input(minimal_profile_ds, tmp_path: Path):
    data_root = tmp_path / "data"
    cruise_dir = data_root / "CRUISE"
    cruise_dir.mkdir(parents=True)
    nc_path = cruise_dir / "cast.nc"
    minimal_profile_ds.to_netcdf(nc_path)

    profile = _profile(data_root, tmp_path / "output")

    run_qc_for_file(nc_path, profile=profile, data_root=data_root)

    output_path = tmp_path / "output" / "CRUISE" / "cast.nc"
    assert output_path.exists()

    with xr.open_dataset(nc_path) as original:
        assert "sea_water_temperature_qc_gap" not in original.variables
    with xr.open_dataset(output_path) as out:
        assert "sea_water_temperature_qc_gap" in out.variables
        assert "sea_water_temperature_qc_agg" in out.variables


def test_run_single_test_uses_location_json_tolerance(minimal_profile_ds, tmp_path: Path):
    data_root = tmp_path / "data"
    cruise_dir = data_root / "CRUISE"
    cruise_dir.mkdir(parents=True)
    nc_path = cruise_dir / "cast.nc"
    minimal_profile_ds.to_netcdf(nc_path)

    station_csv = tmp_path / "Station_Mean_Coords.csv"
    station_csv.write_text(
        "station,lat_mean,lon_mean\n1,25.6,-80.115\n",
        encoding="utf-8",
    )
    location_config = tmp_path / "location_config.json"
    location_config.write_text('{"tolerance": 0.02}', encoding="utf-8")
    profile = _profile(
        data_root,
        tmp_path / "output",
        station_coords=station_csv,
        location_config=location_config,
    )

    results = run_single_test(
        nc_path,
        "location_test",
        variable="sea_water_temperature",
        print_summary=False,
        profile=profile,
    )

    assert len(results) == 1
    assert set(np.ravel(results[0].flags)) == {QC_FLAGS["PASS"]}


def test_run_qc_for_file_uses_flat_line_json_config(minimal_profile_ds, tmp_path: Path):
    data_root = tmp_path / "data"
    cruise_dir = data_root / "CRUISE"
    cruise_dir.mkdir(parents=True)
    nc_path = cruise_dir / "cast.nc"
    flat_path = tmp_path / "flat_line_config.json"
    flat_path.write_text(
        '{"rep_cnt_suspect": 2, "rep_cnt_fail": 3, "eps": 0.0}',
        encoding="utf-8",
    )

    z = np.arange(7, dtype=int)
    profile_coord = [0]
    ds = xr.Dataset(
        {
            "profile": xr.DataArray(profile_coord, dims=("profile",)),
            "z": xr.DataArray(z, dims=("z",)),
            "sea_water_temperature": xr.DataArray(
                [[5.0, 5.0, 5.0, 5.0, 5.0001, 5.0001, 5.0001]],
                dims=("profile", "z"),
                coords={"profile": profile_coord, "z": z},
            ),
            "depth": xr.DataArray(
                np.arange(7, dtype=float).reshape(1, 7),
                dims=("profile", "z"),
                coords={"profile": profile_coord, "z": z},
            ),
            "time": xr.DataArray([np.datetime64("2024-01-15T00:00:00")], dims=("profile",)),
            "longitude": xr.DataArray([-80.1], dims=("profile",)),
            "latitude": xr.DataArray([25.6], dims=("profile",)),
            "station": xr.DataArray([["1"] * 7], dims=("profile", "z"), coords={"profile": profile_coord, "z": z}),
            "cruiseID": xr.DataArray([["CRUISE"] * 7], dims=("profile", "z"), coords={"profile": profile_coord, "z": z}),
        }
    )
    ds.to_netcdf(nc_path)

    profile = _profile(
        data_root, tmp_path / "output", flat_line_config=flat_path
    )
    run_qc_for_file(nc_path, profile=profile, data_root=data_root)

    output_path = tmp_path / "output" / "CRUISE" / "cast.nc"
    with xr.open_dataset(output_path, decode_cf=False) as out:
        expected = np.asarray(
            [[
                QC_FLAGS["PASS"],
                QC_FLAGS["PASS"],
                QC_FLAGS["SUSPECT"],
                QC_FLAGS["FAIL"],
                QC_FLAGS["PASS"],
                QC_FLAGS["PASS"],
                QC_FLAGS["SUSPECT"],
            ]],
            dtype=np.int8,
        )
        assert np.array_equal(out["sea_water_temperature_qc_flat_line"].values, expected)


def test_run_qc_preserves_time_units(tmp_path: Path):
    """QC save must not CF-re-encode time or time_elapsed (ERDDAP regression)."""
    data_root = tmp_path / "data"
    cruise_dir = data_root / "CRUISE"
    cruise_dir.mkdir(parents=True)
    nc_path = cruise_dir / "cast.nc"

    ds = xr.Dataset(
        {
            "sea_water_temperature": (["z"], [20.0, 21.0]),
            "time": (["profile"], [1_138_137_101.0]),
            "time_elapsed": (["z"], [0.0, 1.5]),
        },
        coords={"z": [0.0, 1.0], "profile": [0]},
    )
    ds["time"].attrs.update(
        {
            "standard_name": "time",
            "units": "seconds since 1970-01-01T00:00:00Z",
            "calendar": "julian",
            "axis": "T",
        }
    )
    ds["time_elapsed"].attrs.update(
        {
            "standard_name": "time",
            "units": "seconds since 2006-01-24T21:11:41Z",
            "calendar": "proleptic_gregorian",
            "axis": "T",
        }
    )
    ds["station"] = xr.DataArray(["1"], dims=["profile"])
    ds["cruiseID"] = xr.DataArray(["CRUISE"], dims=["profile"])
    ds.to_netcdf(nc_path)

    profile = _profile(data_root, tmp_path / "output")
    run_qc_for_file(nc_path, profile=profile, data_root=data_root)

    output_path = tmp_path / "output" / "CRUISE" / "cast.nc"
    with xr.open_dataset(nc_path, decode_cf=False) as original, xr.open_dataset(
        output_path, decode_cf=False
    ) as out:
        for var in ("time", "time_elapsed"):
            assert out[var].attrs.get("units") == original[var].attrs.get("units")
            assert str(out[var].dtype) == str(original[var].dtype)
            assert np.array_equal(out[var].values, original[var].values)


def test_run_qc_for_file_preserves_dataset_metadata(minimal_profile_ds, tmp_path: Path):
    data_root = tmp_path / "data"
    cruise_dir = data_root / "CRUISE"
    cruise_dir.mkdir(parents=True)
    nc_path = cruise_dir / "cast.nc"
    ds = minimal_profile_ds.copy()
    ds["cruiseID"] = xr.DataArray([["WS0603"] * 5], dims=("profile", "z"))
    ds["station"] = xr.DataArray([["DT4"] * 5], dims=("profile", "z"))
    ds.attrs["time_coverage_start"] = "2006-01-22T19:05:11Z"
    ds["profile"].attrs["long_name"] = "old_profile_name"
    ds.attrs["title"] = "Original dataset title"
    ds.to_netcdf(nc_path)

    profile = _profile(data_root, tmp_path / "output")
    run_qc_for_file(nc_path, profile=profile, data_root=data_root)

    output_path = tmp_path / "output" / "CRUISE" / "cast.nc"
    with xr.open_dataset(output_path, decode_cf=False) as out:
        assert out["profile"].attrs["long_name"] == "old_profile_name"
        assert out.attrs["title"] == "Original dataset title"


def test_climatology_overrides_apply_without_station_config(
    minimal_profile_ds, tmp_path: Path, monkeypatch
):
    data_root = tmp_path / "data"
    nc_path = data_root / "CRUISE" / "cast.nc"
    nc_path.parent.mkdir(parents=True)
    minimal_profile_ds.to_netcdf(nc_path)
    profile = _profile(data_root, tmp_path / "output")
    monkeypatch.setattr(
        "qc_runner.get_climatology_config_for_file", lambda *args, **kwargs: None
    )

    results = run_single_test(
        nc_path,
        "climatology_test",
        variable="sea_water_temperature",
        climatology_overrides={
            "sea_water_temperature": [
                {
                    "zspan": [0.0, 100.0],
                    "tspan": [1, 12],
                    "vspan": [0.0, 30.0],
                    "period": "month",
                }
            ]
        },
        print_summary=False,
        profile=profile,
    )

    assert np.any(results[0].flags != QC_FLAGS["NOT_EVALUATED"])


def test_run_qc_for_directory_processes_files_in_sorted_order(tmp_path: Path, monkeypatch):
    for name in ("b.nc", "a.nc"):
        (tmp_path / name).touch()
    seen: list[str] = []
    monkeypatch.setattr(
        "qc_runner.run_qc_for_file",
        lambda path, **kwargs: seen.append(Path(path).name),
    )

    run_qc_for_directory(tmp_path, profile=default_profile(), data_root=tmp_path)

    assert seen == ["a.nc", "b.nc"]


def test_run_qc_for_all_rejects_empty_tree(tmp_path: Path):
    with pytest.raises(ValueError, match="No NetCDF"):
        run_qc_for_all(tmp_path, profile=_profile(tmp_path, tmp_path / "output"))


def test_run_qc_for_all_processes_files_in_sorted_order(tmp_path: Path, monkeypatch):
    for name in ("B", "A"):
        cruise = tmp_path / name
        cruise.mkdir()
        (cruise / "cast.nc").touch()
    seen: list[str] = []
    monkeypatch.setattr(
        "qc_runner.run_qc_for_file",
        lambda path, **kwargs: seen.append(str(Path(path).relative_to(tmp_path))),
    )

    run_qc_for_all(tmp_path, profile=_profile(tmp_path, tmp_path / "output"))

    assert seen == ["A/cast.nc", "B/cast.nc"]
