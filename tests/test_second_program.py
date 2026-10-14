"""A second Sea-Bird program can run from dataset config only."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import xarray as xr

from cnv_converter import convert_cnv, pressure_to_depth
from cnv_mapping import inspect_cnv
from dataset_profile import load_dataset_profile
from station_classification import parse_station_classification
from station_identity import load_station_identity


def _program_cnv(
    path: Path,
    *,
    station: str,
    pressures: tuple[float, float],
    start_julian: float = 262.5,
) -> Path:
    columns = [
        ("timeJ", "Time, Julian [days]"),
        ("latitude", "Latitude [deg]"),
        ("longitude", "Longitude [deg]"),
        ("prDM", "Pressure, Digiquartz [db]"),
        ("t090C", "Temperature [ITS-90, deg C]"),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    second = start_julian + 1.0 / 86400.0
    path.write_text(
        "\n".join(
            [
                "* Sea-Bird SBE 19plus Data File:",
                f"* FileName = C:\\source\\SYN2024_Stn.{station}.hex",
                f"# nquan = {len(columns)}",
                "# nvalues = 2",
                *(
                    f"# name {index} = {name}: {description}"
                    for index, (name, description) in enumerate(columns)
                ),
                "# interval = seconds: 1",
                "# start_time = Sep 18 2024 12:21:12 [System UTC, header]",
                "# bad_flag = -9.990e-29",
                "# <Sensors count=\"1\" >",
                "#   <sensor Channel=\"1\"><TemperatureSensor><SerialNumber>T1</SerialNumber></TemperatureSensor></sensor>",
                "# </Sensors>",
                "*END*",
                f"{start_julian} 27.1 -82.1 {pressures[0]} 10",
                f"{second} 27.1 -82.1 {pressures[1]} 12",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _identity(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cruise_pattern": r"(?P<cruise>(?P<platform>SYN)\d{4})",
                "filename_patterns": [
                    {
                        "name": "cruise_station",
                        "pattern": r"{cruise}_Stn.(?P<station>[A-Za-z0-9]+)",
                    }
                ],
                "exclusion_patterns": [],
                "strip_run_number": False,
                "uppercase_cruise": True,
                "normalize": {"strip_leading_zeros": False, "decimal_separator": "."},
                "aliases": {},
                "overrides": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_second_program_uses_timej_prdm_and_rule_classes(tmp_path: Path):
    source = _program_cnv(tmp_path / "input" / "SYN2024_Stn.SHELF.cnv", station="SHELF", pressures=(5.0, 8.0))
    mapping_path = tmp_path / "mapping.json"
    mapping = inspect_cnv(source, mapping_path)

    assert mapping["required_fields"]["time"]["source_name"] == "timeJ"
    assert mapping["vertical_field"]["source_name"] == "prDM"
    assert "timeJ" not in mapping["science_variables"]
    assert "prDM" not in mapping["science_variables"]

    identity = load_station_identity(_identity(tmp_path / "station_identity.json"))
    publication = load_dataset_profile(
        Path(__file__).resolve().parents[1] / "config_template" / "dataset_profile.json"
    ).publication
    report = convert_cnv(
        source,
        tmp_path / "output",
        mapping_path,
        identity=identity,
        publication=publication,
    )
    assert report["counts"]["converted"] == 1
    target = tmp_path / "output" / "SYN2024" / "SYN2024_SHELF.nc"
    with xr.open_dataset(target, decode_cf=False) as dataset:
        np.testing.assert_allclose(dataset["time"].values[0], [0.0, 1.0], atol=1e-6)
        expected = pressure_to_depth(np.array([5.0, 8.0]), 27.1)
        np.testing.assert_allclose(dataset["depth"].values[0], expected)
        assert dataset["depth"].attrs["units"] == "m"
        assert dataset.attrs["id"] == "SYN2024_SHELF"

    classification = parse_station_classification(
        {
            "classes": {"shelf": {"stations": []}, "slope": {"stations": []}},
            "rules": [
                {"class": "shelf", "max_cast_max_depth": 15},
                {"class": "slope", "min_cast_max_depth": 15},
            ],
        }
    )
    with xr.open_dataset(target, decode_cf=False) as dataset:
        assert (
            classification.classify(
                str(dataset["station"].values[0, 0]),
                max_depth=float(np.nanmax(dataset["depth"].values)),
                latitude=float(dataset["latitude"].values[0, 0]),
                longitude=float(dataset["longitude"].values[0, 0]),
            )
            == "shelf"
        )

    deep = _program_cnv(
        tmp_path / "input" / "SYN2024_Stn.SLOPE.cnv",
        station="SLOPE",
        pressures=(20.0, 40.0),
    )
    inspect_cnv(deep, tmp_path / "deep_mapping.json")
    convert_cnv(
        deep,
        tmp_path / "output",
        tmp_path / "deep_mapping.json",
        identity=identity,
        publication=publication,
    )
    with xr.open_dataset(tmp_path / "output" / "SYN2024" / "SYN2024_SLOPE.nc", decode_cf=False) as dataset:
        assert (
            classification.classify(
                "SLOPE",
                max_depth=float(np.nanmax(dataset["depth"].values)),
                latitude=27.1,
                longitude=-82.1,
            )
            == "slope"
        )


def test_inspect_prefers_times_and_depsm_when_both_exist(tmp_path: Path):
    from test_cnv_converter import _cnv

    mapping = inspect_cnv(_cnv(tmp_path / "WS24258_Stn.001.cnv"), tmp_path / "mapping.json")
    assert mapping["required_fields"]["time"]["source_name"] == "timeS"
    assert mapping["vertical_field"]["source_name"] == "depSM"
