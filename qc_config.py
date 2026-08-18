"""
QC configuration constants and placeholders.

This file centralizes tunable parameters for the QC pipeline so they can be
adjusted per variable or dataset without touching the QC logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

import numpy as np
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

# ERDDAP datasets.xml defaults (used by main.py erddap-xml and erddap_xml_sync.py)
_DATASETS_DIR = Path(__file__).parent / "datasets"
ERDDAP_OUTPUT_DIR = Path(__file__).parent / "output" / "erddap"
ERDDAP_DATASETS_XML = _DATASETS_DIR / "mod_CTD_datasets.xml"
ERDDAP_DATASET_TEMPLATE_XML = _DATASETS_DIR / "GenerateDatasetsXml.xml"
ERDDAP_DATASETS_XML_OUTPUT = ERDDAP_OUTPUT_DIR / "mod_CTD_datasets_qc.xml"
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

LOCATION_DEFAULTS = {
    "tolerance": 0.01,
}

# Flat-line test defaults (QARTOD-style count-based implementation).
# REP_CNT values are "number of previous observations".
FLAT_LINE_DEFAULTS = {
    "rep_cnt_suspect": 3,
    "rep_cnt_fail": 5,
    "eps": 0.0,
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
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_variable_thresholds_json(json_path: Path | str) -> Dict[str, Any]:
    """
    Load a flat ``{ "<var_name>": { ... }, ... }`` thresholds file, or legacy ``{ "variables": {...} }``.
    """
    path = Path(json_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        root = json.load(f)
    if not isinstance(root, dict):
        return {}
    block = root.get("variables")
    if isinstance(block, dict):
        return block
    return {k: v for k, v in root.items() if isinstance(v, dict)}


def load_spike_thresholds(json_path: Path | str = SPIKE_THRESHOLDS_JSON) -> Dict[str, Any]:
    """Load per-variable spike test thresholds (suspect_threshold, fail_threshold)."""
    return _load_variable_thresholds_json(json_path)


def load_rate_of_change_thresholds(
    json_path: Path | str = RATE_OF_CHANGE_THRESHOLDS_JSON,
) -> Dict[str, Any]:
    """Load per-variable rate-of-change thresholds (threshold = max |Δvalue| per adjacent sample)."""
    return _load_variable_thresholds_json(json_path)


def _positive_int(value: object, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if isinstance(value, bool) or number < 1:
        return default
    return number


def _nonnegative_float(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number < 0:
        return default
    return number


def _positive_float(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(number) or number <= 0:
        return default
    return number


def load_location_config(
    json_path: Path | str = LOCATION_CONFIG_JSON,
) -> Dict[str, Optional[float]]:
    """Load location-test config; an explicit null tolerance disables the test."""
    config: Dict[str, Optional[float]] = dict(LOCATION_DEFAULTS)
    path = Path(json_path)
    if not path.exists():
        return config
    with path.open("r", encoding="utf-8") as f:
        root = json.load(f)
    if not isinstance(root, dict):
        return config
    if root.get("tolerance") is None and "tolerance" in root:
        return {"tolerance": None}
    config["tolerance"] = _positive_float(root.get("tolerance"), LOCATION_DEFAULTS["tolerance"])
    return config


def load_flat_line_config(json_path: Path | str = FLAT_LINE_CONFIG_JSON) -> Dict[str, Any]:
    """Load global flat-line test config, falling back to defaults for missing or invalid fields."""
    config: Dict[str, Any] = dict(FLAT_LINE_DEFAULTS)
    path = Path(json_path)
    if not path.exists():
        return config
    with path.open("r", encoding="utf-8") as f:
        root = json.load(f)
    if not isinstance(root, dict):
        return config

    config["rep_cnt_suspect"] = _positive_int(root.get("rep_cnt_suspect"), FLAT_LINE_DEFAULTS["rep_cnt_suspect"])
    config["rep_cnt_fail"] = _positive_int(root.get("rep_cnt_fail"), FLAT_LINE_DEFAULTS["rep_cnt_fail"])
    config["eps"] = _nonnegative_float(root.get("eps"), FLAT_LINE_DEFAULTS["eps"])

    if config["rep_cnt_fail"] < config["rep_cnt_suspect"]:
        config["rep_cnt_suspect"] = FLAT_LINE_DEFAULTS["rep_cnt_suspect"]
        config["rep_cnt_fail"] = FLAT_LINE_DEFAULTS["rep_cnt_fail"]

    return config


def load_station_depth_classification(json_path: Path | str = STATION_DEPTH_CLASSIFICATION_JSON) -> Dict[str, Any]:
    """
    Load station → deep_cast / shallow_cast membership lists.
    """
    path = Path(json_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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
