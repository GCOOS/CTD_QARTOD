from __future__ import annotations

import json

import numpy as np
import pytest
import xarray as xr
from lxml import etree

from dataset_profile import MetadataConfig
from xml_generator.config import ErddapConfig
from xml_generator.generate import generate_erddap_xml


def test_generate_erddap_xml_builds_standalone_dataset_from_netcdf(tmp_path):
    data_root = tmp_path / "qc"
    nc_path = data_root / "CRUISE_A" / "cast-01.nc"
    nc_path.parent.mkdir(parents=True)
    dataset = xr.Dataset(
        {
            "oxygen_qc_agg": ("z", np.array([1, 3], dtype=np.int8)),
            "station": ([], "ST01"),
            "longitude": ("z", np.array([-81.0, -81.1])),
            "oxygen": ("z", np.array([4.0, 4.1], dtype=np.float32)),
            "profile": ([], "cast-01"),
            "time": ("z", np.array([1000.0, 1001.0])),
            "time_elapsed": ("z", np.array([0.0, 1.0])),
            "latitude": ("z", np.array([24.0, 24.1])),
            "cruiseID": ([], "CRUISE_A"),
            "depth": ("z", np.array([0.0, 1.0], dtype=np.float32)),
        },
        attrs={
            "title": "Cast title from NetCDF",
            "summary": "Cast summary from NetCDF",
            "institution": "Example institution",
            "publisher_name": "Example publisher",
            "publisher_email": "data@example.org",
            "license": "Example license",
            "time_coverage_start": "2026-01-02T03:04:05Z",
        },
    )
    dataset["oxygen"].attrs["ancillary_variables"] = "oxygen_qc_agg"
    dataset["time_elapsed"].attrs["units"] = "seconds"
    dataset["oxygen_qc_agg"].attrs.update(
        {
            "standard_name": "aggregate_quality_flag",
            "flag_values": np.array([1, 3], dtype=np.int8),
        }
    )
    dataset.to_netcdf(nc_path)

    output_xml = tmp_path / "datasets.xml"
    config = ErddapConfig(
        output_xml=output_xml,
        filedir_prefix="/data/erddap/qc",
        dataset_id_prefix="ctd_",
        required_global_attributes=(
            "title",
            "summary",
            "institution",
            "publisher_name",
            "publisher_email",
            "license",
        ),
        global_add_attributes={
            "_NCProperties": None,
            "infoUrl": "https://example.org/data",
        },
    )

    assert generate_erddap_xml(data_root, config, MetadataConfig()) == output_xml

    root = etree.parse(str(output_xml)).getroot()
    assert root.tag == "erddapDatasets"
    generated = root.find("dataset")
    assert generated is not None
    assert generated.get("type") == "EDDTableFromNcCFFiles"
    assert generated.get("datasetID") == "ctd_cast_01"
    assert generated.findtext("fileDir") == "/data/erddap/qc/CRUISE_A/"
    assert generated.findtext("fileNameRegex") == r"cast\-01\.nc"

    variables = {
        item.findtext("sourceName"): item for item in generated.findall("dataVariable")
    }
    assert "time" not in variables
    assert variables["time_elapsed"].findtext("destinationName") == "time"
    assert (
        variables["time_elapsed"].findtext("addAttributes/att[@name='time_precision']")
        == "1970-01-01T00:00:00.000Z"
    )
    assert (
        variables["time_elapsed"].findtext("addAttributes/att[@name='units']")
        == "seconds since 2026-01-02T03:04:05Z"
    )
    qc_attributes = {
        item.get("name"): item
        for item in variables["oxygen_qc_agg"].findall("addAttributes/att")
    }
    assert qc_attributes["ioos_category"].text == "Quality"
    assert qc_attributes["flag_values"].get("type") == "intList"
    assert qc_attributes["flag_values"].text == "1 3"

    global_attributes = {
        item.get("name"): item for item in generated.findall("addAttributes/att")
    }
    assert set(global_attributes) == {"_NCProperties", "infoUrl"}
    assert global_attributes["_NCProperties"].text == "null"
    assert global_attributes["infoUrl"].text == "https://example.org/data"
    assert not list(tmp_path.rglob("*.tmp"))


def test_generate_erddap_xml_rejects_empty_required_metadata(tmp_path):
    data_root = tmp_path / "qc"
    nc_path = data_root / "CRUISE_A" / "cast.nc"
    nc_path.parent.mkdir(parents=True)
    xr.Dataset({"time": ("z", np.array([0.0]))}).to_netcdf(nc_path)
    config = ErddapConfig(
        output_xml=tmp_path / "datasets.xml",
        filedir_prefix="/data/erddap/qc",
        required_global_attributes=("title",),
        global_add_attributes={"title": []},
    )

    with pytest.raises(ValueError, match="title"):
        generate_erddap_xml(data_root, config, MetadataConfig())

    assert not config.output_xml.exists()


def test_generate_erddap_xml_disambiguates_sanitized_dataset_ids(tmp_path):
    data_root = tmp_path / "qc"
    cruise_dir = data_root / "CRUISE_A"
    cruise_dir.mkdir(parents=True)
    for filename in ("cast-01.nc", "cast_01.nc"):
        xr.Dataset({"time": ("z", np.array([0.0]))}).to_netcdf(cruise_dir / filename)
    config = ErddapConfig(
        output_xml=tmp_path / "datasets.xml",
        filedir_prefix="/data/erddap/qc",
    )

    generate_erddap_xml(data_root, config, MetadataConfig())
    first_ids = [
        dataset.get("datasetID")
        for dataset in etree.parse(str(config.output_xml)).getroot().findall("dataset")
    ]
    generate_erddap_xml(data_root, config, MetadataConfig())
    second_ids = [
        dataset.get("datasetID")
        for dataset in etree.parse(str(config.output_xml)).getroot().findall("dataset")
    ]

    assert len(first_ids) == len(set(first_ids)) == 2
    assert second_ids == first_ids


def test_main_erddap_xml_command_uses_generator_profile(tmp_path):
    from main import main

    data_root = tmp_path / "qc"
    nc_path = data_root / "CRUISE_A" / "cast.nc"
    nc_path.parent.mkdir(parents=True)
    xr.Dataset(
        {"time": ("z", np.array([0.0]))},
        attrs={"title": "Cast title"},
    ).to_netcdf(nc_path)
    output_xml = tmp_path / "datasets.xml"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "data_root": str(tmp_path / "source"),
                "output": {"mode": "duplicate", "directory": str(data_root)},
                "qc_test_modes": {
                    "gap_test": "not_evaluated",
                    "syntax_test": "not_evaluated",
                },
                "erddap": {
                    "output_xml": str(output_xml),
                    "filedir_prefix": "/data/erddap/qc",
                    "dataset_id_prefix": "",
                    "required_global_attributes": ["title"],
                    "global_add_attributes": {},
                },
            }
        ),
        encoding="utf-8",
    )

    main(["erddap-xml", "--profile", str(profile_path)])

    assert etree.parse(str(output_xml)).getroot().find("dataset") is not None
