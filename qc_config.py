"""
QC configuration constants and placeholders.

This file centralizes tunable parameters for the QC pipeline so they can be
adjusted per variable or dataset without touching the QC logic.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

import xarray as xr

from dataset_profile import MetadataConfig, WALTON_SMITH_CONFIG_DIR

# CONFIG FILE PATHS
# Default config files belong to the Walton Smith dataset. Other datasets
# select their own paths through a dataset profile.

CONFIG_DIR = WALTON_SMITH_CONFIG_DIR

# Standard variable name → dataset-specific variable names (Walton categories)
VARIABLE_MAPPING_JSON = CONFIG_DIR / "variable_mapping" / "walton_mapping.json"

# Station coordinates: ground truth lat/lon for each station (for location test)
STATION_COORDS_CSV = CONFIG_DIR / "location_test" / "Station_Mean_Coords.csv"
LOCATION_CONFIG_JSON = CONFIG_DIR / "location_test" / "location_config.json"

# Station climatology: limit tables keyed by cast type (deep vs shallow)
STATION_CLIMATOLOGY_JSON = CONFIG_DIR / "climatology_test" / "station_climatology_config.json"

# Station → deep_cast vs shallow_cast membership (separate from limit values)
STATION_DEPTH_CLASSIFICATION_JSON = (
    CONFIG_DIR / "climatology_test" / "station_depth_classification.json"
)

# Sensor specs: sensor-specific gross range limits by unit
SENSOR_SPECS_JSON = CONFIG_DIR / "gross_range_test" / "sensor_specs.json"

# Variable-sensor map: maps variables to their sensors
VARIABLE_SENSOR_MAP_JSON = CONFIG_DIR / "gross_range_test" / "variable_sensor_map.json"

# Per-variable spike and rate-of-change thresholds
SPIKE_THRESHOLDS_JSON = CONFIG_DIR / "spike_test" / "spike_thresholds.json"
RATE_OF_CHANGE_THRESHOLDS_JSON = (
    CONFIG_DIR / "rate_of_change_test" / "rate_of_change_thresholds.json"
)
FLAT_LINE_CONFIG_JSON = CONFIG_DIR / "flat_line_test" / "flat_line_config.json"

# ERDDAP implementation constants. Dataset paths and policy live in the profile.
_DATASETS_DIR = Path(__file__).parent / "datasets"
ERDDAP_DATASET_TEMPLATE_XML = _DATASETS_DIR / "GenerateDatasetsXml.xml"
# ERDDAP server path: bigParentDirectory/data/erddap/<dataset_name>/...
ERDDAP_FILEDIR_BASE = "/data/erddap"

# QC FLAGS AND PARAMETERS

# QARTOD quality flags
QC_FLAGS = {
    "PASS": 1,
    "NOT_EVALUATED": 2,
    "SUSPECT": 3,
    "FAIL": 4,
    "MISSING": 9,
}

ALL_CATEGORIES = frozenset({
    "in_water_radiance_irradiance",
    "above_water_radiance_irradiance",
    "beam_attenuation",
    "turbidity",
    "PAR",
    "chlorophyll",
    "CDOM",
    "FDOM",
    "backscattering_volume_scattering",
    
    "temperature",
    "practical_salinity",
    "conductivity",
    "pressure",
    "oxygen_dissolved_oxygen",
    "oxygen_saturation",
})

# Maps each QC test to the set of categories it applies to.
TEST_CATEGORIES = {
    #required tests
    "gap_test": ALL_CATEGORIES,
    "syntax_test": ALL_CATEGORIES,
    "location_test": ALL_CATEGORIES,
    "gross_range_test": ALL_CATEGORIES,
    "decreasing_radiance_test": frozenset({
        "in_water_radiance_irradiance",
        "PAR",
    }),
    "climatology_test": frozenset({
        "temperature",
        "practical_salinity",

        "oxygen_dissolved_oxygen",

        #strongly recommended in optic variables
    }),

    #strongly recommended tests

    # "photic_zone_limit_test": frozenset({
    #     # Configure this after the test implementation is added.
    #     "in_water_radiance_irradiance",
    #     "PAR",
    # }),



    "spike_test": ALL_CATEGORIES,
    #spike test need high threshold and low threshold value

    "rate_of_change_test": ALL_CATEGORIES,
    # Count-based |value[i] - value[i-1]| between consecutive samples; threshold in data units per step (see rate_of_change_thresholds.json).

    "flat_line_test": ALL_CATEGORIES,


    

}


def load_station_climatology_config(json_path: Path | str = STATION_CLIMATOLOGY_JSON) -> Dict[str, Any]:
    """
    Load the station climatology limits JSON (deep_cast_limits / shallow_cast_limits only).
    """
    path = Path(json_path)
    with path.open("r", encoding="utf-8") as f:
        root = json.load(f)
    if not isinstance(root, dict):
        raise ValueError(f"Station climatology config must be a JSON object: {path}")
    return root


def _load_variable_thresholds_json(json_path: Path | str) -> Dict[str, Any]:
    """
    Load a flat ``{ "<var_name>": { ... }, ... }`` thresholds file.
    """
    path = Path(json_path)
    with path.open("r", encoding="utf-8") as f:
        root = json.load(f)
    if not isinstance(root, dict):
        raise ValueError(f"Threshold config must be a JSON object: {path}")
    if "variables" in root:
        raise ValueError("legacy 'variables' threshold wrapper is not supported")
    if not all(isinstance(value, dict) for value in root.values()):
        raise ValueError(f"Threshold entries must be JSON objects: {path}")
    return root


def load_spike_thresholds(json_path: Path | str = SPIKE_THRESHOLDS_JSON) -> Dict[str, Any]:
    """Load per-variable spike test thresholds (suspect_threshold, fail_threshold)."""
    return _load_variable_thresholds_json(json_path)


def load_rate_of_change_thresholds(
    json_path: Path | str = RATE_OF_CHANGE_THRESHOLDS_JSON,
) -> Dict[str, Any]:
    """Load per-variable rate-of-change thresholds (threshold = max |Δvalue| per adjacent sample)."""
    return _load_variable_thresholds_json(json_path)


def _finite_float(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if isinstance(value, bool) or not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def load_location_config(
    json_path: Path | str = LOCATION_CONFIG_JSON,
) -> Dict[str, Optional[float]]:
    """Load location-test config; an explicit null tolerance disables the test."""
    path = Path(json_path)
    with path.open("r", encoding="utf-8") as f:
        root = json.load(f)
    if not isinstance(root, dict):
        raise ValueError(f"Location config must be a JSON object: {path}")
    if "tolerance" not in root:
        raise ValueError("location tolerance is required")
    if root["tolerance"] is None:
        return {"tolerance": None}
    tolerance = _finite_float(root["tolerance"], "location tolerance")
    if tolerance <= 0:
        raise ValueError("location tolerance must be positive or null")
    return {"tolerance": tolerance}


def load_flat_line_config(json_path: Path | str = FLAT_LINE_CONFIG_JSON) -> Dict[str, Any]:
    """Load and validate the global flat-line test config."""
    path = Path(json_path)
    with path.open("r", encoding="utf-8") as f:
        root = json.load(f)
    if not isinstance(root, dict):
        raise ValueError(f"Flat-line config must be a JSON object: {path}")
    for key in ("rep_cnt_suspect", "rep_cnt_fail"):
        value = root.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    eps = _finite_float(root.get("eps"), "flat-line eps")
    if eps < 0:
        raise ValueError("flat-line eps must be nonnegative")
    if root["rep_cnt_fail"] < root["rep_cnt_suspect"]:
        raise ValueError("rep_cnt_fail must be >= rep_cnt_suspect")
    return {
        "rep_cnt_suspect": root["rep_cnt_suspect"],
        "rep_cnt_fail": root["rep_cnt_fail"],
        "eps": eps,
    }


def load_station_depth_classification(json_path: Path | str = STATION_DEPTH_CLASSIFICATION_JSON) -> Dict[str, Any]:
    """
    Load station → deep_cast / shallow_cast membership lists.
    """
    path = Path(json_path)
    with path.open("r", encoding="utf-8") as f:
        root = json.load(f)
    if not isinstance(root, dict):
        raise ValueError(f"Station depth classification must be a JSON object: {path}")
    return root


def get_climatology_config_for_file(
    ds: xr.Dataset,
    limits_json_path: Path | str = STATION_CLIMATOLOGY_JSON,
    classification_json_path: Path | str | None = None,
    metadata: MetadataConfig | None = None,
) -> Optional[Mapping[str, Iterable[Mapping[str, float]]]]:
    """
    Resolve climatology limits for the file based on station ID.

    Station membership (which stations are deep vs shallow) comes from
    *classification_json_path* (default: ``STATION_DEPTH_CLASSIFICATION_JSON``).
    Limit tables come from *limits_json_path* (default: ``STATION_CLIMATOLOGY_JSON``).
    """
    from qc_data_loader import get_station_id

    station_id = get_station_id(ds, metadata)
    if not station_id:
        return None

    limits_root = load_station_climatology_config(limits_json_path)
    class_path = (
        Path(classification_json_path)
        if classification_json_path is not None
        else STATION_DEPTH_CLASSIFICATION_JSON
    )
    classification = load_station_depth_classification(class_path)

    # Normalize station id for comparison
    sid = str(station_id).strip().lower()
    if sid.endswith(".0"):
        sid = sid[:-2]

    # Helper to check membership in a list of station ids (case-insensitive)
    def _contains_station(stations: Iterable[str], target: str) -> bool:
        target = target.strip().lower()
        return any(str(s).strip().lower() == target for s in stations)

    station_type_priority = [
        ("deep_cast", "deep_cast_limits"),
        ("shallow_cast", "shallow_cast_limits"),
    ]

    for list_key, limits_key in station_type_priority:
        stations = classification.get(list_key) or []
        if stations and _contains_station(stations, sid):
            limits = limits_root.get(limits_key)
            if limits:
                return limits

    return None
