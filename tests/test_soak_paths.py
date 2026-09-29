from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from dataset_profile import DEFAULT_PROFILE_PATH


@pytest.fixture
def soak_profile(tmp_path: Path) -> tuple[Path, Path]:
    input_root = tmp_path / "profile_input"
    input_root.mkdir()
    profile = json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))
    profile["data_root"] = str(input_root)
    profile_path = tmp_path / "dataset_profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    return profile_path, input_root


def test_qc_input_override_preserves_shared_profile(soak_profile, tmp_path, monkeypatch):
    profile_path, original_root = soak_profile
    before = profile_path.read_bytes()
    trimmed_root = tmp_path / "trimmed"
    trimmed_root.mkdir()
    calls = []

    def run_qc(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(manifest_path=tmp_path / "manifest.json")

    monkeypatch.setattr(main, "run_qc_for_all", run_qc)
    main.main(["qc", "--profile", str(profile_path)])
    main.main(["qc", "--profile", str(profile_path), "--input-root", str(trimmed_root)])

    assert [call["base_dir"] for call in calls] == [original_root, trimmed_root]
    assert calls[0]["profile"] == calls[1]["profile"]
    assert profile_path.read_bytes() == before


@pytest.mark.parametrize("command", ["remove-soak", "review-soak"])
def test_soak_commands_use_profile_and_allow_input_override(
    command: str,
    soak_profile: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_path, profile_root = soak_profile
    override_root = tmp_path / "override_input"
    override_root.mkdir()
    output_root = tmp_path / "soak_output"
    calls: list[list[str]] = []
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0),
    )

    for expected_root, override in (
        (profile_root, []),
        (override_root, ["--input-root", str(override_root)]),
    ):
        main.main([
            command, "--profile", str(profile_path),
            "--output-root", str(output_root), *override,
        ])
        assert calls[-1][calls[-1].index("--input-root") + 1] == str(expected_root)
        assert calls[-1][calls[-1].index("--output-root") + 1] == str(output_root)
        if command == "review-soak":
            assert calls[-1][calls[-1].index("--progress-json") + 1] == str(output_root / "review_progress.json")


@pytest.mark.parametrize("command", ["remove-soak", "review-soak"])
def test_soak_output_cannot_be_inside_profile_input(
    command: str,
    soak_profile: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_path, input_root = soak_profile
    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: pytest.fail("soak script ran"))

    with pytest.raises(SystemExit, match="outside the input root"):
        main.main([
            command,
            "--profile", str(profile_path),
            "--output-root", str(input_root / "trimmed"),
        ])


@pytest.mark.parametrize("command", ["remove-soak", "review-soak"])
def test_soak_rejects_missing_input_root(
    command: str,
    soak_profile: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_path, input_root = soak_profile
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["data_root"] = str(input_root.parent / "missing_input")
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: pytest.fail("soak script ran"))

    with pytest.raises(SystemExit, match="Soak input root is not a directory"):
        main.main([
            command,
            "--profile", str(profile_path),
            "--output-root", str(input_root.parent / "trimmed"),
        ])


@pytest.mark.parametrize("command", ["remove-soak", "review-soak"])
@pytest.mark.parametrize("profile_name,input_override,expected_output", [
    ("WS24258_CNV", None, "WS24258_SOAK_REMOVED"),
    ("original", None, "original_SOAK_REMOVED"),
    ("WS24258_CNV", "WS24258_CNV/WS24258", "WS24258_SOAK_REMOVED/WS24258"),
    ("WS24258_CNV", "alternate_input", "WS24258_SOAK_REMOVED"),
])
def test_soak_default_output_and_review_state(
    command, profile_name, input_override, expected_output,
    soak_profile, tmp_path, monkeypatch,
):
    profile_path, _ = soak_profile
    profile_root = tmp_path / profile_name
    profile_root.mkdir()
    profile = json.loads(profile_path.read_text())
    profile["data_root"] = str(profile_root)
    profile_path.write_text(json.dumps(profile))
    before = profile_path.read_bytes()
    argv = [command, "--profile", str(profile_path)]
    if input_override:
        selected = tmp_path / input_override
        selected.mkdir(parents=True)
        argv += ["--input-root", str(selected)]
    calls = []
    monkeypatch.setattr(
        main.subprocess, "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0),
    )

    main.main(argv)

    output = tmp_path / expected_output
    forwarded = calls[0]
    assert forwarded[forwarded.index("--output-root") + 1] == str(output)
    if command == "review-soak":
        assert forwarded[forwarded.index("--progress-json") + 1] == str(output / "review_progress.json")
        assert forwarded[forwarded.index("--suspicious-export-jsonl") + 1] == str(output / "suspicious_casts.jsonl")
    assert profile_path.read_bytes() == before
    assert not output.exists()


def test_soak_default_output_rejects_input_ancestor(soak_profile, monkeypatch):
    profile_path, input_root = soak_profile
    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: pytest.fail("soak script ran"))
    with pytest.raises(SystemExit, match="outside the input root"):
        main.main([
            "remove-soak", "--profile", str(profile_path),
            "--input-root", str(input_root.parent),
        ])
