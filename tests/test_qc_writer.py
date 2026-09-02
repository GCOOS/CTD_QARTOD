from __future__ import annotations

import json

import numpy as np
import pytest
import xarray as xr

from qc_config import QC_FLAGS
from dataset_profile import DatasetProfile, OutputConfig
from qc_writer import (
    aggregate_qc_flags,
    append_qc_history,
    resolve_output_path,
    set_ancillary_variables,
    write_aggregate_qc_results,
    write_qc_results,
)


def test_write_qc_results_adds_variable():
    ds = xr.Dataset({"temperature": xr.DataArray([1.0, 2.0], dims=("z",))})
    flags = np.array([QC_FLAGS["PASS"], QC_FLAGS["SUSPECT"]], dtype=int)
    out = write_qc_results(
        ds,
        "temperature",
        "gap_test",
        flags,
        applied_config={"mode": "not_evaluated"},
        config_source="config/dataset_profile.json",
    )
    assert "temperature_qc_gap" in out.variables
    qc_var = out["temperature_qc_gap"]
    assert np.array_equal(qc_var.values, flags)
    assert qc_var.dtype == np.int8
    assert qc_var.attrs["standard_name"] == "gap_test_quality_flag"
    assert qc_var.attrs["flag_meanings"] == "PASS NOT_EVALUATED SUSPECT FAIL MISSING"
    assert qc_var.attrs["units"] == "1"
    assert qc_var.attrs["ioos_qc_test"] == "gap_test"
    assert qc_var.attrs["ioos_qc_target"] == "temperature"
    assert qc_var.attrs["ioos_qc_module"] == "qc_tests.gap_test"
    assert json.loads(qc_var.attrs["ioos_qc_config"]) == {
        "mode": "not_evaluated"
    }
    assert qc_var.attrs["ioos_qc_config_source"] == "config/dataset_profile.json"


def test_write_qc_results_raises_for_missing_data_var():
    ds = xr.Dataset({"temperature": xr.DataArray([1.0])})
    try:
        write_qc_results(ds, "missing", "gap_test", [1])
    except KeyError as e:
        assert "missing" in str(e).lower()
    else:
        raise AssertionError("expected KeyError")


def test_aggregate_qc_flags_uses_worst_case_and_missing_data():
    data = xr.DataArray([1.0, 2.0, np.nan, 4.0], dims=("z",))
    flags = aggregate_qc_flags(
        [
            [QC_FLAGS["PASS"], QC_FLAGS["SUSPECT"], QC_FLAGS["PASS"], QC_FLAGS["MISSING"]],
            [QC_FLAGS["PASS"], QC_FLAGS["FAIL"], QC_FLAGS["PASS"], QC_FLAGS["MISSING"]],
        ],
        data=data,
    )
    assert flags.tolist() == [
        QC_FLAGS["PASS"],
        QC_FLAGS["FAIL"],
        QC_FLAGS["MISSING"],
        QC_FLAGS["MISSING"],
    ]


def test_write_aggregate_and_set_ancillary_variables():
    ds = xr.Dataset({"temperature": xr.DataArray([1.0, 2.0], dims=("z",))})
    out = write_aggregate_qc_results(ds, "temperature", [QC_FLAGS["PASS"], QC_FLAGS["FAIL"]])
    assert "temperature_qc_agg" in out.variables
    assert out["temperature_qc_agg"].attrs["standard_name"] == "aggregate_quality_flag"

    out = set_ancillary_variables(out, "temperature", ["temperature_qc_agg", "temperature_qc_gap"])
    assert out["temperature"].attrs["ancillary_variables"] == "temperature_qc_agg temperature_qc_gap"


def test_resolve_output_path_in_place(tmp_path):
    path = tmp_path / "data" / "file.nc"
    profile = DatasetProfile(output=OutputConfig(mode="in_place", directory=tmp_path / "output"))
    assert resolve_output_path(path, profile, tmp_path / "data") == path


def test_resolve_output_path_duplicate_mirrors_tree(tmp_path):
    data_root = tmp_path / "data"
    path = data_root / "cruise" / "file.nc"
    profile = DatasetProfile(output=OutputConfig(mode="duplicate", directory=tmp_path / "output"))
    assert resolve_output_path(path, profile, data_root) == tmp_path / "output" / "cruise" / "file.nc"


def test_resolve_output_path_duplicate_rejects_input_outside_data_root(tmp_path):
    data_root = tmp_path / "data"
    path = tmp_path / "other" / "file.nc"
    profile = DatasetProfile(
        output=OutputConfig(mode="duplicate", directory=tmp_path / "output")
    )

    with pytest.raises(ValueError, match="outside data root"):
        resolve_output_path(path, profile, data_root)


def test_append_qc_history_preserves_existing_history():
    ds = xr.Dataset(attrs={"history": "2024-01-01: source conversion"})

    out = append_qc_history(ds, timestamp="2026-08-30T12:00:00Z")

    assert out.attrs["history"] == (
        "2024-01-01: source conversion\n"
        "2026-08-30T12:00:00Z: QARTOD QC applied by CTD_QARTOD"
    )
