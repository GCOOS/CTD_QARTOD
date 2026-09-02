from __future__ import annotations

import numpy as np
import xarray as xr
from lxml import etree

from dataset_profile import DatasetProfile
from erddap_xml_sync import sync_xml


def _source_names(dataset: etree._Element) -> list[str]:
    return [str(dv.findtext("sourceName")) for dv in dataset.findall("dataVariable")]


def _write_basic_nc(path, variable: str = "oxygen", time_name: str = "time") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ds = xr.Dataset(
        {
            "profile": ([], path.stem),
            time_name: (["z"], np.array([1.0, 2.0], dtype=np.float64)),
            "latitude": (["z"], np.array([24.0, 24.1], dtype=np.float64)),
            "longitude": (["z"], np.array([-81.0, -81.1], dtype=np.float64)),
            "depth": (["z"], np.array([0.0, 1.0], dtype=np.float32)),
            variable: (["z"], np.array([4.0, 4.1], dtype=np.float32)),
        }
    )
    ds.attrs.update(
        {
            "title": f"Converted {path.stem}",
            "id": path.parent.name,
            "geospatial_lat_min": 24.0,
            "creator_name": "NetCDF creator must not replace the template",
            "not_converter_owned": "must not be published",
        }
    )
    ds.to_netcdf(path)


def _write_template(path) -> None:
    path.write_text(
        """<dataset type="EDDTableFromNcCFFiles" datasetID="template" active="true">
  <reloadEveryNMinutes>10080</reloadEveryNMinutes>
  <fileDir>/tmp/</fileDir>
  <fileNameRegex>template.nc</fileNameRegex>
  <!-- sourceAttributes>
    <att name="title">Template cast only</att>
  </sourceAttributes -->
  <addAttributes>
    <att name="title">Template title</att>
    <att name="creator_name">Template Creator</att>
    <att name="license">Template License</att>
    <att name="platform">Template Platform</att>
    <att name="platform_vocabulary">Template Platform Vocabulary</att>
  </addAttributes>
  <dataVariable>
    <sourceName>template_only</sourceName>
    <destinationName>template_only</destinationName>
    <dataType>String</dataType>
    <!-- sourceAttributes>
      <att name="long_name">Template variable only</att>
    </sourceAttributes -->
    <addAttributes>
      <att name="colorBarMaximum" type="double">1.0</att>
    </addAttributes>
  </dataVariable>
</dataset>
""",
        encoding="utf-8",
    )


def test_sync_xml_orders_profile_axes_data_variables_and_qc_flags(tmp_path):
    data_root = tmp_path / "SFER_QC"
    nc_path = data_root / "CRUISE_A" / "cast001.nc"
    nc_path.parent.mkdir(parents=True)

    ds = xr.Dataset(
        {
            "oxygen_qc_agg": (["z"], np.array([1, 1], dtype=np.int8)),
            "station": ([], "ST01"),
            "longitude": (["z"], np.array([-81.0, -81.1], dtype=np.float64)),
            "oxygen": (["z"], np.array([4.0, 4.1], dtype=np.float32)),
            "profile": ([], "CRUISE_A_ST01"),
            "time": (["z"], np.array([1.0, 2.0], dtype=np.float64)),
            "unpaired_qc": (["z"], np.array([1, 1], dtype=np.int8)),
            "oxygen_qc_spike": (["z"], np.array([1, 3], dtype=np.int8)),
            "time_elapsed": (["z"], np.array([0.0, 1.0], dtype=np.float64)),
            "latitude": (["z"], np.array([24.0, 24.1], dtype=np.float64)),
            "cruiseID": ([], "CRUISE_A"),
            "depth": (["z"], np.array([0.0, 1.0], dtype=np.float32)),
            "platform": ([], "ship"),
        }
    )
    ds["oxygen"].attrs["ancillary_variables"] = "oxygen_qc_agg oxygen_qc_spike"
    ds.attrs.update(
        {
            "title": "Converted legacy cast",
            "geospatial_lat_min": 24.0,
            "creator_name": "NetCDF creator must not replace XML",
            "not_converter_owned": "must not be published",
        }
    )
    ds.to_netcdf(nc_path)

    input_xml = tmp_path / "datasets.xml"
    input_xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<erddapDatasets>
  <dataset type="EDDTableFromNcCFFiles" datasetID="cast001">
    <fileDir>data/erddap/SFER_QC/CRUISE_A/</fileDir>
    <fileNameRegex>cast001.nc</fileNameRegex>
    <addAttributes>
      <att name="title">Old title</att>
      <att name="creator_name">Existing Creator</att>
    </addAttributes>
    <dataVariable>
      <sourceName>time</sourceName>
      <destinationName>time</destinationName>
      <dataType>double</dataType>
    </dataVariable>
    <dataVariable>
      <sourceName>time_elapsed</sourceName>
      <destinationName>time_elapsed</destinationName>
      <dataType>double</dataType>
      <addAttributes>
        <att name="colorBarMinimum" type="double">0.0</att>
      </addAttributes>
    </dataVariable>
    <dataVariable>
      <sourceName>oxygen</sourceName>
      <destinationName>dissolved_oxygen</destinationName>
      <dataType>float</dataType>
      <addAttributes>
        <att name="colorBarMaximum" type="double">8.0</att>
      </addAttributes>
    </dataVariable>
  </dataset>
