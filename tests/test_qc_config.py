from __future__ import annotations

import json
from pathlib import Path

import pytest
import xarray as xr

from qc_config import (
    FLAT_LINE_CONFIG_JSON,
    LOCATION_CONFIG_JSON,
    RATE_OF_CHANGE_THRESHOLDS_JSON,
    SPIKE_THRESHOLDS_JSON,
    TEST_CATEGORIES,
    get_climatology_config_for_file,
    load_flat_line_config,
    load_location_config,
    load_rate_of_change_thresholds,
    load_spike_thresholds,
)


def test_load_spike_thresholds_flat_file():
    if not SPIKE_THRESHOLDS_JSON.exists():
        pytest.skip("spike_thresholds.json missing")
    d = load_spike_thresholds(SPIKE_THRESHOLDS_JSON)
    assert "sea_water_temperature" in d
    assert "suspect_threshold" in d["sea_water_temperature"]


def test_load_rate_of_change_thresholds_flat_file():
    if not RATE_OF_CHANGE_THRESHOLDS_JSON.exists():
        pytest.skip("rate_of_change_thresholds.json missing")
    d = load_rate_of_change_thresholds(RATE_OF_CHANGE_THRESHOLDS_JSON)
    assert "sea_water_temperature" in d
    assert "threshold" in d["sea_water_temperature"]


def test_load_flat_line_config_file():
    if not FLAT_LINE_CONFIG_JSON.exists():
        pytest.skip("flat_line_config.json missing")
    d = load_flat_line_config(FLAT_LINE_CONFIG_JSON)
    assert d == {"rep_cnt_suspect": 3, "rep_cnt_fail": 5, "eps": 0.0}


def test_load_location_config_file():
    if not LOCATION_CONFIG_JSON.exists():
        pytest.skip("location_config.json missing")
    assert load_location_config(LOCATION_CONFIG_JSON) == {"tolerance": 0.035}


def test_load_spike_thresholds_rejects_legacy_variables_wrapper(tmp_path: Path):
    p = tmp_path / "thr.json"
    p.write_text(
        json.dumps(
            {
                "variables": {
                    "foo_var": {"suspect_threshold": 1, "fail_threshold": 2}
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="legacy.*variables"):
        load_spike_thresholds(p)


def test_load_thresholds_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_spike_thresholds(tmp_path / "nope.json")
    with pytest.raises(FileNotFoundError):
        load_rate_of_change_thresholds(tmp_path / "nope.json")


def test_load_flat_line_config_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_flat_line_config(tmp_path / "nope.json")


def test_load_location_config_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_location_config(tmp_path / "nope.json")


def test_load_location_config_rejects_invalid_fields(tmp_path: Path):
    p = tmp_path / "location.json"
    p.write_text(json.dumps({"tolerance": -1}), encoding="utf-8")
    with pytest.raises(ValueError, match="tolerance"):
        load_location_config(p)


def test_load_flat_line_config_rejects_missing_and_invalid_fields(tmp_path: Path):
    p = tmp_path / "flat.json"
    p.write_text(json.dumps({"rep_cnt_suspect": 4, "eps": -1}), encoding="utf-8")
    with pytest.raises(ValueError, match="rep_cnt_fail|eps"):
        load_flat_line_config(p)


def test_load_flat_line_config_rejects_invalid_repeat_order(tmp_path: Path):
    p = tmp_path / "flat.json"
    p.write_text(json.dumps({"rep_cnt_suspect": 6, "rep_cnt_fail": 3, "eps": 0.0}), encoding="utf-8")
    with pytest.raises(ValueError, match="rep_cnt_fail"):
        load_flat_line_config(p)


def test_default_config_paths_exist():
    assert SPIKE_THRESHOLDS_JSON.exists()
    assert RATE_OF_CHANGE_THRESHOLDS_JSON.exists()
    assert FLAT_LINE_CONFIG_JSON.exists()
    assert LOCATION_CONFIG_JSON.exists()


def test_test_categories_keys_match_pipeline():
    expected = {
        "gap_test",
        "syntax_test",
        "location_test",
        "gross_range_test",
        "decreasing_radiance_test",
        "climatology_test",
        "flat_line_test",
        "spike_test",
        "rate_of_change_test",
    }
    assert expected <= set(TEST_CATEGORIES.keys())
    assert "photic_zone_limit_test" not in TEST_CATEGORIES


def test_get_climatology_config_for_file(tmp_path: Path):
    limits = {
        "deep_cast_limits": {
            "sea_water_temperature": [{"tspan": (1, 12), "vspan": (0.0, 50.0), "period": "month"}]
        }
    }
    classification = {"deep_cast": ["stn_a"], "shallow_cast": []}
    lim_path = tmp_path / "lim.json"
    cls_path = tmp_path / "cls.json"
    lim_path.write_text(json.dumps(limits), encoding="utf-8")
    cls_path.write_text(json.dumps(classification), encoding="utf-8")

    ds = xr.Dataset()
    ds["station"] = xr.DataArray("stn_a")
    cfg = get_climatology_config_for_file(ds, limits_json_path=lim_path, classification_json_path=cls_path)
    assert cfg is not None
    assert "sea_water_temperature" in cfg


@pytest.mark.parametrize("stored_station,reference_station", [
    ("57_2", "57.2"),
    ("9_5", "9.5"),
])
def test_climatology_matches_dotted_station_reference(
    tmp_path: Path, stored_station: str, reference_station: str
):
    limits_path = tmp_path / "limits.json"
    classes_path = tmp_path / "classes.json"
    limits_path.write_text(json.dumps({"deep_cast_limits": {"sea_water_temperature": [{}]}}))
    classes_path.write_text(json.dumps({"deep_cast": [reference_station], "shallow_cast": []}))
    ds = xr.Dataset({"station": xr.DataArray(stored_station)})

    config = get_climatology_config_for_file(ds, limits_path, classes_path)

    assert config is not None
    assert "sea_water_temperature" in config
