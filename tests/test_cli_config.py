from __future__ import annotations

import pytest

from main import _parse_args


def test_inspect_cnv_cli_uses_one_input_and_one_mapping_output():
    args = _parse_args(
        ["inspect-cnv", "input", "--output", "cnv_mapping.json"]
    )

    assert args.input.name == "input"
    assert args.output.name == "cnv_mapping.json"


def test_convert_cnv_cli_has_no_identity_manifest_stage():
    args = _parse_args(
        [
            "convert-cnv",
            "input",
            "--mapping",
            "cnv_mapping.json",
            "--output",
            "output",
        ]
    )

    assert args.input.name == "input"
    assert args.mapping.name == "cnv_mapping.json"
    assert args.output.name == "output"
    assert not hasattr(args, "manifest")


def test_generate_sensor_config_uses_dataset_profile():
    args = _parse_args(
        ["generate-sensor-config", "--profile", "config/example/profile.json"]
    )

    assert args.profile == "config/example/profile.json"
    assert args.overwrite is False


def test_qc_help_hides_removed_config_override_flags(capsys):
    with pytest.raises(SystemExit) as exc:
        _parse_args(["qc", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    removed = [
        "--mapping-path",
        "--location-tolerance",
        "--station-coords",
        "--station-climatology-config",
        "--station-depth-classification",
        "--sensor-specs",
        "--variable-sensor-map",
        "--spike-thresholds",
        "--rate-of-change-thresholds",
        "--base-dir",
        "--sync-erddap-xml",
        "--erddap-input-xml",
        "--erddap-output-xml",
        "--erddap-filedir-prefix",
    ]
    for flag in removed:
        assert flag not in help_text
    assert "--profile" in help_text


def test_erddap_help_uses_profile_configuration_only(capsys):
    with pytest.raises(SystemExit) as exc:
        _parse_args(["erddap-xml", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--profile" in help_text
    assert "--verbose" in help_text
    for flag in (
        "--input-xml",
        "--output-xml",
        "--data-root",
        "--filedir-prefix",
        "--dataset-type",
        "--dataset-template-xml",
        "--dataset-id-prefix",
        "--no-create-missing-datasets",
        "--keep-orphan-datasets",
        "--in-place",
        "--no-preserve-erddap-ui",
    ):
        assert flag not in help_text


def test_viz_help_uses_profile_data_root(capsys):
    with pytest.raises(SystemExit) as exc:
        _parse_args(["viz", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--profile" in help_text
    assert "--data-root" not in help_text