</erddapDatasets>
""",
        encoding="utf-8",
    )

    sync_xml(
        input_xml=input_xml,
        output_xml=input_xml,
        data_root=data_root,
        filedir_prefix="/data/erddap/SFER_QC",
        dataset_id_prefix="SFER_CTD_",
        profile=DatasetProfile(),
    )

    root = etree.parse(str(input_xml)).getroot()
    dataset = root.find("dataset")
    assert dataset is not None
    assert dataset.get("datasetID") == "SFER_CTD_cast001"
    assert dataset.findtext("fileDir") == "/data/erddap/SFER_QC/CRUISE_A/"
    assert _source_names(dataset) == [
        "profile",
        "time_elapsed",
        "latitude",
        "longitude",
        "cruiseID",
        "station",
        "depth",
        "oxygen",
        "oxygen_qc_agg",
        "oxygen_qc_spike",
        "unpaired_qc",
        "platform",
    ]
    elapsed = dataset.find("dataVariable[sourceName='time_elapsed']")
    oxygen = dataset.find("dataVariable[sourceName='oxygen']")
    assert elapsed is not None
    assert oxygen is not None
    assert elapsed.findtext("destinationName") == "time"
    assert oxygen.findtext("destinationName") == "oxygen"
    assert elapsed.findtext("addAttributes/att[@name='colorBarMinimum']") == "0.0"
    assert oxygen.findtext("addAttributes/att[@name='colorBarMaximum']") == "8.0"
    precision = dataset.findall("dataVariable/addAttributes/att[@name='time_precision']")
    assert len(precision) == 1
    assert precision[0].text == "1970-01-01T00:00:00.000Z"
    assert precision[0].getparent().getparent() is elapsed

    global_attrs = {
        att.get("name"): att for att in dataset.findall("addAttributes/att")
    }
    assert global_attrs["title"].text == "Converted legacy cast"
    assert global_attrs["geospatial_lat_min"].get("type") == "double"
    assert global_attrs["geospatial_lat_min"].text == "24.0"
    assert global_attrs["creator_name"].text == "Existing Creator"
    assert "not_converter_owned" not in global_attrs
    assert not list(tmp_path.rglob("*.tmp"))


def test_sync_xml_creates_missing_datasets_from_template_by_default(tmp_path):
    data_root = tmp_path / "SFER_QC"
    _write_basic_nc(data_root / "CRUISE_A" / "cast001.nc")
    _write_basic_nc(
        data_root / "CRUISE_A" / "cast002.nc",
        variable="salinity",
        time_name="time_elapsed",
    )
    template_xml = tmp_path / "GenerateDatasetsXml.xml"
    _write_template(template_xml)

    input_xml = tmp_path / "datasets.xml"
    output_xml = tmp_path / "datasets.out.xml"
    input_xml.write_text(
        """<erddapDatasets>
<startBodyHtml5><![CDATA[keep me]]></startBodyHtml5>
  <dataset type="EDDTableFromNcCFFiles" datasetID="cast001">
    <fileDir>data/erddap/SFER_QC/CRUISE_A/</fileDir>
    <fileNameRegex>cast001.nc</fileNameRegex>
    <!-- sourceAttributes>
      <att name="title">Existing cast comment</att>
    </sourceAttributes -->
    <addAttributes>
      <att name="title">Old title</att>
      <att name="creator_name">Existing Creator</att>
      <att name="license">Existing License</att>
      <att name="platform">Existing Platform</att>
    </addAttributes>
    <dataVariable>
      <sourceName>oxygen</sourceName>
      <destinationName>oxygen</destinationName>
      <dataType>float</dataType>
    </dataVariable>
  </dataset>
