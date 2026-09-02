"""Integration checks for station_climatology_config.json with the QC pipeline."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from ioos_qc.qartod import ClimatologyConfig

from dataset_profile import DatasetProfile, OutputConfig, default_profile
from qc_config import (
    QC_FLAGS,
    STATION_CLIMATOLOGY_JSON,
    get_climatology_config_for_file,
    load_station_climatology_config,
)
from qc_runner import _get_depth_for_var, _get_time_for_var, run_qc_for_file
from qc_tests.climatology_test import climatology_test

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIM_JSON = STATION_CLIMATOLOGY_JSON

SAMPLES = {
    "shallow_station_1": REPO_ROOT / "datasets/SFER_QC/WS0603/WS0603_1.nc",
    "deep_station_29_5": REPO_ROOT / "datasets/SFER_QC/WS0603/WS0603_29_5.nc",
    "deep_station_21LK": REPO_ROOT / "datasets/SFER_QC/WS0603/WS0603_21LK.nc",
}

CLIMATOLOGY_VARS = (
    "sea_water_temperature",
    "sea_water_salinity",
    "dissolved_oxygen",
)


def _rule_key_order(rule: dict) -> list[str]:
    return list(rule.keys())


@pytest.fixture
def profile():
    return default_profile()


@pytest.fixture
def limits_root():
    return load_station_climatology_config(CLIM_JSON)


def test_climatology_json_loads_and_rule_key_order(limits_root):
    assert "deep_cast_limits" in limits_root
    assert "shallow_cast_limits" in limits_root
    rule = limits_root["deep_cast_limits"]["sea_water_temperature"][0]
    assert _rule_key_order(rule) == ["zspan", "tspan", "vspan", "period"]
    assert "oxygen_saturation" not in limits_root["deep_cast_limits"]
    assert "oxygen_saturation" not in limits_root["shallow_cast_limits"]


def test_climatology_config_builds_in_ioos_qc(limits_root):
    for cast_key in ("deep_cast_limits", "shallow_cast_limits"):
        for var_name, rules in limits_root[cast_key].items():
            cfg = ClimatologyConfig()
            for rule in rules:
                cfg.add(
                    tspan=rule["tspan"],
                    vspan=rule["vspan"],
                    zspan=rule.get("zspan"),
                    period=rule.get("period"),
                )
            assert len(cfg.members) == len(rules), f"{cast_key}/{var_name}"


@pytest.mark.parametrize("sample_name,nc_path", list(SAMPLES.items()))
def test_get_climatology_config_for_sample_files(profile, sample_name, nc_path):
    if not nc_path.exists():
        pytest.skip(f"Sample file not found: {nc_path}")
    ds = xr.open_dataset(nc_path)
    try:
        clim = get_climatology_config_for_file(
            ds,
            limits_json_path=CLIM_JSON,
            metadata=profile.metadata,
        )
        assert clim is not None, f"No climatology config resolved for {sample_name}"
        for var in CLIMATOLOGY_VARS:
            assert var in clim, f"{var} missing from config for {sample_name}"
            assert len(clim[var]) >= 1
    finally:
        ds.close()


@pytest.mark.parametrize("sample_name,nc_path", list(SAMPLES.items()))
def test_climatology_test_runs_on_sample_files(profile, sample_name, nc_path):
    if not nc_path.exists():
        pytest.skip(f"Sample file not found: {nc_path}")
    ds = xr.open_dataset(nc_path)
    try:
        clim = get_climatology_config_for_file(ds, limits_json_path=CLIM_JSON, metadata=profile.metadata)
        assert clim is not None
        for var in CLIMATOLOGY_VARS:
            if var not in ds:
                continue
            data_var = ds[var]
            depth = _get_depth_for_var(ds, data_var, profile)
            time = _get_time_for_var(ds, data_var, profile)
            assert depth is not None and time is not None
            flags = climatology_test(data_var, time=time, depth=depth, config=clim[var])
            assert flags.shape == ds[var].shape
            unique = set(np.unique(flags).tolist())
            assert unique.issubset(set(QC_FLAGS.values()))
            assert QC_FLAGS["NOT_EVALUATED"] not in unique or len(unique) > 1, (
                f"{sample_name}/{var}: all NOT_EVALUATED — limits may not match cast"
            )
    finally:
        ds.close()


@pytest.mark.parametrize("sample_name,nc_path", list(SAMPLES.items()))
def test_full_qc_pipeline_writes_climatology_flags(profile, sample_name, nc_path, tmp_path):
    if not nc_path.exists():
        pytest.skip(f"Sample file not found: {nc_path}")
    work_nc = tmp_path / f"{sample_name}.nc"
    shutil.copy2(nc_path, work_nc)
    in_place_profile = DatasetProfile(
        data_root=tmp_path,
        metadata=profile.metadata,
        output=OutputConfig(mode="in_place", directory=tmp_path),
        paths=profile.paths,
    )
    run_qc_for_file(work_nc, profile=in_place_profile, data_root=tmp_path)
    ds = xr.open_dataset(work_nc)
    try:
        for var in CLIMATOLOGY_VARS:
            qc_name = f"{var}_qc_climatology"
            assert qc_name in ds, f"Missing {qc_name} after pipeline for {sample_name}"
            flags = np.asarray(ds[qc_name].values).ravel()
            evaluated = np.sum(flags != QC_FLAGS["NOT_EVALUATED"])
            assert evaluated > 0, f"{qc_name}: no evaluated points for {sample_name}"
    finally:
        ds.close()
