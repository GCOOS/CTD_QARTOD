"""Ensure config_template files are valid and loadable."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from dataset_profile import load_dataset_profile
from qc_config import (
    load_rate_of_change_thresholds,
    load_flat_line_config,
    load_location_config,
    load_spike_thresholds,
    load_station_climatology_config,
    load_station_depth_classification,
)
from instrument_resolver import load_sensor_specs, load_variable_sensor_map
from qc_data_loader import load_mapping

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "config_template"


@pytest.mark.parametrize(
    "rel_path",
    [
        "dataset_profile.json",
        "qc_variable_mapping.json",
        "gross_range_test/sensor_specs.json",
        "gross_range_test/variable_sensor_map.json",
        "climatology_test/station_climatology_config.json",
        "climatology_test/station_depth_classification.json",
        "location_test/location_config.json",
        "spike_test/spike_thresholds.json",
        "rate_of_change_test/rate_of_change_thresholds.json",
        "flat_line_test/flat_line_config.json",
    ],
)
def test_template_json_parses(rel_path: str):
    path = TEMPLATE_ROOT / rel_path
    assert path.exists(), f"missing template {rel_path}"
    with path.open(encoding="utf-8") as f:
        json.load(f)


def test_template_station_coords_csv():
    path = TEMPLATE_ROOT / "location_test" / "Station_Mean_Coords.csv"
    assert path.exists()
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert {"station", "lat_mean", "lon_mean"} <= set(rows[0].keys())


def test_template_loaders_accept_templates():
    profile = load_dataset_profile(TEMPLATE_ROOT / "dataset_profile.json")
    assert profile.metadata.sample_dimension == "z"
    assert profile.output.mode == "duplicate"
    mapping = load_mapping(TEMPLATE_ROOT / "qc_variable_mapping.json")
    assert "temperature" in mapping
    assert load_sensor_specs(TEMPLATE_ROOT / "gross_range_test" / "sensor_specs.json")
    assert load_variable_sensor_map(TEMPLATE_ROOT / "gross_range_test" / "variable_sensor_map.json")
    clim = load_station_climatology_config(
        TEMPLATE_ROOT / "climatology_test" / "station_climatology_config.json"
    )
    assert "deep_cast_limits" in clim
    assert load_station_depth_classification(
        TEMPLATE_ROOT / "climatology_test" / "station_depth_classification.json"
    )
    spike = load_spike_thresholds(TEMPLATE_ROOT / "spike_test" / "spike_thresholds.json")
    assert spike["sea_water_temperature"]["fail_threshold"] > 0
    roc = load_rate_of_change_thresholds(
        TEMPLATE_ROOT / "rate_of_change_test" / "rate_of_change_thresholds.json"
    )
    assert roc["sea_water_temperature"]["threshold"] > 0
    location = load_location_config(TEMPLATE_ROOT / "location_test" / "location_config.json")
    assert location["tolerance"] > 0
    flat = load_flat_line_config(TEMPLATE_ROOT / "flat_line_test" / "flat_line_config.json")
    assert flat["rep_cnt_fail"] >= flat["rep_cnt_suspect"]