<endBodyHtml5><![CDATA[also keep me]]></endBodyHtml5>
</erddapDatasets>
""",
        encoding="utf-8",
    )

    sync_xml(
        input_xml=input_xml,
        output_xml=output_xml,
        data_root=data_root,
        filedir_prefix="/data/erddap/SFER_QC",
        dataset_template_xml=template_xml,
        profile=DatasetProfile(),
    )

    text = output_xml.read_text(encoding="utf-8")
    assert "<startBodyHtml5><![CDATA[keep me]]></startBodyHtml5>" in text
    assert "<endBodyHtml5><![CDATA[also keep me]]></endBodyHtml5>" in text
    assert "Existing cast comment" in text
    assert "Template cast only" in text
    assert "Template variable only" not in text

    root = etree.parse(str(output_xml)).getroot()
    datasets = root.findall("dataset")
    assert [dataset.get("datasetID") for dataset in datasets] == ["cast001", "cast002"]
    assert datasets[1].findtext("fileDir") == "/data/erddap/SFER_QC/CRUISE_A/"
    assert datasets[1].findtext("fileNameRegex") == "cast002.nc"
    assert "salinity" in _source_names(datasets[1])
    for dataset, source_name, creator, license_text, platform in (
        (datasets[0], "time", "Existing Creator", "Existing License", "Existing Platform"),
        (datasets[1], "time_elapsed", "Template Creator", "Template License", "Template Platform"),
    ):
        time_variables = [
            dv
            for dv in dataset.findall("dataVariable")
            if dv.findtext("destinationName") == "time"
        ]
        assert len(time_variables) == 1
        assert time_variables[0].findtext("sourceName") == source_name
        precision = dataset.findall("dataVariable/addAttributes/att[@name='time_precision']")
        assert len(precision) == 1
        assert precision[0].text == "1970-01-01T00:00:00.000Z"
        assert precision[0].getparent().getparent() is time_variables[0]

        global_attrs = {
            att.get("name"): att for att in dataset.findall("addAttributes/att")
        }
        assert global_attrs["title"].text == f"Converted {dataset.get('datasetID')}"
        assert global_attrs["id"].text == "CRUISE_A"
        assert global_attrs["geospatial_lat_min"].get("type") == "double"
        assert global_attrs["geospatial_lat_min"].text == "24.0"
        assert global_attrs["creator_name"].text == creator
        assert global_attrs["license"].text == license_text
        assert global_attrs["platform"].text == platform
        assert "not_converter_owned" not in global_attrs
    assert not list(tmp_path.rglob("*.tmp"))


def test_sync_xml_can_skip_missing_dataset_creation(tmp_path):
    data_root = tmp_path / "SFER_QC"
    _write_basic_nc(data_root / "CRUISE_A" / "cast001.nc")
    _write_basic_nc(data_root / "CRUISE_A" / "cast002.nc")

    input_xml = tmp_path / "datasets.xml"
    output_xml = tmp_path / "datasets.out.xml"
    input_xml.write_text(
        """<erddapDatasets>
  <dataset type="EDDTableFromNcCFFiles" datasetID="cast001">
    <fileDir>data/erddap/SFER_QC/CRUISE_A/</fileDir>
    <fileNameRegex>cast001.nc</fileNameRegex>
  </dataset>
</erddapDatasets>
""",
        encoding="utf-8",
    )

    sync_xml(
        input_xml=input_xml,
        output_xml=output_xml,
        data_root=data_root,
        filedir_prefix="/data/erddap/SFER_QC",
        create_missing_datasets=False,
        profile=DatasetProfile(),
    )

    root = etree.parse(str(output_xml)).getroot()
    assert [dataset.get("datasetID") for dataset in root.findall("dataset")] == ["cast001"]

def test_sync_xml_removes_orphan_datasets_by_default(tmp_path):
    data_root = tmp_path / "SFER_QC"
    _write_basic_nc(data_root / "CRUISE_A" / "cast001.nc")

    input_xml = tmp_path / "datasets.xml"
    output_xml = tmp_path / "datasets.out.xml"
    input_xml.write_text(
        """<erddapDatasets>
  <dataset type="EDDTableFromNcCFFiles" datasetID="cast001">
    <fileDir>data/erddap/SFER_QC/CRUISE_A/</fileDir>
    <fileNameRegex>cast001.nc</fileNameRegex>
  </dataset>
  <dataset type="EDDTableFromNcCFFiles" datasetID="missing">
    <fileDir>data/erddap/SFER_QC/CRUISE_A/</fileDir>
    <fileNameRegex>missing.nc</fileNameRegex>
  </dataset>
</erddapDatasets>
""",
        encoding="utf-8",
    )

    sync_xml(
        input_xml=input_xml,
        output_xml=output_xml,
        data_root=data_root,
        filedir_prefix="/data/erddap/SFER_QC",
        profile=DatasetProfile(),
    )

    root = etree.parse(str(output_xml)).getroot()
    assert [dataset.get("datasetID") for dataset in root.findall("dataset")] == ["cast001"]
