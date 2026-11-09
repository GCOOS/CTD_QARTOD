"""Verified CNV aliases and spelling-only unit normalization.

The catalog does not convert numbers or infer units missing from a header.
"""
from collections.abc import Mapping
from functools import lru_cache
import json
import logging
from pathlib import Path
import re

CATALOG_PATH = Path(__file__).parent / "config_template" / "cnv_catalog.json"
TEMPLATE_MAPPING_PATH = Path(__file__).parent / "config_template" / "qc_variable_mapping.json"
CF_ATTRIBUTE_KEYS = ("standard_name", "standard_name_url")
COORDINATE_NAMES = ("time", "latitude", "longitude", "depth")

logger = logging.getLogger(__name__)
_empty_cf_warnings: set[tuple[str, str]] = set()


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def cf_placeholders(definition: Mapping[str, object]) -> dict[str, str]:
    """Return editable standard_name / standard_name_url strings, empty if unset."""
    placeholders: dict[str, str] = {}
    for key in CF_ATTRIBUTE_KEYS:
        value = definition.get(key)
        placeholders[key] = value.strip() if isinstance(value, str) else ""
    return placeholders


def catalog_cf_attributes(definition: Mapping[str, object], target: str) -> dict[str, str]:
    """Copy non-empty CF name attributes; never synthesize a URL."""
    attributes: dict[str, str] = {}
    for key in CF_ATTRIBUTE_KEYS:
        value = definition.get(key)
        if isinstance(value, str) and value.strip():
            attributes[key] = value.strip()
            continue
        token = (target, key)
        if token not in _empty_cf_warnings:
            _empty_cf_warnings.add(token)
            logger.warning(
                "%s is empty for %s; omitting the attribute",
                key,
                target,
            )
    return attributes


def catalog_coordinate_placeholders() -> dict[str, dict[str, str]]:
    """Seed per-dataset coordinate CF placeholders from the shared catalog."""
    coordinates = catalog().get("coordinates")
    if not isinstance(coordinates, dict):
        raise ValueError("cnv_catalog.json coordinates must be an object")
    placeholders: dict[str, dict[str, str]] = {}
    for name in COORDINATE_NAMES:
        definition = coordinates.get(name)
        if not isinstance(definition, dict):
            raise ValueError(f"cnv_catalog.json coordinates.{name} is required")
        placeholders[name] = cf_placeholders(definition)
    return placeholders


def normalize_unit(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    for original, normalized in catalog()["units"].items():
        if value.casefold() == original.casefold():
            return normalized
    return value


@lru_cache(maxsize=1)
def template_categories() -> dict:
    return json.loads(TEMPLATE_MAPPING_PATH.read_text(encoding="utf-8"))


def _entry(name: str, definition: dict) -> dict:
    category = next((category for category, names in template_categories().items() if name in names), None)
    return {**definition, "target": name, "qc_reference": name if category else None,
            "qc_category": category}


def source_entry(source: str) -> dict | None:
    return next((_entry(name, entry) for name, entry in catalog()["variables"].items()
                 if source in entry["sources"]), None)


def match_entry(source: str, descriptions: list[str], units: list[str]) -> dict | None:
    entry = source_entry(source)
    if entry is None or not all(re.search(entry["description"], text) for text in descriptions):
        return None
    if not all(normalize_unit(unit) in entry["units"] for unit in units):
        return None
    return entry


def qc_entry(name: str, attributes: dict) -> dict | None:
    """Match converted source provenance first, then canonical/reference names."""
    source = attributes.get("source_name")
    if source:
        entry = source_entry(str(source))
        if entry is not None:
            return entry
    base = re.sub(r"_\d+$", "", re.sub(r"[\s-]+", "_", name.strip().casefold()))
    return next((_entry(target, entry)
                 for target, entry in catalog()["variables"].items()
                 if base in {target.casefold(),
                             *(alias.casefold() for alias in entry.get("aliases", []))}), None)
