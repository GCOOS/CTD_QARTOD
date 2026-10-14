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

import numpy as np
import xarray as xr

from dataset_profile import CONFIG_TEMPLATE_DIR, MetadataConfig
from station_classification import load_station_classification
from station_identity import StationNormalization

# CONFIG FILE PATHS
# Generic defaults come from the shared template. Station references
# (coordinates, classes, climatology) have no default: datasets select
# their own paths through a dataset profile.

CONFIG_DIR = CONFIG_TEMPLATE_DIR

# Standard variable name → data variable names
VARIABLE_MAPPING_JSON = CONFIG_DIR / "qc_variable_mapping.json"

LOCATION_CONFIG_JSON = CONFIG_DIR / "location_test" / "location_config.json"

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


def load_station_climatology_config(json_path: Path | str) -> Dict[str, Any]:
    """
    Load the station climatology limits JSON, keyed by station class name.
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


def _finite_stat(values: object, reducer) -> Optional[float]:
    if values is None:
        return None
    array = np.asarray(values, dtype=float)
    finite = array[np.isfinite(array)]
    return float(reducer(finite)) if finite.size else None


def get_climatology_config_for_file(
    ds: xr.Dataset,
    limits_json_path: Path | str,
    classification_json_path: Path | str,
    metadata: MetadataConfig | None = None,
    normalization: StationNormalization = StationNormalization(),
) -> Optional[Mapping[str, Iterable[Mapping[str, float]]]]:
    """
    Resolve climatology limits for the file from its station class.

    The class comes from the explicit station lists in *classification_json_path*,
    then from the first rule matching the cast maximum depth and mean position.
    Limit tables in *limits_json_path* are keyed by class name.
    """
    from qc_data_loader import get_lon_lat, get_station_id, _metadata_or_default

    meta = _metadata_or_default(metadata)
    classification = load_station_classification(classification_json_path, normalization)
    depth_name = next((name for name in meta.depth if name in ds.variables), None)
    longitude, latitude = get_lon_lat(ds, meta)
    class_name = classification.classify(
        get_station_id(ds, meta),
        max_depth=_finite_stat(ds[depth_name].values if depth_name else None, np.max),
        latitude=_finite_stat(None if latitude is None else latitude.values, np.mean),
        longitude=_finite_stat(None if longitude is None else longitude.values, np.mean),
    )
    if class_name is None:
        return None
    return load_station_climatology_config(limits_json_path).get(class_name) or None
