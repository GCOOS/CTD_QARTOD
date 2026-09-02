from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from dataset_profile import DEFAULT_CNV_PROFILE_PATH, DEFAULT_PROFILE_PATH, load_dataset_profile
from qc_validation import validate_qc_profile


def _replace_path(profile, key: str, path: Path):
    return replace(profile, paths=replace(profile.paths, **{key: path}))


def _replace_json(profile, tmp_path: Path, key: str, payload: object):
    path = tmp_path / f"{key}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return _replace_path(profile, key, path)


@pytest.mark.parametrize("profile_path", [DEFAULT_PROFILE_PATH, DEFAULT_CNV_PROFILE_PATH])
def test_active_profiles_pass_preflight(profile_path: Path):
    paths = validate_qc_profile(load_dataset_profile(profile_path))
    assert paths["variable_mapping"].is_file()
    assert paths["flat_line_config"].is_file()


def test_validation_rejects_missing_required_file(tmp_path: Path):
    profile = _replace_path(
        load_dataset_profile(DEFAULT_PROFILE_PATH),
        "variable_mapping",
        tmp_path / "missing.json",
    )
    with pytest.raises(ValueError, match="variable_mapping"):
        validate_qc_profile(profile)


def test_validation_rejects_duplicate_variable_categories(tmp_path: Path):
    profile = _replace_json(
        load_dataset_profile(DEFAULT_PROFILE_PATH),
        tmp_path,
        "variable_mapping",
        {"temperature": ["same_var"], "practical_salinity": ["same_var"]},
    )
    with pytest.raises(ValueError, match="same_var.*multiple categories"):
        validate_qc_profile(profile)


def test_validation_rejects_duplicate_normalized_stations(tmp_path: Path):
    path = tmp_path / "stations.csv"
    path.write_text(
        "station,lat_mean,lon_mean\nTB1,27.8,-82.8\nTB1 ,27.9,-82.9\n",
        encoding="utf-8",
    )
    profile = _replace_path(
        load_dataset_profile(DEFAULT_PROFILE_PATH), "station_coords", path
    )
    with pytest.raises(ValueError, match="duplicate station.*tb1"):
        validate_qc_profile(profile)


def test_validation_rejects_overlapping_station_classes(tmp_path: Path):
    profile = _replace_json(
        load_dataset_profile(DEFAULT_PROFILE_PATH),
        tmp_path,
        "station_depth_classification",
        {"deep_cast": ["A"], "shallow_cast": ["a "]},
    )
    with pytest.raises(ValueError, match="deep_cast and shallow_cast.*a"):
        validate_qc_profile(profile)


def test_validation_rejects_unknown_sensor_reference(tmp_path: Path):
    profile = _replace_json(
        load_dataset_profile(DEFAULT_PROFILE_PATH),
        tmp_path,
        "variable_sensor_map",
        {"sea_water_temperature": "missing_sensor"},
    )
    with pytest.raises(ValueError, match="missing_sensor"):
        validate_qc_profile(profile)


@pytest.mark.parametrize(
    ("key", "payload", "message"),
    [
        (
            "spike_thresholds",
            {"sea_water_temperature": {"suspect_threshold": 2, "fail_threshold": 1}},
            "suspect_threshold.*fail_threshold",
        ),
        (
            "rate_of_change_thresholds",
            {"sea_water_temperature": {"threshold": 0}},
            "threshold.*positive",
        ),
        (
            "flat_line_config",
            {"rep_cnt_suspect": 6, "rep_cnt_fail": 3, "eps": 0},
            "rep_cnt_fail.*rep_cnt_suspect",
        ),
    ],
)
def test_validation_rejects_invalid_thresholds(
    tmp_path: Path, key: str, payload: object, message: str
):
    profile = _replace_json(
        load_dataset_profile(DEFAULT_PROFILE_PATH), tmp_path, key, payload
    )
    with pytest.raises(ValueError, match=message):
        validate_qc_profile(profile)


def test_validation_rejects_unavailable_placeholder_run_mode():
    profile = replace(
        load_dataset_profile(DEFAULT_PROFILE_PATH),
        qc_test_modes={"gap_test": "run", "syntax_test": "not_evaluated"},
    )
    with pytest.raises(ValueError, match="gap_test.*not implemented"):
        validate_qc_profile(profile)
