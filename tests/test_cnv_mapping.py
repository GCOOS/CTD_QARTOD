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

    assert mapping["schema_version"] == 5
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
            "missing_units": False,
            "descriptions": ["Temperature [ITS-90, deg C]"],
        },
        "mapped_to": "sea_water_temperature",
    }
    assert json.loads(output.read_text(encoding="utf-8")) == mapping


def test_inspect_cnv_accepts_one_file(tmp_path: Path):
    source = _cnv(tmp_path / "WS24258_Stn.054b.cnv")
    output = tmp_path / "mapping.json"

    mapping = inspect_cnv(source, output)
    loaded = load_cnv_mapping(output)

    assert mapping["science_variables"]["t090C"]["observed"]["file_count"] == 1
    assert mapping["science_variables"]["flag"]["observed"]["units"] == []
    assert loaded.science_variables["t090C"].attributes == {
        "long_name": "Sea Water Temperature",
        "standard_name": "sea_water_temperature",
        "ioos_category": "Temperature",
        "ncei_name": "WATER TEMPERATURE",
    }
    assert loaded.science_variables["t090C"].sensor_tag == "TemperatureSensor"


def test_mapping_requires_review_for_conflicting_descriptions(tmp_path: Path):
    root = tmp_path / "input"
    _cnv(root / "WS24258_Stn.054b.cnv")
    _cnv(
        root / "WS24258_Stn.055.cnv",
        temperature_description="Potential Temperature [ITS-90, deg C]",
    )
    path = tmp_path / "mapping.json"
    data = inspect_cnv(root, path)

    assert data["science_variables"]["t090C"]["mapped_to"] is None

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
            "missing_units": False,
            "descriptions": [
                "Temperature [ITS-90, deg C], WS = 0.5",
                "Temperature [ITS-90, deg C], WS = 2",
            ],
        },
        "mapped_to": "sea_water_temperature",
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
    data["science_variables"]["flag"].update({"ignore": True, "mapped_to": None})
    path.write_text(json.dumps(data), encoding="utf-8")

    mapping = load_cnv_mapping(path)

    assert mapping.science_variables["t090C"].target_name == "sea_water_temperature"
    assert mapping.science_variables["t090C"].attributes["standard_name"] == "sea_water_temperature"
    assert "units" not in mapping.science_variables["t090C"].attributes
    assert mapping.science_variables["t090C"].sensor_tag == "TemperatureSensor"
    assert mapping.science_variables["flag"].action == "ignore"


@pytest.mark.parametrize("field,value", [("attributes", {"units": "K"}), ("sensor_tag", "TemperatureSensor"), ("action", "map")])
def test_dataset_mapping_cannot_duplicate_catalog_definitions(tmp_path, field, value):
    source = _cnv(tmp_path / "WS24258_Stn.001.cnv")
    path = tmp_path / "mapping.json"
    data = inspect_cnv(source, path)
    data["science_variables"]["t090C"][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="definitions belong in cnv_catalog"):
        load_cnv_mapping(path)


def test_conversion_resolves_current_catalog_without_reinspection(tmp_path, monkeypatch):
    from copy import deepcopy
    import cnv_catalog
    from cnv_converter import convert_cnv
    import xarray as xr

    source = _cnv(tmp_path / "WS24258_Stn.001.cnv")
    path = tmp_path / "mapping.json"
    inspect_cnv(source, path)
    original = path.read_bytes()
    definition = deepcopy(cnv_catalog.catalog()["variables"]["sea_water_temperature"])
    definition["long_name"] = "Catalog-owned updated label"
    monkeypatch.setitem(cnv_catalog.catalog()["variables"], "sea_water_temperature", definition)
    report = convert_cnv(source, tmp_path / "output", path)
    assert report["counts"]["converted"] == 1
    with xr.open_dataset(tmp_path / "output/WS24258/WS24258_1.nc", decode_cf=False) as ds:
        assert ds.sea_water_temperature.attrs["long_name"] == "Catalog-owned updated label"
        assert ds.sea_water_temperature.attrs["units"] == "degree_Celsius"
    assert path.read_bytes() == original


def test_mapping_rejects_unknown_catalog_target(tmp_path):
    source = _cnv(tmp_path / "WS24258_Stn.001.cnv")
    path = tmp_path / "mapping.json"
    data = inspect_cnv(source, path)
    data["science_variables"]["t090C"]["mapped_to"] = "not_a_measurement"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unknown catalog target"):
        load_cnv_mapping(path)
