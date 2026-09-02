from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import xarray as xr
from lxml import etree

from dataset_profile import DatasetProfile, MetadataConfig, default_profile
from xml_generator.config import ErddapConfig

logger = logging.getLogger("xml_generator")

TIME_PRECISION = "1970-01-01T00:00:00.000Z"
DATASET_SETTINGS = (
    ("reloadEveryNMinutes", "10080"),
    ("updateEveryNMillis", "10000"),
    ("recursive", "true"),
    ("pathRegex", ".*"),
    ("metadataFrom", "last"),
    ("standardizeWhat", "0"),
    ("sortFilesBySourceNames", ""),
    ("fileTableInMemory", "false"),
)


def _normalize(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.item() if value.ndim == 0 else value.tolist()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _attribute_text(value: object) -> str:
    value = _normalize(value)
    if isinstance(value, list):
        return " ".join(_attribute_text(item) for item in value)
    if value is None:
        return "null"
    if isinstance(value, float):
        return "NaN" if np.isnan(value) else repr(value)
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return str(int(value))
    return str(value)


def _attribute_type(value: object) -> str | None:
    value = _normalize(value)
    if isinstance(value, list):
        if value and all(
            isinstance(item, int) and not isinstance(item, bool) for item in value
        ):
            return "intList" if len(value) > 1 else "int"
        if value and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in value
        ):
            return "doubleList" if len(value) > 1 else "double"
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "double"
    return None


def _append_attribute(parent: etree._Element, name: str, value: object) -> None:
    attribute = etree.SubElement(parent, "att", name=name)
    attribute_type = _attribute_type(value)
    if attribute_type:
        attribute.set("type", attribute_type)
    attribute.text = _attribute_text(value)


def _data_type(dtype: np.dtype) -> str:
    dtype = np.dtype(dtype)
    if dtype.kind in "USO":
        return "String"
    if dtype.kind == "b":
        return "byte"
    if dtype.kind == "i":
        return ("byte", "short", "int", "long")[
            min(3, dtype.itemsize.bit_length() - 1)
        ]
    if dtype.kind == "u":
        return {1: "short", 2: "int", 4: "long"}.get(dtype.itemsize, "String")
    if dtype.kind == "f":
        return "float" if dtype.itemsize <= 4 else "double"
    if dtype.kind == "M":
        return "double"
    return "String"


def _dataset_id(stem: str, prefix: str) -> str:
    dataset_id = prefix + re.sub(r"[^A-Za-z0-9_]+", "_", stem)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", dataset_id):
        raise ValueError(f"Invalid ERDDAP datasetID generated from {stem!r}: {dataset_id!r}")
    return dataset_id


def _first_present(
    names: Iterable[str],
    variables: Mapping[str, tuple[str, Mapping[str, object]]],
) -> str | None:
    return next((name for name in names if name in variables), None)


def _ordered_variables(
    variables: Mapping[str, tuple[str, Mapping[str, object]]],
    metadata: MetadataConfig,
) -> list[str]:
    names = list(variables)
    if "time_elapsed" in variables:
        if "time" in names:
            names.remove("time")
        time_name = "time_elapsed"
    else:
        time_name = _first_present(metadata.time, variables)
        if time_name is None:
            raise ValueError("NetCDF has neither time nor time_elapsed")

    ordered: list[str] = []
    for candidates in (
        ("profile",),
        (time_name,),
        metadata.latitude,
        metadata.longitude,
        (metadata.cruise_id,),
        (metadata.station,),
        metadata.depth,
    ):
        name = _first_present(candidates, variables)
        if name is not None and name in names and name not in ordered:
            ordered.append(name)

    ancillary = {
        name: [
            item
            for item in re.split(r"[\s,]+", str(attrs.get("ancillary_variables", "")).strip())
            if item in variables
        ]
        for name, (_, attrs) in variables.items()
    }
    referenced = {item for items in ancillary.values() for item in items}
    for name in names:
        if name in ordered or name in referenced or not ancillary[name]:
            continue
        ordered.append(name)
        ordered.extend(item for item in ancillary[name] if item not in ordered)
    ordered.extend(name for name in names if name not in ordered)
    return ordered


def _variable_element(
    name: str,
    data_type: str,
    attributes: Mapping[str, object],
) -> etree._Element:
    destination = "time" if name in {"time", "time_elapsed"} else name
    variable = etree.Element("dataVariable")
    etree.SubElement(variable, "sourceName").text = name
    etree.SubElement(variable, "destinationName").text = destination
    etree.SubElement(variable, "dataType").text = data_type
    additions = etree.SubElement(variable, "addAttributes")
    merged = {
        key: _normalize(value)
        for key, value in attributes.items()
        if key != "_ChunkSizes" and value is not None
    }
    merged.pop("time_precision", None)
    if destination == "time":
        merged["time_precision"] = TIME_PRECISION
    standard_name = str(merged.get("standard_name", ""))
    if not merged.get("ioos_category") and (
        "_qc" in name
        or standard_name == "aggregate_quality_flag"
        or standard_name.endswith("_quality_flag")
    ):
        merged["ioos_category"] = "Quality"
    for attribute_name in sorted(merged):
        _append_attribute(additions, attribute_name, merged[attribute_name])
    return variable


def _file_dir(data_root: Path, nc_path: Path, prefix: str) -> str:
    relative = nc_path.parent.relative_to(data_root).as_posix()
    suffix = "" if relative == "." else f"/{relative}"
    return f"{prefix.rstrip('/')}{suffix}/"


