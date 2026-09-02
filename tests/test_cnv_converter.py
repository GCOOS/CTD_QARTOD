"""Filename-driven CNV conversion tests for the confirmed source format."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from cnv_converter import convert_cnv
from cnv_mapping import inspect_cnv


def _cnv(
    path: Path,
    *,
    start_time: str = "Sep 18 2024 12:21:12",
    embedded_name: str = "different.hex",
) -> Path:
    columns = [
        ("timeS", "Time, Elapsed [seconds]"),
        ("latitude", "Latitude [deg]"),
        ("longitude", "Longitude [deg]"),
        ("depSM", "Depth [salt water, m]"),
        ("t090C", "Temperature [ITS-90, deg C]"),
        ("t090C", "Temperature [ITS-90, deg C]"),
        ("flag", "Sea-Bird source flag"),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "* Sea-Bird SBE 9 Data File:",
                f"* FileName = C:\\source\\{embedded_name}",
                f"# nquan = {len(columns)}",
                "# nvalues = 2",
                *(
                    f"# name {index} = {name}: {description}"
                    for index, (name, description) in enumerate(columns)
                ),
                "# interval = seconds: 1",
                f"# start_time = {start_time} [System UTC, header]",
                "# bad_flag = -9.990e-29",
                "# <Sensors count=\"2\" >",
                "#   <sensor Channel=\"1\"><TemperatureSensor><SerialNumber>T1</SerialNumber></TemperatureSensor></sensor>",
                "#   <sensor Channel=\"2\"><TemperatureSensor><SerialNumber>T2</SerialNumber></TemperatureSensor></sensor>",
                "# </Sensors>",
                "*END*",
                "0 27.1 -82.1 1 10 11 0",
                "1 27.2 -82.2 2 12 13 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _ready_mapping(source: Path, path: Path) -> Path:
    data = inspect_cnv(source, path)
    data["science_variables"]["t090C"].update(
        {
            "action": "map",
            "target_name": "temperature",
            "attributes": {
                "long_name": "Sea Water Temperature",
                "standard_name": "sea_water_temperature",
            },
            "sensor_tag": "TemperatureSensor",
        }
    )
    data["science_variables"]["flag"].update(
        {"action": "ignore", "target_name": None}
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_conversion_maps_duplicate_sources_then_suffixes_destination(tmp_path: Path):
    source = _cnv(tmp_path / "input" / "WS24258_Stn.054b.cnv")
    mapping = _ready_mapping(source, tmp_path / "mapping.json")
    output = tmp_path / "output"

    report = convert_cnv(source, output, mapping)

    target = output / "WS24258" / "WS24258_54b.nc"
    assert report["counts"] == {"cnv_discovered": 1, "converted": 1, "failed": 0}
    assert target.is_file()
    assert not (output / "WS24258" / "WS24258_54-2.nc").exists()
    assert "embedded filename differs from actual filename" in report["records"][0]["warnings"]
    with xr.open_dataset(target, decode_cf=False) as dataset:
        assert dataset["time"].dims == ("profile", "z")
        assert dataset["latitude"].dims == ("profile", "z")
        assert dataset["longitude"].dims == ("profile", "z")
        np.testing.assert_array_equal(dataset["temperature"].values[0], [10, 12])
        np.testing.assert_array_equal(dataset["temperature_2"].values[0], [11, 13])
        assert dataset["temperature"].attrs["source_name"] == "t090C"
        assert dataset["temperature_2"].attrs["source_occurrence"] == 2
        assert dataset["temperature"].attrs["instrument"] == "instrument1"
        assert dataset["temperature_2"].attrs["instrument"] == "instrument2"
        assert set(dataset["station"].values.ravel()) == {"54b"}
        assert set(dataset["cruiseID"].values.ravel()) == {"WS24258"}


def test_recursive_conversion_numbers_only_identical_station_ids(tmp_path: Path):
    root = tmp_path / "input"
    early = _cnv(
        root / "first" / "WS24258_Stn.054.cnv",
        start_time="Sep 18 2024 12:21:12",
    )
    _cnv(
        root / "second" / "WS24258_Stn.054.cnv",
        start_time="Sep 19 2024 12:21:12",
    )
    mapping = _ready_mapping(early, tmp_path / "mapping.json")

    report = convert_cnv(root, tmp_path / "output", mapping)

    assert report["counts"]["converted"] == 2
    assert (tmp_path / "output" / "WS24258" / "WS24258_54.nc").is_file()
    assert (tmp_path / "output" / "WS24258" / "WS24258_54-2.nc").is_file()


def test_conversion_rejects_mapping_that_still_needs_review(tmp_path: Path):
    source = _cnv(tmp_path / "WS24258_Stn.001.cnv")
    mapping = tmp_path / "mapping.json"
    data = inspect_cnv(source, mapping)
    data["science_variables"]["t090C"].update(
        {"action": "review", "target_name": None, "attributes": {}}
    )
    mapping.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="still requires review"):
        convert_cnv(source, tmp_path / "output", mapping)
