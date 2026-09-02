"""Regression tests for dissolved O2 vs O2 saturation Walton categories."""

from __future__ import annotations

from pathlib import Path

from qc_config import ALL_CATEGORIES, TEST_CATEGORIES, VARIABLE_MAPPING_JSON
from qc_data_loader import get_variable_category, load_mapping
from qc_runner import _should_run_test

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_oxygen_variables_use_split_categories():
    mapping = load_mapping(VARIABLE_MAPPING_JSON)
    assert get_variable_category("dissolved_oxygen", mapping) == "oxygen_dissolved_oxygen"
    assert get_variable_category("oxygen_saturation", mapping) == "oxygen_saturation"
    assert get_variable_category("oxygen_saturation_2", mapping) == "oxygen_saturation"


def test_climatology_applies_only_to_dissolved_oxygen_category():
    clim_cats = TEST_CATEGORIES["climatology_test"]
    assert "oxygen_dissolved_oxygen" in clim_cats
    assert "oxygen_saturation" not in clim_cats

    assert _should_run_test("climatology_test", "oxygen_dissolved_oxygen")
    assert not _should_run_test("climatology_test", "oxygen_saturation")


def test_saturation_still_runs_common_qc_tests():
    assert "oxygen_saturation" in ALL_CATEGORIES
    for test_name in ("spike_test", "rate_of_change_test", "gross_range_test", "flat_line_test"):
        assert _should_run_test(test_name, "oxygen_saturation")
