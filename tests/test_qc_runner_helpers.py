from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

import qc_runner
from qc_config import QC_FLAGS


def test_should_run_test_temperature_gets_core_tests():
    assert qc_runner._should_run_test("gap_test", "temperature") is True
    assert qc_runner._should_run_test("climatology_test", "temperature") is True
    assert qc_runner._should_run_test("decreasing_radiance_test", "temperature") is False


def test_broadcast_like_expands_scalar():
    target = xr.DataArray(np.zeros((2, 3)), dims=("a", "b"))
    src = xr.DataArray(7.0)
    b = qc_runner._broadcast_like(target, src)
    assert b.shape == (2, 3)
    assert np.all(b == 7.0)


def test_broadcast_like_uses_named_dimensions():
    target = xr.DataArray(np.zeros((2, 3)), dims=("profile", "z"))
    source = xr.DataArray([10.0, 20.0], dims=("profile",))

    broadcast = qc_runner._broadcast_like(target, source)

    assert np.array_equal(broadcast, [[10.0, 10.0, 10.0], [20.0, 20.0, 20.0]])


def test_broadcast_like_rejects_unrelated_dimensions():
    target = xr.DataArray(np.zeros((2, 3)), dims=("profile", "z"))
    source = xr.DataArray([10.0, 20.0], dims=("other",))

    with pytest.raises(ValueError, match="cannot broadcast.*other"):
        qc_runner._broadcast_like(target, source)


def test_get_depth_for_var_from_coord(minimal_profile_ds):
    ds = minimal_profile_ds
    v = ds["sea_water_temperature"]
    d = qc_runner._get_depth_for_var(ds, v)
    assert d is not None
    assert d.shape == v.shape


def test_get_time_for_var_from_coord(minimal_profile_ds):
    ds = minimal_profile_ds
    v = ds["sea_water_temperature"]
    t = qc_runner._get_time_for_var(ds, v)
    assert t is not None
    assert t.shape == v.shape


def test_spike_params_for_var():
    thr = {"sea_water_temperature": {"suspect_threshold": 1, "fail_threshold": 2}}
    p = qc_runner._spike_params_for_var(thr, "sea_water_temperature")
    assert p == (1.0, 2.0)
    assert qc_runner._spike_params_for_var(thr, "other") is None


def test_roc_threshold_for_var():
    thr = {"x": {"threshold": 0.5}}
    assert qc_runner._roc_threshold_for_var(thr, "x") == 0.5
    assert qc_runner._roc_threshold_for_var(thr, "y") is None
    bad = {"x": {"threshold": -1}}
    assert qc_runner._roc_threshold_for_var(bad, "x") is None


def test_list_available_tests_non_empty():
    names = qc_runner.list_available_tests()
    assert "location_test" in names
    assert "photic_zone_limit_test" not in names
    assert len(names) >= 8


def _run_test_with_encoded_fill(ds: xr.Dataset, test_name: str) -> np.ndarray:
    ds = ds.copy(deep=True)
    var_name = "sea_water_temperature"
    ds[var_name].values[0, -1] = -9.99e-29
    ds[var_name].attrs["_FillValue"] = -9.99e-29
    ds[var_name].attrs["missing_value"] = -9.99e-29
    return qc_runner._run_single_test_for_var(
        test_name=test_name,
        var_name=var_name,
        data_var=ds[var_name],
        ds=ds,
        category="temperature",
        gross_ranges={
            var_name: {
                "fail_span": (-2.0, 40.0),
                "suspect_span": (0.0, 35.0),
            }
        },
        climatology_config={
            var_name: [
                {
                    "tspan": [1, 12],
                    "zspan": [0.0, 100.0],
                    "vspan": [0.0, 30.0],
                }
            ]
        },
        expected_location=(-80.1, 25.6),
        location_tolerance=0.05,
    )


def test_encoded_fill_value_is_missing_for_gross_range(minimal_profile_ds):
    flags = _run_test_with_encoded_fill(minimal_profile_ds, "gross_range_test")

    assert flags[0, -1] == QC_FLAGS["MISSING"]
    assert QC_FLAGS["SUSPECT"] not in flags[0, -1:]


def test_encoded_fill_value_is_missing_for_climatology(minimal_profile_ds):
    flags = _run_test_with_encoded_fill(minimal_profile_ds, "climatology_test")

    assert flags[0, -1] == QC_FLAGS["MISSING"]


def test_encoded_fill_value_is_missing_for_not_evaluated_tests(minimal_profile_ds):
    flags = _run_test_with_encoded_fill(minimal_profile_ds, "syntax_test")

    assert flags[0, 0] == QC_FLAGS["NOT_EVALUATED"]
    assert flags[0, -1] == QC_FLAGS["MISSING"]


def test_placeholder_run_mode_fails_instead_of_silently_not_evaluating(
    minimal_profile_ds,
):
    from dataclasses import replace
    from dataset_profile import DatasetProfile

    profile = replace(
        DatasetProfile(),
        qc_test_modes={"gap_test": "run", "syntax_test": "not_evaluated"},
    )
    with pytest.raises(ValueError, match="gap_test.*not implemented"):
        qc_runner._run_single_test_for_var(
            test_name="gap_test",
            var_name="sea_water_temperature",
            data_var=minimal_profile_ds["sea_water_temperature"],
            ds=minimal_profile_ds,
            category="temperature",
            gross_ranges=None,
            climatology_config=None,
            expected_location=None,
            location_tolerance=None,
            profile=profile,
        )
