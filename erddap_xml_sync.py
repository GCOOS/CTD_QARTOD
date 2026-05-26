#!/usr/bin/env python3
"""
Sync ERDDAP datasets XML from NetCDF files.

This script updates EDDTableFromNcCFFiles <dataset> blocks so that:
1) fileDir/fileNameRegex point to the selected data root layout,
2) dataVariable entries (names, types, and addAttributes) match NetCDF contents, and
3) optionally removes dataset blocks with no matching NetCDF under --data-root.


"""

from __future__ import annotations

import argparse
import copy
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

import numpy as np
import xarray as xr
from lxml import etree

from dataset_profile import DatasetProfile, default_profile
from qc_config import (
    ERDDAP_DATASETS_XML,
    ERDDAP_DATASETS_XML_OUTPUT,
    ERDDAP_FILEDIR_BASE,
)

logger = logging.getLogger("erddap_xml_sync")

# ERDDAP display attributes not present in NetCDF; preserved when --preserve-erddap-ui is set.
ERDDAP_UI_ATTR_NAMES = frozenset(
    {
        "colorBarMaximum",
        "colorBarMinimum",
        "colorBarScale",
        "colorBarType",
    }
)

NC_ATTRS_SKIP = frozenset({"_ChunkSizes"})


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


def prefixed_dataset_id(stem_or_id: str, dataset_id_prefix: str = "") -> str:
    """Return a datasetID-safe token with an optional, non-duplicated prefix."""
    safe_id = safe_dataset_id(stem_or_id)
    if dataset_id_prefix and not safe_id.startswith(dataset_id_prefix):
        return f"{dataset_id_prefix}{safe_id}"
    return safe_id


def scan_nc_files(data_root: Path) -> Dict[Tuple[str, str], Path]:
    """Index NetCDF files by (cruise_dir_name, filename)."""
    indexed: Dict[Tuple[str, str], Path] = {}
    for nc_path in sorted(data_root.glob("*/*.nc")):
        cruise = nc_path.parent.name
        indexed[(cruise, nc_path.name)] = nc_path
    return indexed


def _normalize_attr_value(value: object) -> object:
    if isinstance(value, (np.generic,)):
        value = value.item()
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return value.item()
        if value.dtype.kind in ("U", "S", "O"):
            return [str(v) for v in value.tolist()]
        return value.tolist()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _format_attr_text(value: object) -> str:
    value = _normalize_attr_value(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, float):
                parts.append(repr(float(item)))
            elif isinstance(item, (int, np.integer)):
                parts.append(str(int(item)))
            else:
                parts.append(str(item))
        return ", ".join(parts)
    if isinstance(value, float):
        if np.isnan(value):
            return "NaN"
        return repr(float(value))
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if value is None:
        return "null"
    return str(value)


def _erddap_attr_type(name: str, value: object) -> str | None:
    value = _normalize_attr_value(value)
    if isinstance(value, list):
        if value and all(isinstance(v, (int, np.integer)) for v in value):
            return "intList" if len(value) > 1 else "int"
        if value and all(isinstance(v, (float, int, np.floating, np.integer)) for v in value):
            return "doubleList" if len(value) > 1 else "double"
        return None
    if isinstance(value, (int, np.integer)):
        return "int"
    if isinstance(value, (float, np.floating)):
        return "double"
    return None


def _infer_ioos_category(var_name: str, attrs: Mapping[str, object]) -> str | None:
    if attrs.get("ioos_category"):
        return str(attrs["ioos_category"])
    standard_name = str(attrs.get("standard_name") or "")
    if standard_name.endswith("_quality_flag") or standard_name == "aggregate_quality_flag":
        return "Quality"
    if "_qc" in var_name:
        return "Quality"
    return None


def nc_attrs_to_add_attributes(
    nc_attrs: Mapping[str, object],
    var_name: str,
    preserved_ui: Mapping[str, str] | None = None,
) -> etree.Element:
    """Build <addAttributes> from NetCDF variable attributes."""
    add_attrs = etree.Element("addAttributes")
    merged: dict[str, object] = dict(nc_attrs)

    ioos = _infer_ioos_category(var_name, merged)
    if ioos:
        merged["ioos_category"] = ioos

    for key, text in (preserved_ui or {}).items():
        if key not in merged:
            merged[key] = text

    for name in sorted(merged):
        if name in NC_ATTRS_SKIP:
            continue
        value = merged[name]
        if value is None:
            continue
        att = etree.SubElement(add_attrs, "att")
        att.set("name", name)
        attr_type = _erddap_attr_type(name, value)
        if attr_type:
            att.set("type", attr_type)
        att.text = _format_attr_text(value)
    return add_attrs


