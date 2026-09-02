"""Atomic provenance manifest for one batch QC run."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping

from dataset_profile import REPO_ROOT, DatasetProfile

RUNTIME_PACKAGES = (
    "xarray",
    "numpy",
    "ioos_qc",
    "netCDF4",
    "lxml",
)


@dataclass(frozen=True)
class QCRunSummary:
    manifest_path: Path
    discovered: int
    written_paths: tuple[Path, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else None


def _runtime_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {"python": platform.python_version()}
    for package in RUNTIME_PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    return versions


def build_qc_run_manifest(
    profile: DatasetProfile,
    input_root: Path,
    config_paths: Mapping[str, Path],
    discovered: int,
) -> tuple[Path, dict[str, Any]]:
    output_root = (
        profile.output.directory
        if profile.output.mode == "duplicate"
        else input_root
    )
    paths = dict(config_paths)
    if profile.profile_path is not None:
        paths["dataset_profile"] = profile.profile_path
    configuration = {
        name: {"path": str(path.resolve()), "sha256": _sha256(path)}
        for name, path in sorted(paths.items())
    }
    payload: dict[str, Any] = {
        "status": "running",
        "started_at": utc_now(),
        "completed_at": None,
        "command": list(sys.argv),
        "git_revision": _git_revision(),
        "runtime": _runtime_versions(),
        "input_root": str(input_root.resolve()),
        "output_root": str(output_root.resolve()),
        "configuration": configuration,
        "counts": {
            "discovered": discovered,
            "processed": 0,
            "written": 0,
            "failed": 0,
        },
        "error": None,
    }
    return output_root / "qc_run_manifest.json", payload


def write_qc_run_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace the manifest with the current run state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)
