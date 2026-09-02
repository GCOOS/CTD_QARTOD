from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import xarray as xr

from dataset_profile import OutputConfig, default_profile
from qc_runner import run_qc_for_all


def _profile(data_root: Path, output_root: Path):
    return replace(
        default_profile(),
        data_root=data_root,
        output=OutputConfig(mode="duplicate", directory=output_root),
    )


def test_batch_writes_complete_manifest_and_history(
    minimal_profile_ds: xr.Dataset, tmp_path: Path
):
    data_root = tmp_path / "input"
    nc_path = data_root / "CRUISE" / "cast.nc"
    nc_path.parent.mkdir(parents=True)
    ds = minimal_profile_ds.copy()
    ds.attrs["history"] = "2024-01-01: source conversion"
    ds.to_netcdf(nc_path)
    profile = _profile(data_root, tmp_path / "output")

    summary = run_qc_for_all(profile=profile)

    payload = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert payload["counts"] == {
        "discovered": 1,
        "processed": 1,
        "written": 1,
        "failed": 0,
    }
    assert payload["input_root"] == str(data_root.resolve())
    assert payload["output_root"] == str((tmp_path / "output").resolve())
    assert payload["command"]
    assert payload["runtime"]["python"]
    assert len(payload["configuration"]["variable_mapping"]["sha256"]) == 64

    output_path = tmp_path / "output" / "CRUISE" / "cast.nc"
    with xr.open_dataset(output_path, decode_cf=False) as out:
        assert out.attrs["history"].startswith("2024-01-01: source conversion\n")
        assert "QARTOD QC applied by CTD_QARTOD" in out.attrs["history"]


def test_batch_writes_failed_manifest(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "input"
    nc_path = data_root / "CRUISE" / "cast.nc"
    nc_path.parent.mkdir(parents=True)
    nc_path.touch()
    profile = _profile(data_root, tmp_path / "output")
    monkeypatch.setattr(
        "qc_runner.run_qc_for_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("broken cast")),
    )

    with pytest.raises(RuntimeError, match="broken cast"):
        run_qc_for_all(profile=profile)

    manifest_path = tmp_path / "output" / "qc_run_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["counts"]["failed"] == 1
    assert payload["error"] == "RuntimeError: broken cast"
