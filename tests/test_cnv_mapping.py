"""Source-keyed CNV mapping inspection and validation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cnv_mapping import inspect_cnv, load_cnv_mapping


def _cnv(
    path: Path,
    *,
    duplicate_temperature: bool = False,
    include_conductivity: bool = False,
    temperature_description: str = "Temperature [ITS-90, deg C]",
) -> Path:
    science = [
        ("depSM", "Depth [salt water, m]"),
        ("t090C", temperature_description),
    ]
    if duplicate_temperature:
        science.append(("t090C", temperature_description))
    if include_conductivity:
        science.append(("c0S/m", "Conductivity [S/m]"))
    science.append(("flag", "Sea-Bird source flag"))
    columns = [
        ("timeS", "Time, Elapsed [seconds]"),
        ("latitude", "Latitude [deg]"),
        ("longitude", "Longitude [deg]"),
        *science,
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "* Sea-Bird SBE 9 Data File:",
                "* FileName = C:\\source\\different.hex",
                f"# nquan = {len(columns)}",
                "# nvalues = 1",
                *(
                    f"# name {index} = {name}: {description}"
                    for index, (name, description) in enumerate(columns)
                ),
                "# interval = seconds: 1",
                "# start_time = Sep 18 2024 12:21:12 [System UTC, header]",
                "# bad_flag = -9.990e-29",
                '# <Sensors count="1" >',
                '#   <sensor Channel="1"><TemperatureSensor><SerialNumber>T1</SerialNumber></TemperatureSensor></sensor>',
                "# </Sensors>",
                "*END*",
                " ".join(str(index) for index in range(len(columns))),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_inspect_cnv_collects_recursive_source_inventory(tmp_path: Path):
    root = tmp_path / "input"
    _cnv(
        root / "WS24258" / "WS24258_Stn.001.cnv",
        duplicate_temperature=True,
    )
    _cnv(
        root / "WS25001" / "WS25001_Stn.002.cnv",
        include_conductivity=True,
    )
    output = tmp_path / "cnv_mapping.json"

    mapping = inspect_cnv(root, output)

    assert mapping["schema_version"] == 4
    assert mapping["inspection"]["sensor_parse_status"] == {"parsed": 2}
    assert mapping["inspection"]["sensor_tags"] == {
        "TemperatureSensor": {
            "file_count": 2,
            "max_occurrences_per_file": 1,
        }
    }
    assert mapping["required_fields"] == {
        "time": {"source_name": "timeS", "target_name": "time"},
        "longitude": {"source_name": "longitude", "target_name": "longitude"},
        "latitude": {"source_name": "latitude", "target_name": "latitude"},
    }
    assert mapping["vertical_field"] == {
        "source_name": "depSM",
        "source_occurrence": 1,
        "target_name": "depth",
    }
    assert set(mapping["science_variables"]) == {"c0S/m", "flag", "t090C"}
    assert mapping["science_variables"]["t090C"] == {
        "observed": {
            "file_count": 2,
            "max_occurrences_per_file": 2,
            "units": ["deg C"],
            "descriptions": ["Temperature [ITS-90, deg C]"],
        },
        "action": "map",
        "target_name": "temperature",
        "attributes": {
            "long_name": "Temperature",
            "units": "deg C",
            "standard_name": None,
            "ioos_category": None,
            "ncei_name": None,
        },
        "sensor_tag": None,
    }
    assert json.loads(output.read_text(encoding="utf-8")) == mapping


def test_inspect_cnv_accepts_one_file(tmp_path: Path):
    source = _cnv(tmp_path / "WS24258_Stn.054b.cnv")
    output = tmp_path / "mapping.json"

    mapping = inspect_cnv(source, output)
    loaded = load_cnv_mapping(output)

    assert mapping["science_variables"]["t090C"]["observed"]["file_count"] == 1
    assert mapping["science_variables"]["flag"]["attributes"]["units"] is None
    assert loaded.science_variables["t090C"].attributes == {
        "long_name": "Temperature",
        "units": "deg C",
    }
    assert loaded.science_variables["t090C"].sensor_tag is None


def test_mapping_requires_review_for_conflicting_descriptions(tmp_path: Path):
    root = tmp_path / "input"
    _cnv(root / "WS24258_Stn.054b.cnv")
    _cnv(
        root / "WS24258_Stn.055.cnv",
        temperature_description="Potential Temperature [ITS-90, deg C]",
    )
    path = tmp_path / "mapping.json"
    data = inspect_cnv(root, path)

    assert data["science_variables"]["t090C"]["action"] == "review"

    with pytest.raises(ValueError, match="still requires review"):
        load_cnv_mapping(path)


def test_mapping_ignores_processing_suffix_differences(tmp_path: Path):
    root = tmp_path / "input"
    _cnv(
        root / "WS24258_Stn.054b.cnv",
        temperature_description="Temperature [ITS-90, deg C], WS = 0.5",
    )
    _cnv(
        root / "WS24258_Stn.055.cnv",
        temperature_description="Temperature [ITS-90, deg C], WS = 2",
    )

    data = inspect_cnv(root, tmp_path / "mapping.json")

    assert data["science_variables"]["t090C"] == {
        "observed": {
            "file_count": 2,
            "max_occurrences_per_file": 1,
            "units": ["deg C"],
            "descriptions": [
                "Temperature [ITS-90, deg C], WS = 0.5",
                "Temperature [ITS-90, deg C], WS = 2",
            ],
        },
        "action": "map",
        "target_name": "temperature",
        "attributes": {
            "long_name": "Temperature",
            "units": "deg C",
            "standard_name": None,
            "ioos_category": None,
            "ncei_name": None,
        },
        "sensor_tag": None,
    }


def test_inspection_reports_header_only_cnv_as_failed(tmp_path: Path):
    root = tmp_path / "input"
    _cnv(root / "WS24258_Stn.001.cnv")
    incomplete = _cnv(root / "WS24258_Stn.002.cnv")
    text = incomplete.read_text(encoding="utf-8")
    text = "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith(("# nquan = ", "# nvalues = "))
    )
    incomplete.write_text(text + "\n", encoding="utf-8")

    data = inspect_cnv(root, tmp_path / "mapping.json")

    assert data["inspection"]["inspected_file_count"] == 1
    assert data["inspection"]["failed_files"] == [
        {
            "source": str(incomplete),
            "reason": (
                "WS24258_Stn.002.cnv: nquan does not match column declarations"
            ),
        }
    ]


def test_mapping_loads_mapped_and_ignored_sources(tmp_path: Path):
    source = _cnv(tmp_path / "WS24258_Stn.054b.cnv")
    path = tmp_path / "mapping.json"
    data = inspect_cnv(source, path)
    data["science_variables"]["t090C"].update(
        {
            "action": "map",
            "target_name": "temperature",
            "attributes": {"standard_name": "sea_water_temperature"},
            "sensor_tag": "TemperatureSensor",
        }
    )
    data["science_variables"]["flag"].update(
        {"action": "ignore", "target_name": None}
    )
    path.write_text(json.dumps(data), encoding="utf-8")

    mapping = load_cnv_mapping(path)

    assert mapping.science_variables["t090C"].target_name == "temperature"
    assert mapping.science_variables["t090C"].attributes == {
        "standard_name": "sea_water_temperature"
    }
    assert mapping.science_variables["t090C"].sensor_tag == "TemperatureSensor"
    assert mapping.science_variables["flag"].action == "ignore"
