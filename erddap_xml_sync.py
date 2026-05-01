#!/usr/bin/env python3
"""
Sync ERDDAP datasets XML from NetCDF files.

This script updates EDDTableFromNcCFFiles <dataset> blocks so that:
1) fileDir/fileNameRegex point to the selected data root layout, and
2) dataVariable entries match variables currently present in each NetCDF file
   (including QC variables like *_qc_*).

It can also optionally create missing dataset blocks for new NetCDF files.

** can't generate new xml for new datasets rn
"""

from __future__ import annotations

import argparse
import copy
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import xarray as xr
from lxml import etree


logger = logging.getLogger("erddap_xml_sync")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def erddap_data_type(dtype: np.dtype) -> str:
    """Map numpy dtype to ERDDAP dataType values."""
    kind = np.dtype(dtype).kind
    itemsize = np.dtype(dtype).itemsize

    if kind in ("U", "S", "O"):
        return "String"
    if kind == "b":
        return "byte"
    if kind == "i":
        if itemsize <= 1:
            return "byte"
        if itemsize <= 2:
            return "short"
        if itemsize <= 4:
            return "int"
        return "long"
    if kind == "u":
        if itemsize <= 1:
            return "short"
        if itemsize <= 2:
            return "int"
        if itemsize <= 4:
            return "long"
        return "String"
    if kind == "f":
        return "float" if itemsize <= 4 else "double"
    if kind == "M":
        return "double"
    return "String"


def safe_dataset_id(stem: str) -> str:
    """Convert file stem to a datasetID-safe token."""
    return re.sub(r"[^A-Za-z0-9_]+", "_", stem)


def scan_nc_files(data_root: Path) -> Dict[Tuple[str, str], Path]:
    """Index NetCDF files by (cruise_dir_name, filename)."""
    indexed: Dict[Tuple[str, str], Path] = {}
    for nc_path in sorted(data_root.glob("*/*.nc")):
        cruise = nc_path.parent.name
        indexed[(cruise, nc_path.name)] = nc_path
    return indexed


def read_nc_schema(nc_path: Path) -> List[Tuple[str, str]]:
    """Return ordered (variable_name, erddap_dataType) from a NetCDF file."""
    with xr.open_dataset(nc_path, decode_cf=False, mask_and_scale=False) as ds:
        result: List[Tuple[str, str]] = []
        for name in ds.variables:
            dtype = ds.variables[name].dtype
            result.append((name, erddap_data_type(dtype)))
        return result


