"""Tests for the Dash QC result viewer data helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from dataset_profile import DatasetProfile, PathsConfig
from qc_dashboard import (
    _climatology_limits_for_samples,
    _issue_filter_dropdown_state,
    _location_info_for_file,
    _make_figure,
    adjacent_file,
    adjacent_issue_key,
    build_qc_issue_index,
    create_app,
    discover_qc_files,
    discover_tests_for_variable,
    discover_variables,
    filter_issue_rows,
    flag_summary,
    issue_row_from_key,
    issue_row_key,
    issue_row_selection,
    load_qc_issue_index,
    write_qc_issue_index,
)


def _write_qc_fixture(path: Path, include_ancillary: bool = True) -> None:
    ds = xr.Dataset(
        {
            "depth": (["z"], np.array([0.0, 1.0, 0.8, 2.0], dtype=np.float32)),
            "sea_water_temperature": (
                ["z"],
                np.array([20.0, 20.1, 99.0, 20.3], dtype=np.float32),
            ),
            "sea_water_temperature_qc_agg": (
                ["z"],
                np.array([1, 1, 4, 1], dtype=np.int8),
            ),
            "sea_water_temperature_qc_spike": (
                ["z"],
                np.array([1, 1, 3, 1], dtype=np.int8),
            ),
            "unmapped_numeric": (
                ["z"],
                np.array([5.0, 5.1, 5.2, 5.3], dtype=np.float32),
            ),
            "unmapped_numeric_qc_agg": (
                ["z"],
                np.array([1, 1, 1, 1], dtype=np.int8),
            ),
        },
        attrs={"title": "Test cast"},
    )
    ds["sea_water_temperature"].attrs["units"] = "degree_Celsius"
    if include_ancillary:
        ds["sea_water_temperature"].attrs[
            "ancillary_variables"
        ] = "sea_water_temperature_qc_agg sea_water_temperature_qc_spike"
    ds["sea_water_temperature_qc_agg"].attrs["standard_name"] = "aggregate_quality_flag"
    ds["sea_water_temperature_qc_spike"].attrs["standard_name"] = "spike_test_quality_flag"
    ds["unmapped_numeric"].attrs["ancillary_variables"] = "unmapped_numeric_qc_agg"

    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path)


def test_discover_qc_files_groups_by_cruise(tmp_path: Path) -> None:
    root = tmp_path / "SFER_QC"
    first = root / "CRUISE_A" / "cast001.nc"
    second = root / "CRUISE_B" / "cast002.nc"
    _write_qc_fixture(first)
    _write_qc_fixture(second)

    files = discover_qc_files(root)

    assert sorted(files) == ["CRUISE_A", "CRUISE_B"]
    assert files["CRUISE_A"] == [first]
    assert files["CRUISE_B"] == [second]


def test_discover_variables_and_tests_from_ancillary_metadata(tmp_path: Path) -> None:
    nc_path = tmp_path / "SFER_QC" / "CRUISE_A" / "cast001.nc"
    _write_qc_fixture(nc_path, include_ancillary=True)

    with xr.open_dataset(nc_path, decode_cf=False, mask_and_scale=True) as ds:
        variables = discover_variables(ds, {"temperature": ["sea_water_temperature"]})
        tests = discover_tests_for_variable(ds, "sea_water_temperature")

    assert [item.value for item in variables] == ["sea_water_temperature"]
    assert [item.value for item in tests] == [
        "sea_water_temperature_qc_agg",
        "sea_water_temperature_qc_spike",
    ]
    assert tests[0].label == "Aggregate"
    assert tests[1].label == "Spike"


def test_discover_variables_excludes_unmapped_qc_variables(tmp_path: Path) -> None:
    nc_path = tmp_path / "SFER_QC" / "CRUISE_A" / "cast001.nc"
    _write_qc_fixture(nc_path, include_ancillary=True)

    with xr.open_dataset(nc_path, decode_cf=False, mask_and_scale=True) as ds:
        variables = discover_variables(ds, {"temperature": ["sea_water_temperature"]})

    assert [item.value for item in variables] == ["sea_water_temperature"]
    assert [item.label for item in variables] == ["sea_water_temperature"]


def test_discover_tests_falls_back_to_qc_prefix(tmp_path: Path) -> None:
    nc_path = tmp_path / "SFER_QC" / "CRUISE_A" / "cast001.nc"
    _write_qc_fixture(nc_path, include_ancillary=False)

    with xr.open_dataset(nc_path, decode_cf=False, mask_and_scale=True) as ds:
        tests = discover_tests_for_variable(ds, "sea_water_temperature")

    assert [item.value for item in tests] == [
        "sea_water_temperature_qc_agg",
        "sea_water_temperature_qc_spike",
    ]


def test_flag_summary_counts_known_flags() -> None:
    rows = flag_summary(np.array([1, 1, 2, 3, 4, 4, 9], dtype=np.int8))
    by_flag = {row["flag"]: row for row in rows}

    assert by_flag["PASS"]["count"] == 2
    assert by_flag["NOT_EVALUATED"]["count"] == 1
    assert by_flag["SUSPECT"]["count"] == 1
    assert by_flag["FAIL"]["count"] == 2
    assert by_flag["MISSING"]["count"] == 1
    assert by_flag["FAIL"]["percent"] == pytest.approx(28.5714, rel=1e-4)


def test_adjacent_file_moves_within_current_cruise() -> None:
    files = [Path("A/one.nc"), Path("A/two.nc"), Path("A/three.nc")]

    assert adjacent_file(files, "A/two.nc", -1) == "A/one.nc"
    assert adjacent_file(files, "A/two.nc", 1) == "A/three.nc"
    assert adjacent_file(files, "A/one.nc", -1) == "A/one.nc"
    assert adjacent_file(files, "A/three.nc", 1) == "A/three.nc"
    assert adjacent_file(files, None, 1) == "A/one.nc"


def test_climatology_limits_match_depth_and_month() -> None:
    config = [
        {"zspan": [0.0, 10.0], "tspan": [1, 6], "vspan": [10.0, 20.0]},
        {"zspan": [10.0, 50.0], "tspan": [7, 12], "vspan": [5.0, 15.0]},
    ]

    limits = _climatology_limits_for_samples(
        config,
        depth=np.array([5.0, 25.0, 75.0], dtype=float),
        months=np.array([3.0, 9.0, 9.0], dtype=float),
    )

    assert limits is not None
    np.testing.assert_allclose(limits["lower"][:2], [10.0, 5.0])
    np.testing.assert_allclose(limits["upper"][:2], [20.0, 15.0])
    assert np.isnan(limits["lower"][2])
    assert np.isnan(limits["upper"][2])


def test_climatology_limits_missing_inputs_return_none() -> None:
    config = [{"zspan": [0.0, 10.0], "tspan": [1, 12], "vspan": [10.0, 20.0]}]

    assert _climatology_limits_for_samples(config, depth=None, months=np.array([1.0])) is None
    assert _climatology_limits_for_samples(config, depth=np.array([1.0]), months=None) is None
    assert _climatology_limits_for_samples([], depth=np.array([1.0]), months=np.array([1.0])) is None


def test_figure_uses_x_markers_for_suspect_and_fail() -> None:
    payload = {
        "index": np.arange(4),
        "values": np.array([20.0, 21.0, 22.0, 23.0]),
        "depth": np.array([0.0, 1.0, 2.0, 3.0]),
        "flags": np.array([1, 3, 4, 9]),
        "variable_units": "degree_Celsius",
        "variable": "sea_water_temperature",
        "climatology_limits": None,
    }

    fig = _make_figure(payload, ["PASS", "SUSPECT", "FAIL", "MISSING"])
    marker_symbols = [
        trace.marker.symbol
        for trace in fig.data
        if getattr(trace, "mode", None) == "markers" and trace.name in {"SUSPECT (3)", "FAIL (4)"}
    ]

    assert marker_symbols
    assert set(marker_symbols) == {"x"}


def test_figure_adds_climatology_limit_traces() -> None:
    payload = {
        "index": np.arange(3),
        "values": np.array([20.0, 21.0, 22.0]),
        "depth": np.array([0.0, 1.0, 2.0]),
        "flags": np.array([1, 1, 1]),
        "variable_units": "degree_Celsius",
        "variable": "sea_water_temperature",
        "climatology_limits": {
            "lower": np.array([10.0, 10.0, 10.0]),
            "upper": np.array([30.0, 30.0, 30.0]),
        },
    }

    fig = _make_figure(payload, ["PASS"])
    names = [trace.name for trace in fig.data]

    assert "Climatology lower" in names
    assert "Climatology upper" in names


def test_location_info_resolves_actual_and_expected_coordinates(minimal_profile_ds, tmp_path: Path) -> None:
    station_csv = tmp_path / "Station_Mean_Coords.csv"
    station_csv.write_text("station,lat_mean,lon_mean\n1,25.7,-80.2\n", encoding="utf-8")
    profile = DatasetProfile(paths=PathsConfig(station_coords=station_csv))

    info = _location_info_for_file(minimal_profile_ds, minimal_profile_ds["sea_water_temperature"], profile)

    assert info is not None
    assert info["actual_lat"] == pytest.approx(25.6)
    assert info["actual_lon"] == pytest.approx(-80.1)
    assert info["expected_lat"] == pytest.approx(25.7)
    assert info["expected_lon"] == pytest.approx(-80.2)
    assert info["delta_lat"] == pytest.approx(-0.1)
    assert info["delta_lon"] == pytest.approx(0.1)


def test_figure_keeps_standard_layout_for_location_context() -> None:
    payload = {
        "index": np.arange(3),
        "values": np.array([20.0, 21.0, 22.0]),
        "depth": np.array([0.0, 1.0, 2.0]),
        "flags": np.array([1, 4, 1]),
        "variable_units": "degree_Celsius",
        "variable": "sea_water_temperature",
        "climatology_limits": None,
        "location_info": {
            "actual_lat": 25.6,
            "actual_lon": -80.1,
            "expected_lat": 25.7,
            "expected_lon": -80.2,
            "tolerance": 0.02,
        },
    }

    fig = _make_figure(payload, ["PASS", "FAIL"])
    names = [trace.name for trace in fig.data]

    assert "Actual location" not in names
    assert "Expected location" not in names
    assert fig.layout.height == 900


def test_build_qc_issue_index_from_fixture(tmp_path: Path) -> None:
    nc_path = tmp_path / "SFER_QC" / "CRUISE_A" / "cast001.nc"
    _write_qc_fixture(nc_path)

    rows = build_qc_issue_index(tmp_path / "SFER_QC", {"temperature": ["sea_water_temperature"]})
    by_qc = {row["qc_name"]: row for row in rows}

    assert set(by_qc) == {
        "sea_water_temperature_qc_agg",
        "sea_water_temperature_qc_spike",
    }
    assert by_qc["sea_water_temperature_qc_agg"]["fail"] == 1
    assert by_qc["sea_water_temperature_qc_agg"]["total"] == 4
    assert by_qc["sea_water_temperature_qc_spike"]["suspect"] == 1
    assert by_qc["sea_water_temperature_qc_spike"]["cruise"] == "CRUISE_A"


def test_qc_issue_index_cache_round_trip(tmp_path: Path) -> None:
    rows = [
        {
            "cruise": "CRUISE_A",
            "file": "cast001.nc",
            "path": "CRUISE_A/cast001.nc",
            "variable": "sea_water_temperature",
            "test": "spike",
            "test_label": "Spike",
            "qc_name": "sea_water_temperature_qc_spike",
            "pass": 3,
            "not_evaluated": 0,
            "suspect": 1,
            "fail": 0,
            "missing": 0,
            "total": 4,
        }
    ]
    cache_path = tmp_path / "qc_dashboard_index.json"

    written = write_qc_issue_index(cache_path, rows, {"data_root": "SFER_QC", "file_count": 1})
    loaded = load_qc_issue_index(cache_path)

    assert written["metadata"]["row_count"] == 1
    assert loaded["metadata"]["data_root"] == "SFER_QC"
    assert loaded["metadata"]["file_count"] == 1
    assert loaded["rows"] == rows


def test_filter_issue_rows_by_test_variable_and_flags() -> None:
    rows = [
        {"test": "spike", "variable": "temp", "suspect": 1, "fail": 0},
        {"test": "spike", "variable": "oxygen", "suspect": 0, "fail": 2},
        {"test": "flat_line", "variable": "temp", "suspect": 0, "fail": 0},
    ]

    assert filter_issue_rows(rows, test="spike", variable="ALL", flags=["SUSPECT"]) == [rows[0]]
    assert filter_issue_rows(rows, test="spike", variable="oxygen", flags=["FAIL"]) == [rows[1]]
    assert filter_issue_rows(rows, test="ALL", variable="ALL", flags=["SUSPECT", "FAIL"]) == rows[:2]


def test_issue_filter_dropdown_state_limits_variables_by_selected_test() -> None:
    rows = [
        {"test": "climatology", "test_label": "Climatology", "variable": "temp"},
        {"test": "spike", "test_label": "Spike", "variable": "temp"},
        {"test": "spike", "test_label": "Spike", "variable": "oxygen"},
    ]

    _test_options, test_value, variable_options, variable_value = _issue_filter_dropdown_state(
        rows,
        current_test="climatology",
        current_variable="oxygen",
        triggered_id="issue-test-dropdown",
    )

    assert test_value == "climatology"
    assert [item["value"] for item in variable_options] == ["ALL", "temp"]
    assert variable_value == "ALL"


def test_issue_filter_dropdown_state_limits_tests_by_selected_variable() -> None:
    rows = [
        {"test": "climatology", "test_label": "Climatology", "variable": "temp"},
        {"test": "spike", "test_label": "Spike", "variable": "temp"},
        {"test": "flat_line", "test_label": "Flat Line", "variable": "oxygen"},
    ]

    test_options, test_value, _variable_options, variable_value = _issue_filter_dropdown_state(
        rows,
        current_test="climatology",
        current_variable="oxygen",
        triggered_id="issue-variable-dropdown",
    )

    assert [item["value"] for item in test_options] == ["ALL", "flat_line"]
    assert test_value == "ALL"
    assert variable_value == "oxygen"


def test_issue_row_selection_uses_plot_target_fields() -> None:
    row = {
        "path": "output/SFER_QC/CRUISE_A/cast001.nc",
        "variable": "sea_water_temperature",
        "qc_name": "sea_water_temperature_qc_spike",
    }

    assert issue_row_selection(row) == row


def test_issue_row_key_round_trips_to_issue_row() -> None:
    rows = [
        {
            "path": "output/SFER_QC/CRUISE_A/cast001.nc",
            "variable": "sea_water_temperature",
            "qc_name": "sea_water_temperature_qc_spike",
        },
        {
            "path": "output/SFER_QC/CRUISE_A/cast002.nc",
            "variable": "sea_water_temperature",
            "qc_name": "sea_water_temperature_qc_flat_line",
        },
    ]

    key = issue_row_key(rows[1])

    assert issue_row_from_key(rows, key) == rows[1]
    assert issue_row_from_key(rows, "missing") is None


def test_adjacent_issue_key_moves_within_filtered_issues() -> None:
    rows = [
        {"path": "one.nc", "variable": "temp", "qc_name": "temp_qc_spike"},
        {"path": "two.nc", "variable": "temp", "qc_name": "temp_qc_spike"},
        {"path": "three.nc", "variable": "temp", "qc_name": "temp_qc_spike"},
    ]
    middle = issue_row_key(rows[1])

    assert issue_row_from_key(rows, adjacent_issue_key(rows, middle, -1)) == rows[0]
    assert issue_row_from_key(rows, adjacent_issue_key(rows, middle, 1)) == rows[2]
    assert issue_row_from_key(rows, adjacent_issue_key(rows, None, 1)) == rows[0]


def test_create_app_builds_without_starting_server(tmp_path: Path) -> None:
    pytest.importorskip("dash")
    nc_path = tmp_path / "SFER_QC" / "CRUISE_A" / "cast001.nc"
    _write_qc_fixture(nc_path)

    app = create_app(data_root=tmp_path / "SFER_QC")

    assert app.title == "CTD QC Viewer"
