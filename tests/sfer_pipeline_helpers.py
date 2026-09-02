"""Helpers for SFER full-dataset pipeline integration tests."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import xarray as xr

from dataset_profile import DatasetProfile, OutputConfig, load_dataset_profile, resolve_config_path
from qc_data_loader import get_qc_variables, get_variable_category, load_mapping
from qc_runner import (
    _IMPLEMENTED_TESTS,
    _should_run_test,
    run_qc_for_all,
    run_qc_for_file,
)
from qc_writer import aggregate_qc_variable_name, qc_standard_name, qc_variable_name

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SFER_ROOT = REPO_ROOT / "datasets" / "SFER_CTD_SOAK_REMOVED"
DEFAULT_PROFILE_PATH = REPO_ROOT / "config" / "dataset_profile.json"
RESULTS_DIR = Path(__file__).resolve().parent / "sfer_pipeline_results"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "pipeline_sfer_output"


def sfer_data_root() -> Path:
    return DEFAULT_SFER_ROOT


def sfer_dataset_available() -> bool:
    root = sfer_data_root()
    if not root.is_dir():
        return False
    return any(root.glob("*/*.nc"))


def build_duplicate_profile(
    data_root: Path | None = None,
    output_dir: Path | None = None,
) -> DatasetProfile:
    """Profile that mirrors SFER tree under *output_dir* without touching sources."""
    root = Path(data_root) if data_root is not None else sfer_data_root()
    base = load_dataset_profile(DEFAULT_PROFILE_PATH)
    return DatasetProfile(
        data_root=root,
        metadata=base.metadata,
        output=OutputConfig(mode="duplicate", directory=output_dir or DEFAULT_OUTPUT_DIR),
        paths=base.paths,
        profile_path=base.profile_path,
    )


def run_kwargs(profile: DatasetProfile) -> dict[str, Any]:
    return {
        "mapping_path": resolve_config_path("variable_mapping", profile),
        "station_coords_csv": resolve_config_path("station_coords", profile),
        "station_climatology_config_path": resolve_config_path("station_climatology", profile),
        "station_depth_classification_path": resolve_config_path(
            "station_depth_classification", profile
        ),
        "sensor_specs_path": resolve_config_path("sensor_specs", profile),
        "variable_sensor_map_path": resolve_config_path("variable_sensor_map", profile),
        "spike_thresholds_path": resolve_config_path("spike_thresholds", profile),
        "rate_of_change_thresholds_path": resolve_config_path(
            "rate_of_change_thresholds", profile
        ),
        "profile": profile,
        "data_root": profile.data_root,
    }


def expected_qc_var_names(
    mapping: dict[str, list[str]],
    qc_vars: Iterable[str],
) -> list[str]:
    names: list[str] = []
    for var_name in qc_vars:
        category = get_variable_category(var_name, mapping)
        var_names = [aggregate_qc_variable_name(var_name)]
        for test_name in _IMPLEMENTED_TESTS:
            if _should_run_test(test_name, category):
                var_names.append(qc_variable_name(var_name, test_name))
        names.extend(var_names)
    return sorted(names)


def validate_qc_output(path: Path, mapping_path: Path) -> list[str]:
    """Return a list of validation error messages (empty if OK)."""
    errors: list[str] = []
    mapping = load_mapping(mapping_path)
    with xr.open_dataset(path) as ds:
        qc_vars = get_qc_variables(ds, mapping)
        if not qc_vars:
            errors.append(f"{path.name}: no mapped QC variables in file")
            return errors
        expected = expected_qc_var_names(mapping, qc_vars)
        missing = [name for name in expected if name not in ds.variables]
        if missing:
            errors.append(f"{path.name}: missing QC vars: {missing[:5]}{'...' if len(missing) > 5 else ''}")
        expected_by_var: dict[str, list[str]] = {}
        for var_name in qc_vars:
            category = get_variable_category(var_name, mapping)
            var_expected = [aggregate_qc_variable_name(var_name)]
            for test_name in _IMPLEMENTED_TESTS:
                if _should_run_test(test_name, category):
                    var_expected.append(qc_variable_name(var_name, test_name))
            expected_by_var[var_name] = var_expected
        for qc_name in expected:
            if qc_name not in ds.variables:
                continue
            arr = ds[qc_name]
            source_name = qc_name.rsplit("_qc_", 1)[0]
            if arr.dims != ds[source_name].dims:
                errors.append(f"{path.name}: {qc_name} dims mismatch")
            if arr.attrs.get("units") != "1":
                errors.append(f"{path.name}: {qc_name} missing units=1")
            if arr.attrs.get("flag_meanings") != "PASS NOT_EVALUATED SUSPECT FAIL MISSING":
                errors.append(f"{path.name}: {qc_name} unexpected flag_meanings")
            expected_standard = "aggregate_quality_flag"
            if not qc_name.endswith("_qc_agg"):
                suffix = qc_name.rsplit("_qc_", 1)[1]
                test_name = next(
                    (
                        name
                        for name in _IMPLEMENTED_TESTS
                        if qc_variable_name(source_name, name) == qc_name
                    ),
                    None,
                )
                if test_name is not None:
                    expected_standard = qc_standard_name(test_name)
                else:
                    errors.append(f"{path.name}: cannot resolve QC suffix {suffix}")
            if arr.attrs.get("standard_name") != expected_standard:
                errors.append(f"{path.name}: {qc_name} unexpected standard_name")
        for var_name, var_expected in expected_by_var.items():
            ancillary = ds[var_name].attrs.get("ancillary_variables", "")
            if ancillary.split() != var_expected:
                errors.append(f"{path.name}: {var_name} ancillary_variables mismatch")
    return errors


@dataclass
class PipelineRunSummary:
    data_root: str
    output_dir: str
    files_processed: int
    files_failed: int
    failures: list[dict[str, str]]
    started_at: str
    finished_at: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def iter_sfer_nc_files(data_root: Path, *, max_files: int | None = None) -> list[Path]:
    files = sorted(data_root.glob("*/*.nc"))
    if max_files is not None:
        return files[:max_files]
    return files


def run_pipeline_on_files(
    nc_files: list[Path],
    profile: DatasetProfile,
) -> PipelineRunSummary:
    kwargs = run_kwargs(profile)
    started = datetime.now(timezone.utc).isoformat()
    failures: list[dict[str, str]] = []
    ok = 0
    for nc_path in nc_files:
        try:
            run_qc_for_file(nc_path, **kwargs)
            ok += 1
        except Exception as exc:
            rel = str(nc_path.relative_to(profile.data_root))
            failures.append({"file": rel, "error": repr(exc)})
    finished = datetime.now(timezone.utc).isoformat()
    return PipelineRunSummary(
        data_root=str(profile.data_root),
        output_dir=str(profile.output.directory),
        files_processed=ok,
        files_failed=len(failures),
        failures=failures,
        started_at=started,
        finished_at=finished,
    )


def run_full_sfer_pipeline(
    profile: DatasetProfile | None = None,
    *,
    write_summary: bool = True,
) -> PipelineRunSummary:
    prof = profile or build_duplicate_profile()
    started = datetime.now(timezone.utc).isoformat()
    failures: list[dict[str, str]] = []
    try:
        run_qc_for_all(base_dir=prof.data_root, profile=prof)
    except Exception as exc:
        failures.append({"file": "<run_qc_for_all>", "error": repr(exc)})
    finished = datetime.now(timezone.utc).isoformat()
    nc_files = iter_sfer_nc_files(prof.data_root)
    summary = PipelineRunSummary(
        data_root=str(prof.data_root),
        output_dir=str(prof.output.directory),
        files_processed=len(nc_files) - len(failures),
        files_failed=len(failures),
        failures=failures,
        started_at=started,
        finished_at=finished,
    )
    if write_summary:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "last_run.json").write_text(summary.to_json(), encoding="utf-8")
    return summary


def write_summary(summary: PipelineRunSummary, path: Path | None = None) -> Path:
    out = path or (RESULTS_DIR / "last_run.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(summary.to_json(), encoding="utf-8")
    return out