def _get_child_text(elem: etree._Element, child_tag: str) -> str | None:
    child = elem.find(child_tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _ensure_child_text(elem: etree._Element, child_tag: str, value: str) -> etree._Element:
    child = elem.find(child_tag)
    if child is None:
        child = etree.SubElement(elem, child_tag)
    child.text = value
    return child


def _build_new_datavariable(source_name: str, data_type: str) -> etree._Element:
    dv = etree.Element("dataVariable")
    etree.SubElement(dv, "sourceName").text = source_name
    etree.SubElement(dv, "destinationName").text = source_name
    etree.SubElement(dv, "dataType").text = data_type

    add_attrs = etree.SubElement(dv, "addAttributes")
    att_comment = etree.SubElement(add_attrs, "att")
    att_comment.set("name", "comment")
    att_comment.text = "null"

    att_ioos = etree.SubElement(add_attrs, "att")
    att_ioos.set("name", "ioos_category")
    att_ioos.text = "Unknown"
    return dv


def _sync_datavariables_for_dataset(
    dataset_elem: etree._Element,
    nc_schema: Iterable[Tuple[str, str]],
) -> Tuple[int, int]:
    """
    Sync dataset dataVariable list to match NetCDF schema.

    Returns:
        (reused_existing_count, created_new_count)
    """
    existing_by_source: Dict[str, etree._Element] = {}
    old_dvs = list(dataset_elem.findall("dataVariable"))
    for dv in old_dvs:
        src = _get_child_text(dv, "sourceName")
        if src and src not in existing_by_source:
            existing_by_source[src] = dv

    for dv in old_dvs:
        dataset_elem.remove(dv)

    reused = 0
    created = 0
    for source_name, data_type in nc_schema:
        if source_name in existing_by_source:
            dv = copy.deepcopy(existing_by_source[source_name])
            _ensure_child_text(dv, "sourceName", source_name)
            _ensure_child_text(dv, "destinationName", source_name)
            _ensure_child_text(dv, "dataType", data_type)
            reused += 1
        else:
            dv = _build_new_datavariable(source_name, data_type)
            created += 1
        dataset_elem.append(dv)

    return reused, created


def _guess_cruise_from_filedir(file_dir: str | None) -> str | None:
    if not file_dir:
        return None
    p = Path(file_dir.strip().strip("/"))
    if not p.parts:
        return None
    return p.parts[-1]


def _create_dataset_from_template(
    template: etree._Element,
    nc_path: Path,
    filedir_prefix: str,
) -> etree._Element:
    dataset = copy.deepcopy(template)
    dataset.set("datasetID", safe_dataset_id(nc_path.stem))
    _ensure_child_text(dataset, "fileDir", f"{filedir_prefix.rstrip('/')}/{nc_path.parent.name}/")
    _ensure_child_text(dataset, "fileNameRegex", nc_path.name)
    return dataset


def sync_xml(
    input_xml: Path,
    output_xml: Path,
    data_root: Path,
    filedir_prefix: str,
    dataset_type: str = "EDDTableFromNcCFFiles",
    create_missing_datasets: bool = False,
) -> None:
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(str(input_xml), parser)
    root = tree.getroot()

    if root.tag != "erddapDatasets":
        raise ValueError(f"Unexpected root tag: {root.tag}")

    nc_index = scan_nc_files(data_root)
    if not nc_index:
        raise ValueError(f"No NetCDF files found in {data_root}")

    matched_keys: set[Tuple[str, str]] = set()
    datasets = [d for d in root.findall("dataset") if d.get("type") == dataset_type]
    template_dataset = datasets[0] if datasets else None

    nc_schema_cache: Dict[Path, List[Tuple[str, str]]] = {}

    updated_count = 0
    skipped_count = 0
    created_count = 0
    reused_vars_total = 0
    created_vars_total = 0

    for dataset in datasets:
        file_dir = _get_child_text(dataset, "fileDir")
        file_name_regex = _get_child_text(dataset, "fileNameRegex")
        cruise = _guess_cruise_from_filedir(file_dir)
        if not cruise or not file_name_regex:
            skipped_count += 1
            continue

        key = (cruise, file_name_regex)
        nc_path = nc_index.get(key)
        if nc_path is None:
            skipped_count += 1
            continue

        matched_keys.add(key)
        if nc_path not in nc_schema_cache:
            nc_schema_cache[nc_path] = read_nc_schema(nc_path)
        schema = nc_schema_cache[nc_path]

        _ensure_child_text(dataset, "fileDir", f"{filedir_prefix.rstrip('/')}/{cruise}/")
        _ensure_child_text(dataset, "fileNameRegex", nc_path.name)
        reused, created = _sync_datavariables_for_dataset(dataset, schema)

        updated_count += 1
        reused_vars_total += reused
        created_vars_total += created

    if create_missing_datasets:
        if template_dataset is None:
            raise ValueError("Cannot create missing datasets: no template dataset found in XML.")
        grouped = defaultdict(list)
        for (cruise, filename), path in nc_index.items():
            if (cruise, filename) not in matched_keys:
                grouped[cruise].append(path)

        for cruise in sorted(grouped):
            for nc_path in sorted(grouped[cruise], key=lambda p: p.name):
                new_dataset = _create_dataset_from_template(template_dataset, nc_path, filedir_prefix)
                schema = nc_schema_cache.get(nc_path)
                if schema is None:
                    schema = read_nc_schema(nc_path)
                    nc_schema_cache[nc_path] = schema
                _sync_datavariables_for_dataset(new_dataset, schema)
                root.append(new_dataset)
                created_count += 1

    output_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_xml), pretty_print=True, xml_declaration=True, encoding="UTF-8")

    logger.info("Updated dataset blocks: %d", updated_count)
    logger.info("Skipped existing blocks (no matching file): %d", skipped_count)
    logger.info("Created new dataset blocks: %d", created_count)
    logger.info("Reused existing dataVariable nodes: %d", reused_vars_total)
    logger.info("Created new dataVariable nodes: %d", created_vars_total)
    logger.info("Wrote XML: %s", output_xml)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync ERDDAP datasets.xml blocks against NetCDF files (including QC vars)."
    )
    parser.add_argument(
        "--input-xml",
        type=Path,
        default=Path("datasets/mod_CTD_datasets.xml"),
        help="Input ERDDAP datasets XML file.",
    )
    parser.add_argument(
        "--output-xml",
        type=Path,
        default=Path("datasets/mod_CTD_datasets_qc.xml"),
        help="Output XML path.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("datasets/SFER_CTD_SOAK_REMOVED"),
        help="Root containing cruise directories with .nc files.",
    )
    parser.add_argument(
        "--filedir-prefix",
        type=str,
        default="data/erddap/SFER_CTD_SOAK_REMOVED",
        help="ERDDAP fileDir prefix used in each dataset block.",
    )
    parser.add_argument(
        "--dataset-type",
        type=str,
        default="EDDTableFromNcCFFiles",
        help="Dataset type tag to sync.",
    )
    parser.add_argument(
        "--create-missing-datasets",
        action="store_true",
        help="Create new dataset blocks for NetCDF files absent from the XML.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite input XML instead of writing to --output-xml.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(verbose=args.verbose)

    if not args.input_xml.exists():
        raise FileNotFoundError(f"Input XML not found: {args.input_xml}")
    if not args.data_root.exists():
        raise FileNotFoundError(f"Data root not found: {args.data_root}")

    output_xml = args.input_xml if args.in_place else args.output_xml

    sync_xml(
        input_xml=args.input_xml,
        output_xml=output_xml,
        data_root=args.data_root,
        filedir_prefix=args.filedir_prefix,
        dataset_type=args.dataset_type,
        create_missing_datasets=args.create_missing_datasets,
    )


if __name__ == "__main__":
    main()