def read_nc_variable_metadata(nc_path: Path) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Return ordered (variable_name, erddap_dataType, attrs) from a NetCDF file."""
    with xr.open_dataset(nc_path, decode_cf=False, mask_and_scale=False) as ds:
        result: List[Tuple[str, str, Dict[str, Any]]] = []
        for name in ds.variables:
            var = ds[name]
            attrs = {k: _normalize_attr_value(v) for k, v in dict(var.attrs).items()}
            result.append((name, erddap_data_type(var.dtype), attrs))
        return result


def _split_ancillary_variables(value: object) -> list[str]:
    value = _normalize_attr_value(value)
    if value is None:
        return []
    if isinstance(value, list):
        parts = [str(item).strip() for item in value]
    else:
        parts = re.split(r"[\s,]+", str(value).strip())
    return [part for part in parts if part]


def _append_first_present(
    ordered: list[Tuple[str, str, Dict[str, Any]]],
    used: set[str],
    by_name: Mapping[str, Tuple[str, str, Dict[str, Any]]],
    candidates: Iterable[str],
) -> None:
    for name in candidates:
        item = by_name.get(name)
        if item is not None and name not in used:
            ordered.append(item)
            used.add(name)
            return


def _order_nc_metadata_for_erddap(
    nc_metadata: Iterable[Tuple[str, str, Dict[str, Any]]],
    profile: DatasetProfile | None = None,
) -> list[Tuple[str, str, Dict[str, Any]]]:
    """Order ERDDAP variables for profile/axis fields, then data variables and QC flags."""
    metadata = list(nc_metadata)
    prof = profile or default_profile()
    by_name = {item[0]: item for item in metadata}
    ordered: list[Tuple[str, str, Dict[str, Any]]] = []
    used: set[str] = set()

    lead_groups: list[Iterable[str]] = [
        ("profile",),
        prof.metadata.time,
        ("time_elapsed",),
        prof.metadata.latitude,
        prof.metadata.longitude,
        (prof.metadata.cruise_id,),
        (prof.metadata.station,),
        prof.metadata.depth,
    ]
    for candidates in lead_groups:
        _append_first_present(ordered, used, by_name, candidates)

    ancillary_by_parent = {}
    for name, _, attrs in metadata:
        ancillary_by_parent[name] = [
            qc_name
            for qc_name in _split_ancillary_variables(attrs.get("ancillary_variables"))
            if qc_name in by_name
        ]
    referenced_flags = {
        qc_name
        for qc_names in ancillary_by_parent.values()
        for qc_name in qc_names
    }

    for name, _, _ in metadata:
        if name in used or name in referenced_flags:
            continue
        qc_names = ancillary_by_parent.get(name) or []
        if not qc_names:
            continue
        ordered.append(by_name[name])
        used.add(name)
        for qc_name in qc_names:
            if qc_name not in used:
                ordered.append(by_name[qc_name])
                used.add(qc_name)

    for item in metadata:
        if item[0] not in used:
            ordered.append(item)
            used.add(item[0])

    return ordered


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


def _extract_erddap_ui_attrs(data_var_elem: etree._Element) -> Dict[str, str]:
    """Pull ERDDAP UI-only attributes from an existing dataVariable node."""
    add_attrs = data_var_elem.find("addAttributes")
    if add_attrs is None:
        return {}
    ui: Dict[str, str] = {}
    for att in add_attrs.findall("att"):
        name = att.get("name")
        if name in ERDDAP_UI_ATTR_NAMES and att.text is not None:
            ui[name] = att.text.strip()
    return ui


def _replace_add_attributes(data_var_elem: etree._Element, new_add_attrs: etree.Element) -> None:
    for child in list(data_var_elem):
        if child.tag == "addAttributes":
            data_var_elem.remove(child)
    data_var_elem.append(new_add_attrs)


def _build_datavariable(
    source_name: str,
    data_type: str,
    nc_attrs: Mapping[str, object],
    preserved_ui: Mapping[str, str] | None = None,
) -> etree._Element:
    dv = etree.Element("dataVariable")
    etree.SubElement(dv, "sourceName").text = source_name
    etree.SubElement(dv, "destinationName").text = source_name
    etree.SubElement(dv, "dataType").text = data_type
    _replace_add_attributes(dv, nc_attrs_to_add_attributes(nc_attrs, source_name, preserved_ui))
    return dv


def _sync_datavariables_for_dataset(
    dataset_elem: etree._Element,
    nc_metadata: Iterable[Tuple[str, str, Dict[str, Any]]],
    *,
    preserve_erddap_ui: bool = True,
) -> Tuple[int, int]:
    """
    Sync dataset dataVariable list to match NetCDF schema and attributes.

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
    for source_name, data_type, nc_attrs in nc_metadata:
        preserved_ui = None
        if preserve_erddap_ui and source_name in existing_by_source:
            preserved_ui = _extract_erddap_ui_attrs(existing_by_source[source_name])
            dv = copy.deepcopy(existing_by_source[source_name])
            _ensure_child_text(dv, "sourceName", source_name)
            _ensure_child_text(dv, "destinationName", source_name)
            _ensure_child_text(dv, "dataType", data_type)
            _replace_add_attributes(
                dv,
                nc_attrs_to_add_attributes(nc_attrs, source_name, preserved_ui),
            )
            reused += 1
        else:
            dv = _build_datavariable(source_name, data_type, nc_attrs, preserved_ui)
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


