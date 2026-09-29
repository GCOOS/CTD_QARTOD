import importlib

import numpy as np
import pytest
import xarray as xr


@pytest.fixture
def review(monkeypatch, repo_root):
    monkeypatch.syspath_prepend(str(repo_root / "soak_removal"))
    return importlib.import_module("review_app")


@pytest.mark.parametrize("nested", [False, True])
def test_review_loads_and_saves_both_directory_layouts(review, tmp_path, nested):
    root = tmp_path / "input"
    folder = "WS24256" if nested else "."
    source_dir = root / folder
    source_dir.mkdir(parents=True)
    source = source_dir / "WS24256_1.nc"
    xr.Dataset(
        {"sea_water_temperature": ("z", np.linspace(24, 20, 600))},
        coords={"depth": ("z", np.linspace(0, 15, 600))},
    ).to_netcdf(source)
    before = source.read_bytes()
    navigator = review.FileNavigator(root)
    assert navigator.get_folders() == [folder]
    assert navigator.get_files(folder) == [source.name]
    loaded = review.load_and_analyze_file(root, folder, source.name)
    assert "error" not in loaded
    assert loaded["total_scans"] == 600

    output = tmp_path / "output"
    result = review.save_trimmed_netcdf(root, output, folder, source.name, 100, 500)
    assert result["ok"], result
    with xr.open_dataset(output / folder / source.name) as saved:
        assert saved.sizes["z"] == 400
    assert source.read_bytes() == before

    app = review.create_app(root, output, output / "review_progress.json")
    client = app.server.test_client()
    assert client.get("/").status_code == 200
    layout = client.get("/_dash-layout")
    assert layout.status_code == 200
    assert source.name in layout.get_data(as_text=True)