def _validate_globals(
    nc_path: Path,
    source: Mapping[str, object],
    config: ErddapConfig,
) -> None:
    combined = dict(source)
    for name, value in config.global_add_attributes.items():
        if value is None:
            combined.pop(name, None)
        else:
            combined[name] = value
    missing = [
        name
        for name in config.required_global_attributes
        if name not in combined
        or combined[name] is None
        or (isinstance(combined[name], str) and not combined[name].strip())
        or (isinstance(combined[name], (list, tuple, dict)) and not combined[name])
    ]
    if missing:
        raise ValueError(f"{nc_path} is missing required global attributes: {', '.join(missing)}")


def _prepare_legacy_time(
    nc_path: Path,
    global_attributes: Mapping[str, object],
    variables: Mapping[str, tuple[str, dict[str, object]]],
) -> None:
    if "time_elapsed" not in variables:
        return
    attributes = variables["time_elapsed"][1]
    units = str(attributes.get("units", "")).strip()
    if " since " in units.casefold():
        return
    unit = {
        "s": "seconds",
        "sec": "seconds",
        "second": "seconds",
        "seconds": "seconds",
        "min": "minutes",
        "minute": "minutes",
        "minutes": "minutes",
        "hour": "hours",
        "hours": "hours",
        "day": "days",
        "days": "days",
    }.get(units.casefold())
    origin = global_attributes.get("time_coverage_start")
    if unit is None or not isinstance(origin, str) or not origin.strip():
        raise ValueError(
            f"{nc_path} cannot publish time_elapsed as time without temporal units and time_coverage_start"
        )
    attributes["units"] = f"{unit} since {origin}"
    source_time = variables.get("time")
    attributes.setdefault(
        "calendar",
        source_time[1].get("calendar", "proleptic_gregorian")
        if source_time is not None
        else "proleptic_gregorian",
    )


def _dataset_element(
    data_root: Path,
    nc_path: Path,
    dataset_id: str,
    config: ErddapConfig,
    metadata: MetadataConfig,
) -> etree._Element:
    with xr.open_dataset(nc_path, decode_cf=False, mask_and_scale=False) as source:
        global_attributes = {name: _normalize(value) for name, value in source.attrs.items()}
        _validate_globals(nc_path, global_attributes, config)
        variables = {
            name: (
                _data_type(variable.dtype),
                {key: _normalize(value) for key, value in variable.attrs.items()},
            )
            for name, variable in source.variables.items()
        }
        _prepare_legacy_time(nc_path, global_attributes, variables)

    dataset = etree.Element(
        "dataset",
        type="EDDTableFromNcCFFiles",
        datasetID=dataset_id,
        active="true",
    )
    etree.SubElement(dataset, "reloadEveryNMinutes").text = DATASET_SETTINGS[0][1]
    etree.SubElement(dataset, "updateEveryNMillis").text = DATASET_SETTINGS[1][1]
    etree.SubElement(dataset, "fileDir").text = _file_dir(data_root, nc_path, config.filedir_prefix)
    etree.SubElement(dataset, "fileNameRegex").text = re.escape(nc_path.name)
    for tag, value in DATASET_SETTINGS[2:]:
        etree.SubElement(dataset, tag).text = value
    if config.global_add_attributes:
        additions = etree.SubElement(dataset, "addAttributes")
        for name in sorted(config.global_add_attributes):
            _append_attribute(additions, name, config.global_add_attributes[name])
    for name in _ordered_variables(variables, metadata):
        data_type, attributes = variables[name]
        dataset.append(_variable_element(name, data_type, attributes))
    return dataset


def _write_xml(root: etree._Element, output_xml: Path) -> None:
    output_xml.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_xml.parent,
            prefix=f".{output_xml.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        etree.ElementTree(root).write(
            str(temporary),
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )
        etree.parse(str(temporary))
        os.replace(temporary, output_xml)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def generate_erddap_xml(
    data_root: Path,
    config: ErddapConfig,
    metadata: MetadataConfig,
) -> Path:
    """Generate a standalone datasets.xml from every NetCDF below data_root."""

    data_root = Path(data_root)
    if not data_root.is_dir():
        raise NotADirectoryError(f"NetCDF data root does not exist: {data_root}")
    paths = sorted(data_root.rglob("*.nc"))
    if not paths:
        raise ValueError(f"No NetCDF files found in {data_root}")

    root = etree.Element("erddapDatasets")
    base_ids = [_dataset_id(path.stem, config.dataset_id_prefix) for path in paths]
    id_counts = Counter(base_ids)
    dataset_ids: set[str] = set()
    for path, base_id in zip(paths, base_ids, strict=True):
        dataset_id = base_id
        if id_counts[base_id] > 1:
            relative_path = path.relative_to(data_root).as_posix()
            suffix = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:8]
            dataset_id = f"{base_id}_{suffix}"
        dataset = _dataset_element(data_root, path, dataset_id, config, metadata)
        dataset_id = str(dataset.get("datasetID"))
        if dataset_id in dataset_ids:
            raise ValueError(f"Duplicate ERDDAP datasetID: {dataset_id}")
        dataset_ids.add(dataset_id)
        root.append(dataset)
    _write_xml(root, config.output_xml)
    return config.output_xml


def generate_erddap_xml_for_profile(
    profile: DatasetProfile | None = None,
    *,
    verbose: bool = False,
) -> Path:
    """Generate datasets.xml using the selected dataset profile."""

    selected = profile or default_profile()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    data_root = (
        selected.output.directory
        if selected.output.mode == "duplicate"
        else selected.data_root
    )
    output = generate_erddap_xml(data_root, selected.erddap, selected.metadata)
    logger.info("Generated ERDDAP XML: %s", output)
    return output