def _dataset_match_key(dataset_elem: etree._Element) -> Tuple[str, str] | None:
    """Return (cruise, fileNameRegex) when both can be parsed from a dataset block."""
    cruise = _guess_cruise_from_filedir(_get_child_text(dataset_elem, "fileDir"))
    file_name_regex = _get_child_text(dataset_elem, "fileNameRegex")
    if not cruise or not file_name_regex:
        return None
    return cruise, file_name_regex


def _remove_orphan_datasets(
    root: etree._Element,
    nc_index: Mapping[Tuple[str, str], Path],
    *,
    dataset_type: str,
) -> int:
    """
    Remove EDDTableFromNcCFFiles blocks whose (cruise, fileNameRegex) is absent from nc_index.

    Blocks missing fileDir/fileNameRegex or an unparseable cruise are left unchanged.
    """
    to_remove: list[etree._Element] = []
    for dataset in root.findall("dataset"):
        if dataset.get("type") != dataset_type:
            continue
        key = _dataset_match_key(dataset)
        if key is None or key not in nc_index:
            if key is not None:
                to_remove.append(dataset)
    for dataset in to_remove:
        root.remove(dataset)
    return len(to_remove)


def _create_dataset_from_template(
    template: etree._Element,
    nc_path: Path,
    filedir_prefix: str,
    dataset_id_prefix: str = "",
) -> etree._Element:
    dataset = copy.deepcopy(template)
    dataset.set("datasetID", prefixed_dataset_id(nc_path.stem, dataset_id_prefix))
    _ensure_child_text(dataset, "fileDir", f"{filedir_prefix.rstrip('/')}/{nc_path.parent.name}/")
    _ensure_child_text(dataset, "fileNameRegex", nc_path.name)
    return dataset


def resolve_erddap_data_root(
    profile: DatasetProfile | None = None,
    override: Path | str | None = None,
) -> Path:
    """NetCDF tree used for XML sync (QC duplicate output when configured)."""
    if override is not None:
        return Path(override)
    prof = profile or default_profile()
    if prof.output.mode == "duplicate":
        return prof.output.directory
    return prof.data_root


def _filedir_prefix_from_data_root(data_root: Path) -> str:
    """Map a local data root to the ERDDAP server path under data/erddap/."""
    dataset_name = data_root.name
    if not dataset_name:
        raise ValueError(f"Cannot derive ERDDAP fileDir prefix from data root: {data_root}")
    return f"{ERDDAP_FILEDIR_BASE}/{dataset_name}"


def resolve_filedir_prefix(data_root: Path, filedir_prefix: str | None = None) -> str:
    """Resolve the ERDDAP fileDir prefix.

    If the caller did not explicitly set --filedir-prefix, use
    ``data/erddap/<dataset_folder_name>`` derived from --data-root
    (e.g. ``output/SFER_QC`` → ``data/erddap/SFER_QC``).
    """
    if filedir_prefix:
        return filedir_prefix.rstrip("/")
    return _filedir_prefix_from_data_root(data_root)


