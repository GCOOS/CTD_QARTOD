from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from dataset_profile import (
    DEFAULT_PROFILE_PATH,
    REPO_ROOT,
    DatasetProfile,
    align_for_profile_tests,
    default_profile,
    load_dataset_profile,
    resolve_config_path,
    resolve_sample_axis_index,
    restore_flags_shape,
)


def _profile_payload() -> dict[str, object]:
    return {
        "data_root": "datasets/example",
        "netcdf_global_attributes": {
            "institution": "Example Ocean Institute",
            "license": None,
        },
        "qc_test_modes": {
            "gap_test": "not_evaluated",
            "syntax_test": "not_evaluated",
        },
        "erddap": {
            "output_xml": "output/erddap/datasets.xml",
            "filedir_prefix": "/data/erddap/SFER_QC",
            "dataset_id_prefix": "",
            "required_global_attributes": ["title", "summary"],
            "global_add_attributes": {
                "_NCProperties": None,
                "infoUrl": "https://example.org/data",
            },
        },
    }


def test_profile_loads_human_netcdf_global_attributes(tmp_path: Path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(_profile_payload()), encoding="utf-8")

    profile = load_dataset_profile(profile_path)

    assert profile.netcdf_global_attributes == {
        "institution": "Example Ocean Institute",
        "license": None,
    }


def test_profile_rejects_nested_netcdf_global_attribute(tmp_path: Path):
    payload = _profile_payload()
    payload["netcdf_global_attributes"] = {
        "institution": {"name": "Example Ocean Institute"}
    }
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="netcdf_global_attributes.institution"):
        load_dataset_profile(profile_path)


def test_default_profile_loads_sfer_layout():
    profile = default_profile()
    with DEFAULT_PROFILE_PATH.open("r", encoding="utf-8") as f:
        profile_json = json.load(f)
    assert profile.data_root == REPO_ROOT / profile_json["data_root"]
    assert profile.output.mode == profile_json["output"]["mode"]
    assert profile.output.directory == REPO_ROOT / profile_json["output"]["directory"]
    assert profile.metadata.sample_dimension == "z"
    assert profile.metadata.depth == ("depth",)
    assert profile.qc_test_modes == {
        "gap_test": "not_evaluated",
        "syntax_test": "not_evaluated",
    }
    assert profile.erddap.output_xml == (
        REPO_ROOT / "output" / "erddap" / "datasets.xml"
    )
    assert profile.erddap.dataset_id_prefix == profile_json["erddap"]["dataset_id_prefix"]
    assert "title" in profile.erddap.required_global_attributes
    assert profile.erddap.global_add_attributes["_NCProperties"] is None


def test_profile_path_resolution(tmp_path: Path):
    p = tmp_path / "profile.json"
    payload = _profile_payload()
    payload["paths"] = {
        "variable_mapping": "config/variable_mapping/walton_mapping.json",
        "location_config": "config/location_test/location_config.json",
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    profile = load_dataset_profile(p)
    assert profile.data_root == REPO_ROOT / "datasets" / "example"
    assert resolve_config_path("variable_mapping", profile) == (
        REPO_ROOT / "config" / "variable_mapping" / "walton_mapping.json"
    )
    assert resolve_config_path("location_config", profile) == (
        REPO_ROOT / "config" / "location_test" / "location_config.json"
    )


def test_profile_requires_explicit_placeholder_test_modes(tmp_path: Path):
    payload = _profile_payload()
    del payload["qc_test_modes"]
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="qc_test_modes"):
        load_dataset_profile(profile_path)


@pytest.mark.parametrize("mode", ["disabled", "not-evaluated", "RUN"])
def test_profile_rejects_unknown_placeholder_test_mode(tmp_path: Path, mode: str):
    payload = _profile_payload()
    payload["qc_test_modes"]["gap_test"] = mode
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="gap_test"):
        load_dataset_profile(profile_path)


def test_profile_requires_erddap_policy(tmp_path: Path):
    payload = _profile_payload()
    del payload["erddap"]
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="erddap"):
        load_dataset_profile(profile_path)


def test_profile_rejects_null_erddap_output(tmp_path: Path):
    payload = _profile_payload()
    payload["erddap"]["output_xml"] = None
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="output_xml"):
        load_dataset_profile(profile_path)


def test_resolve_config_path_requires_profile_entry():
    with pytest.raises(ValueError, match="paths.variable_mapping"):
        resolve_config_path("variable_mapping", DatasetProfile())


def test_align_for_profile_tests_noop_when_z_last():
    arr = np.arange(6).reshape(1, 6)
    aligned, moved_axis = align_for_profile_tests(arr, ("profile", "z"), "z")
    assert moved_axis is None
    assert np.array_equal(aligned, arr)
    restored = restore_flags_shape(aligned, arr.shape, moved_axis)
    assert np.array_equal(restored, arr)


def test_align_for_profile_tests_moves_z_to_last_and_restores():
    arr = np.arange(6).reshape(6, 1)
    aligned, moved_axis = align_for_profile_tests(arr, ("z", "profile"), "z")
    assert moved_axis == 0
    assert aligned.shape == (1, 6)
    restored = restore_flags_shape(aligned, arr.shape, moved_axis)
    assert restored.shape == arr.shape
    assert np.array_equal(restored, arr)


def test_resolve_sample_axis_index_errors_on_missing_dim():
    try:
        resolve_sample_axis_index(("profile", "depth"), "z")
    except ValueError as e:
        assert "Sample dimension" in str(e)
    else:
        raise AssertionError("expected missing sample dimension to fail")
