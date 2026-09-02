"""End-to-end pipeline tests on datasets/SFER_CTD_SOAK_REMOVED (duplicate output)."""

from __future__ import annotations

from pathlib import Path

import pytest
import xarray as xr

from sfer_pipeline_helpers import (
    RESULTS_DIR,
    build_duplicate_profile,
    expected_qc_var_names,
    iter_sfer_nc_files,
    run_kwargs,
    run_pipeline_on_files,
    sfer_data_root,
    sfer_dataset_available,
    validate_qc_output,
    write_summary,
)
from dataset_profile import resolve_config_path
from qc_data_loader import get_qc_variables, load_mapping
from qc_runner import run_qc_for_file

pytestmark = pytest.mark.skipif(
    not sfer_dataset_available(),
    reason="SFER dataset not present at datasets/SFER_CTD_SOAK_REMOVED",
)


@pytest.fixture
def sfer_root() -> Path:
    return sfer_data_root()


@pytest.fixture
def sfer_duplicate_profile(sfer_root: Path, tmp_path: Path):
    return build_duplicate_profile(sfer_root, tmp_path / "qc_output")


def test_sfer_dataset_layout(sfer_root: Path):
    cruises = [p for p in sfer_root.iterdir() if p.is_dir()]
    assert len(cruises) >= 1
    nc_files = list(sfer_root.glob("*/*.nc"))
    assert len(nc_files) >= 1
    sample = nc_files[0]
    with xr.open_dataset(sample) as ds:
        assert "z" in ds.dims
        assert "station" in ds.variables
        assert "cruiseID" in ds.variables


def test_sfer_pipeline_smoke(sfer_duplicate_profile, tmp_path: Path):
    """Run QC on a small cross-section of cruises; validate duplicate outputs."""
    smoke_files: list[Path] = []
    for cruise_dir in sorted(sfer_duplicate_profile.data_root.iterdir()):
        if not cruise_dir.is_dir():
            continue
        nc_files = sorted(cruise_dir.glob("*.nc"))
        nc = nc_files[0] if nc_files else None
        if nc is not None:
            smoke_files.append(nc)
        if len(smoke_files) >= 5:
            break
    assert len(smoke_files) >= 3

    summary = run_pipeline_on_files(smoke_files, sfer_duplicate_profile)
    write_summary(summary, tmp_path / "smoke_summary.json")
    assert summary.files_failed == 0, summary.failures

    mapping_path = resolve_config_path("variable_mapping", sfer_duplicate_profile)
    for nc_path in smoke_files:
        rel = nc_path.relative_to(sfer_duplicate_profile.data_root)
        out_path = sfer_duplicate_profile.output.directory / rel
        assert out_path.exists()
        with xr.open_dataset(nc_path) as original:
            assert not any(v.endswith("_qc_gap") for v in original.variables)
        errors = validate_qc_output(out_path, mapping_path)
        assert errors == [], errors


def test_sfer_pipeline_single_file_expected_qc_vars(sfer_duplicate_profile):
    nc_path = iter_sfer_nc_files(sfer_duplicate_profile.data_root, max_files=1)[0]
    run_qc_for_file(nc_path, **run_kwargs(sfer_duplicate_profile))
    rel = nc_path.relative_to(sfer_duplicate_profile.data_root)
    out_path = sfer_duplicate_profile.output.directory / rel
    mapping = load_mapping(resolve_config_path("variable_mapping", sfer_duplicate_profile))
    with xr.open_dataset(out_path) as ds:
        qc_vars = get_qc_variables(ds, mapping)
        for name in expected_qc_var_names(mapping, qc_vars):
            assert name in ds.variables
        if "sea_water_temperature" in ds:
            ancillary = ds["sea_water_temperature"].attrs["ancillary_variables"].split()
            assert "sea_water_temperature_qc_agg" in ancillary
            assert "sea_water_temperature_qc" not in ancillary
            assert ds["sea_water_temperature_qc_spike"].attrs["standard_name"] == "spike_test_quality_flag"
            assert ds["sea_water_temperature_qc_agg"].attrs["standard_name"] == "aggregate_quality_flag"


@pytest.mark.slow
def test_sfer_pipeline_full_dataset(sfer_root: Path, tmp_path: Path):
    """
    Run the full QC pipeline on all SFER casts (duplicate mode).

    Skipped by default; run with: pytest tests/test_sfer_pipeline.py -m slow
    """
    out_dir = Path(__file__).resolve().parent / "pipeline_sfer_output"
    profile = build_duplicate_profile(sfer_root, out_dir)
    all_files = iter_sfer_nc_files(sfer_root)
    summary = run_pipeline_on_files(all_files, profile)
    write_summary(summary, RESULTS_DIR / "last_run.json")
    assert summary.files_failed == 0, summary.failures[:10]

    mapping_path = resolve_config_path("variable_mapping", profile)
    sample = all_files[:: max(1, len(all_files) // 20)][:20]
    for nc_path in sample:
        rel = nc_path.relative_to(sfer_root)
        out_path = out_dir / rel
        errors = validate_qc_output(out_path, mapping_path)
        assert errors == [], errors