def sync_xml(
    input_xml: Path,
    output_xml: Path,
    data_root: Path,
    filedir_prefix: str,
    dataset_id_prefix: str = "",
    dataset_type: str = "EDDTableFromNcCFFiles",
    create_missing_datasets: bool = False,
    remove_orphan_datasets: bool = True,
    preserve_erddap_ui: bool = True,
    profile: DatasetProfile | None = None,
) -> None:
    parser = etree.XMLParser(remove_blank_text=True, strip_cdata=False)
    tree = etree.parse(str(input_xml), parser)
    root = tree.getroot()

    if root.tag != "erddapDatasets":
        raise ValueError(f"Unexpected root tag: {root.tag}")

    prof = profile or default_profile()
    nc_index = scan_nc_files(data_root)
    if not nc_index:
        raise ValueError(f"No NetCDF files found in {data_root}")

    matched_keys: set[Tuple[str, str]] = set()
    datasets = [d for d in root.findall("dataset") if d.get("type") == dataset_type]
    template_dataset = datasets[0] if datasets else None

    nc_metadata_cache: Dict[Path, List[Tuple[str, str, Dict[str, Any]]]] = {}

    updated_count = 0
    skipped_count = 0
    removed_count = 0
    created_count = 0
    reused_vars_total = 0
    created_vars_total = 0

    for dataset in datasets:
        key = _dataset_match_key(dataset)
        if key is None:
            skipped_count += 1
            continue

        nc_path = nc_index.get(key)
        if nc_path is None:
            if not remove_orphan_datasets:
                skipped_count += 1
            continue

        matched_keys.add(key)
        if nc_path not in nc_metadata_cache:
            nc_metadata_cache[nc_path] = _order_nc_metadata_for_erddap(
                read_nc_variable_metadata(nc_path),
                prof,
            )
        metadata = nc_metadata_cache[nc_path]

        cruise, _ = key
        current_dataset_id = dataset.get("datasetID")
        if current_dataset_id:
            dataset.set("datasetID", prefixed_dataset_id(current_dataset_id, dataset_id_prefix))
        _ensure_child_text(dataset, "fileDir", f"{filedir_prefix.rstrip('/')}/{cruise}/")
        _ensure_child_text(dataset, "fileNameRegex", nc_path.name)
        reused, created = _sync_datavariables_for_dataset(
            dataset,
            metadata,
            preserve_erddap_ui=preserve_erddap_ui,
        )

        updated_count += 1
        reused_vars_total += reused
        created_vars_total += created

    if remove_orphan_datasets:
        removed_count = _remove_orphan_datasets(
            root, nc_index, dataset_type=dataset_type
        )

    if create_missing_datasets:
        if template_dataset is None:
            raise ValueError("Cannot create missing datasets: no template dataset found in XML.")
        grouped = defaultdict(list)
        for (cruise, filename), path in nc_index.items():
            if (cruise, filename) not in matched_keys:
                grouped[cruise].append(path)

        for cruise in sorted(grouped):
            for nc_path in sorted(grouped[cruise], key=lambda p: p.name):
                new_dataset = _create_dataset_from_template(
                    template_dataset,
                    nc_path,
                    filedir_prefix,
                    dataset_id_prefix,
                )
                metadata = nc_metadata_cache.get(nc_path)
                if metadata is None:
                    metadata = _order_nc_metadata_for_erddap(
                        read_nc_variable_metadata(nc_path),
                        prof,
                    )
                    nc_metadata_cache[nc_path] = metadata
                _sync_datavariables_for_dataset(
                    new_dataset,
                    metadata,
                    preserve_erddap_ui=preserve_erddap_ui,
                )
                root.append(new_dataset)
                created_count += 1

    output_xml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_xml), pretty_print=True, xml_declaration=True, encoding="UTF-8")

    logger.info("Updated dataset blocks: %d", updated_count)
    logger.info("Skipped existing blocks (unparseable or kept orphan): %d", skipped_count)
    logger.info("Removed orphan dataset blocks: %d", removed_count)
    logger.info("Created new dataset blocks: %d", created_count)
    logger.info("Reused existing dataVariable nodes: %d", reused_vars_total)
    logger.info("Created new dataVariable nodes: %d", created_vars_total)
    logger.info("Wrote XML: %s", output_xml)


def run_erddap_xml_sync(
    *,
    input_xml: Path,
    output_xml: Path,
    data_root: Path,
    profile: DatasetProfile | None = None,
    filedir_prefix: str | None = None,
    dataset_id_prefix: str = "",
    dataset_type: str = "EDDTableFromNcCFFiles",
    create_missing_datasets: bool = False,
    remove_orphan_datasets: bool = True,
    in_place: bool = False,
    preserve_erddap_ui: bool = True,
    verbose: bool = False,
) -> Path:
    """
    Validate paths, configure logging, and run sync_xml.

    When *in_place* is True, *output_xml* is ignored and the input file is overwritten.
    Returns the path written.
    """
    setup_logging(verbose=verbose)
    if not input_xml.exists():
        raise FileNotFoundError(f"Input XML not found: {input_xml}")
    if not data_root.exists():
        raise FileNotFoundError(f"Data root not found: {data_root}")

    resolved_output = input_xml if in_place else output_xml
    resolved_filedir_prefix = resolve_filedir_prefix(data_root, filedir_prefix)
    sync_xml(
        input_xml=input_xml,
        output_xml=resolved_output,
        data_root=data_root,
        filedir_prefix=resolved_filedir_prefix,
        profile=profile,
        dataset_id_prefix=dataset_id_prefix,
        dataset_type=dataset_type,
        create_missing_datasets=create_missing_datasets,
        remove_orphan_datasets=remove_orphan_datasets,
        preserve_erddap_ui=preserve_erddap_ui,
    )
    return resolved_output


