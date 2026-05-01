"""
QC configuration constants and placeholders.

This file centralizes tunable parameters for the QC pipeline so they can be
adjusted per variable or dataset without touching the QC logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

import xarray as xr

from qc_data_loader import get_station_id

# CONFIG FILE PATHS
# All config files are stored in the config/ directory.
# Change these paths if you need to use different config files.

CONFIG_DIR = Path(__file__).parent / "config"

# Default dataset directory
DATASET_DIR = Path(__file__).parent / "datasets" / "SFER_CTD_SOAK_REMOVED"

# Variable mapping: maps variable names to QC categories
VARIABLE_MAPPING_JSON = CONFIG_DIR / "walton_mapping.json"

# Station coordinates: ground truth lat/lon for each station (for location test)
STATION_COORDS_CSV = CONFIG_DIR / "Station_Mean_Coords.csv"

# Station climatology: station-specific climatology limits
STATION_CLIMATOLOGY_JSON = CONFIG_DIR / "station_climatology_config.json"

# Sensor specs: sensor-specific gross range limits by unit
SENSOR_SPECS_JSON = CONFIG_DIR / "sensor_specs.json"

# Variable-sensor map: maps variables to their sensors
VARIABLE_SENSOR_MAP_JSON = CONFIG_DIR / "variable_sensor_map.json"

# QC FLAGS AND PARAMETERS

# QARTOD quality flags
QC_FLAGS = {
    "PASS": 1,
    "NOT_EVALUATED": 2,
    "SUSPECT": 3,
    "FAIL": 4,
    "MISSING": 9,
}

# Variable-specific gross range defaults (customize per sensor/dataset)
# Each entry: variable_name -> {"fail_span": (min, max), "suspect_span": (min, max)}
GROSS_RANGE_CONFIG = {
    "sea_water_temperature": {"fail_span": (-2.0, 40.0), "suspect_span": (0.0, 35.0)},
    "sea_water_salinity": {"fail_span": (0.0, 45.0), "suspect_span": (25.0, 40.0)},
    "sea_water_salinity_2": {"fail_span": (0.0, 45.0), "suspect_span": (25.0, 40.0)},
    "sea_water_electrical_conductivity": {"fail_span": (0.0, 7.0), "suspect_span": (0.0, 6.5)},
    "sea_water_electrical_conductivity_2": {"fail_span": (0.0, 7.0), "suspect_span": (0.0, 6.5)},
    "sea_water_pressure": {"fail_span": (0.0, 12000.0), "suspect_span": (0.0, 11000.0)},
    "dissolved_oxygen": {"fail_span": (0.0, 500.0), "suspect_span": (0.0, 400.0)},
    "oxygen_saturation": {"fail_span": (0.0, 500.0), "suspect_span": (0.0, 400.0)},
    "oxygen_saturation_2": {"fail_span": (0.0, 500.0), "suspect_span": (0.0, 400.0)},
    "beam_attenuation": {"fail_span": (0.0, 10.0), "suspect_span": (0.0, 8.0)},
    "sea_water_turbidity": {"fail_span": (0.0, 1000.0), "suspect_span": (0.0, 800.0)},
    "photosynthetically_available_radiation": {"fail_span": (0.0, 4000.0), "suspect_span": (0.0, 3500.0)},
    "surface_photosynthetically_available_radiation": {"fail_span": (0.0, 4000.0), "suspect_span": (0.0, 3500.0)},
    "chlorophyll_concentration": {"fail_span": (0.0, 1000.0), "suspect_span": (0.0, 500.0)},
    "chlorophyll_fluorescence": {"fail_span": (0.0, 1000.0), "suspect_span": (0.0, 500.0)},
    "CDOM": {"fail_span": (0.0, 1000.0), "suspect_span": (0.0, 500.0)},
}

# Tolerance (in degrees) for Location Test
LOCATION_TOLERANCE = 0.01

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

    "photic_zone_limit_test": frozenset({
        #may not apply where high ch fl increases energy at 683nm
        "in_water_radiance_irradiance",
        "PAR",
    }),



    "spike_test": ALL_CATEGORIES,

    "rate_of_change_test": ALL_CATEGORIES,#only pass and suspect

    "flat_line_test": ALL_CATEGORIES,

    # "spike_test": frozenset({
    #     #t, sp, c, p
    #     #temperature,practical_salinity,conductivity,pressure
    #     #1,3,4
    #     "temperature",
    #     "practical_salinity",
    #     "conductivity",
    #     "pressure",

    #     "oxygen_dissolved_oxygen",
    # }),


    # "rate_of_change_test": frozenset({
    #     #t, sp, c, p
    #     #temperature,practical_salinity,conductivity,pressure
    #     #1,4
    #     "temperature",
    #     "practical_salinity",
    #     "conductivity",
    #     "pressure",

    #     "oxygen_dissolved_oxygen",


    # }),
    # "flat_line_test": frozenset({
    #     #t, sp, c, p
    #     #temperature,practical_salinity,conductivity,pressure
    #     #1,3,4
    #     "temperature",
    #     "practical_salinity",
    #     "conductivity",
    #     "pressure",

    #     "oxygen_dissolved_oxygen",
    # }),


}

# PLACEHOLDER!!!!!!!!!!!!!!!!!!!!
# 
#  
# Each variable maps to a list of period configs. Example for monthly bins:
# {"tspan": (1, 3), "vspan": (15, 28), "period": "month"}
CLIMATOLOGY_CONFIG = {
    "sea_water_temperature": [
        {"tspan": (1, 3), "vspan": (15.0, 28.0), "period": "month"},
        {"tspan": (4, 6), "vspan": (18.0, 31.0), "period": "month"},
        {"tspan": (7, 9), "vspan": (20.0, 33.0), "period": "month"},
        {"tspan": (10, 12), "vspan": (15.0, 30.0), "period": "month"},
    ],
    "sea_water_salinity": [
        {"tspan": (1, 12), "vspan": (25.0, 40.0), "period": "month"},
    ],
}




def load_station_climatology_config(json_path: Path | str = STATION_CLIMATOLOGY_JSON) -> Dict[str, Any]:
    """
    Load the station climatology JSON configuration.
    """
    path = Path(json_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_climatology_config_for_file(
    ds: xr.Dataset,
    json_path: Path | str = STATION_CLIMATOLOGY_JSON,
) -> Optional[Mapping[str, Iterable[Mapping[str, float]]]]:
    """
    Resolve climatology limits for the file based on station ID.
    """
    station_id = get_station_id(ds)
    if not station_id:
        return None

    config = load_station_climatology_config(json_path)

    # Normalize station id for comparison
    sid = str(station_id).strip().lower()
    if sid.endswith(".0"):
        sid = sid[:-2]

    # Helper to check membership in a list of station ids (case-insensitive)
    def _contains_station(stations: Iterable[str], target: str) -> bool:
        target = target.strip().lower()
        return any(str(s).strip().lower() == target for s in stations)

    # Preferred new-style station types and their corresponding limits keys
    station_type_priority = [
        ("deep_cast", "deep_cast_limits"),
        ("shallow_cast", "shallow_cast_limits"),
        ("shallow_stable", "shallow_stable_limits"),
    ]


    for list_key, limits_key in station_type_priority:
        stations = config.get(list_key) or []
        if stations and _contains_station(stations, sid):
            limits = config.get(limits_key)
            if limits:
                return limits

    return None