def run_erddap_xml_sync_for_profile(
    profile: DatasetProfile | None = None,
    *,
    input_xml: Path | None = None,
    output_xml: Path | None = None,
    data_root: Path | str | None = None,
    filedir_prefix: str | None = None,
    dataset_id_prefix: str = "",
    create_missing_datasets: bool = False,
    remove_orphan_datasets: bool = True,
    preserve_erddap_ui: bool = True,
    verbose: bool = False,
) -> Path:
    """Sync ERDDAP XML using dataset profile defaults (QC output tree when duplicate mode)."""
    prof = profile or default_profile()
    return run_erddap_xml_sync(
        input_xml=input_xml or ERDDAP_DATASETS_XML,
        output_xml=output_xml or ERDDAP_DATASETS_XML_OUTPUT,
        data_root=resolve_erddap_data_root(prof, data_root),
        profile=prof,
        filedir_prefix=filedir_prefix,
        dataset_id_prefix=dataset_id_prefix,
        create_missing_datasets=create_missing_datasets,
        remove_orphan_datasets=remove_orphan_datasets,
        in_place=False,
        preserve_erddap_ui=preserve_erddap_ui,
        verbose=verbose,
    )


def add_erddap_xml_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ERDDAP XML sync CLI flags on *parser* (subparser or standalone)."""
    from dataset_profile import DEFAULT_PROFILE_PATH

    parser.add_argument(
        "--profile",
        type=str,
        default=str(DEFAULT_PROFILE_PATH),
        help=f"Dataset profile JSON for default --data-root (default: '{DEFAULT_PROFILE_PATH}')",
    )
    parser.add_argument(
        "--input-xml",
        type=Path,
        default=ERDDAP_DATASETS_XML,
        help=f"Input ERDDAP datasets XML file (default: '{ERDDAP_DATASETS_XML}')",
    )
    parser.add_argument(
        "--output-xml",
        type=Path,
        default=ERDDAP_DATASETS_XML_OUTPUT,
        help=f"Output XML path (default: '{ERDDAP_DATASETS_XML_OUTPUT}')",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help=(
            "Root containing cruise directories with .nc files "
            "(default: profile duplicate output dir or data_root)"
        ),
    )
    parser.add_argument(
        "--filedir-prefix",
        type=str,
        default=None,
        help=(
            "ERDDAP fileDir prefix used in each dataset block "
            f"(default: {ERDDAP_FILEDIR_BASE}/<dataset_name> from --data-root)"
        ),
    )
    parser.add_argument(
        "--dataset-id-prefix",
        type=str,
        default="",
        help="Prefix to add to synced ERDDAP datasetID values, e.g. 'SFER_CTD_'.",
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
        "--keep-orphan-datasets",
        action="store_true",
        help=(
            "Keep dataset blocks whose cruise/filename is not present under --data-root "
            "(default: remove them)."
        ),
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite input XML instead of writing to --output-xml (not recommended).",
    )
    parser.add_argument(
        "--no-preserve-erddap-ui",
        action="store_true",
        help="Do not preserve ERDDAP color bar attributes from the input XML.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync ERDDAP datasets.xml blocks against NetCDF files (including QC metadata)."
    )
    add_erddap_xml_arguments(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    prof = default_profile()
    data_root = resolve_erddap_data_root(prof, args.data_root)
    run_erddap_xml_sync(
        input_xml=args.input_xml,
        output_xml=args.output_xml,
        data_root=data_root,
        profile=prof,
        filedir_prefix=args.filedir_prefix,
        dataset_id_prefix=args.dataset_id_prefix,
        dataset_type=args.dataset_type,
        create_missing_datasets=args.create_missing_datasets,
        remove_orphan_datasets=not args.keep_orphan_datasets,
        in_place=args.in_place,
        preserve_erddap_ui=not args.no_preserve_erddap_ui,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
