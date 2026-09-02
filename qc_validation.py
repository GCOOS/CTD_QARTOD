"""Strict preflight validation for dataset-owned QC configuration."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Mapping

from dataset_profile import DatasetProfile
from qc_config import ALL_CATEGORIES
from station_resolver import normalize_station_coord

REQUIRED_QC_PATHS = (
    "variable_mapping",
    "station_coords",
    "location_config",
    "station_climatology",
    "station_depth_classification",
    "sensor_specs",
    "variable_sensor_map",
    "spike_thresholds",
    "rate_of_change_thresholds",
    "flat_line_config",
)


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid readable JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return value


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _validate_variable_mapping(root: Mapping[str, Any]) -> set[str]:
    unknown_categories = sorted(set(root) - ALL_CATEGORIES)
    if unknown_categories:
        raise ValueError(
            f"variable_mapping has unknown categories: {', '.join(unknown_categories)}"
        )

    owner: dict[str, str] = {}
    for category, variables in root.items():
        if not isinstance(variables, list) or not all(
            isinstance(variable, str) and variable for variable in variables
        ):
            raise ValueError(f"variable_mapping.{category} must be a list of names")
        for variable in variables:
            if variable in owner:
                raise ValueError(
                    f"variable_mapping variable {variable!r} appears in multiple categories: "
                    f"{owner[variable]!r} and {category!r}"
                )
            owner[variable] = category
    return set(owner)


def _validate_station_coords(path: Path) -> None:
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        required = {"station", "lat_mean", "lon_mean"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"station_coords must have columns {sorted(required)}")

        stations: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            station = normalize_station_coord(row.get("station") or "")
            if not station:
                raise ValueError(f"station_coords line {line_number} has no station")
            if station in stations:
                raise ValueError(f"station_coords duplicate station {station!r}")
            stations.add(station)

            latitude = _finite_number(
                row.get("lat_mean"), f"station_coords line {line_number} lat_mean"
            )
            longitude = _finite_number(
                row.get("lon_mean"), f"station_coords line {line_number} lon_mean"
            )
            if not -90 <= latitude <= 90:
                raise ValueError(f"station_coords line {line_number} latitude is invalid")
            if not -180 <= longitude <= 180:
                raise ValueError(f"station_coords line {line_number} longitude is invalid")


def _validate_location(root: Mapping[str, Any]) -> None:
    if "tolerance" not in root:
        raise ValueError("location_config.tolerance is required")
    if root["tolerance"] is None:
        return
    if _finite_number(root["tolerance"], "location_config.tolerance") <= 0:
        raise ValueError("location_config.tolerance must be positive or null")


def _validate_station_classes(root: Mapping[str, Any]) -> None:
    classes: dict[str, set[str]] = {}
    for name in ("deep_cast", "shallow_cast"):
        values = root.get(name, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"station_depth_classification.{name} must be a list")
        normalized = [normalize_station_coord(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"station_depth_classification.{name} has duplicates")
        classes[name] = set(normalized)

    overlap = sorted(classes["deep_cast"] & classes["shallow_cast"])
    if overlap:
        raise ValueError(
            "station_depth_classification deep_cast and shallow_cast overlap: "
            + ", ".join(overlap)
        )


def _validate_climatology(root: Mapping[str, Any], variables: set[str]) -> None:
    unknown = sorted(set(root) - {"deep_cast_limits", "shallow_cast_limits"})
    if unknown:
        raise ValueError(f"station_climatology has unknown keys: {', '.join(unknown)}")

    for cast_name in ("deep_cast_limits", "shallow_cast_limits"):
        limits = root.get(cast_name, {})
        if not isinstance(limits, dict):
            raise ValueError(f"station_climatology.{cast_name} must be an object")
        for variable, records in limits.items():
            if variable not in variables:
                raise ValueError(
                    f"station_climatology references unmapped variable {variable!r}"
                )
            if not isinstance(records, list):
                raise ValueError(
                    f"station_climatology.{cast_name}.{variable} must be a list"
                )
            for index, record in enumerate(records):
                if not isinstance(record, dict) or record.get("period") != "month":
                    raise ValueError(
                        f"station_climatology {cast_name}.{variable}[{index}] is invalid"
                    )
                for span_name in ("zspan", "tspan", "vspan"):
                    span = record.get(span_name)
                    if not isinstance(span, list) or len(span) != 2:
                        raise ValueError(
                            f"station_climatology {variable} {span_name} must have two values"
                        )
                    low = _finite_number(span[0], f"{variable}.{span_name}[0]")
                    high = _finite_number(span[1], f"{variable}.{span_name}[1]")
                    if low > high:
                        raise ValueError(
                            f"station_climatology {variable} {span_name} is reversed"
                        )


def _validate_sensor_specs(root: Mapping[str, Any]) -> set[str]:
    sensors = root.get("sensors")
    if not isinstance(sensors, dict):
        raise ValueError("sensor_specs.sensors must be an object")
    for sensor, spec in sensors.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("ranges"), dict):
            raise ValueError(f"sensor_specs.{sensor}.ranges must be an object")
        for unit, bounds in spec["ranges"].items():
            if not isinstance(bounds, dict) or "min" not in bounds or "max" not in bounds:
                raise ValueError(f"sensor_specs.{sensor}.ranges[{unit!r}] is invalid")
            minimum, maximum = bounds["min"], bounds["max"]
            if minimum is None and maximum is None:
                continue
            if minimum is None or maximum is None:
                raise ValueError(f"sensor_specs.{sensor}.ranges[{unit!r}] is incomplete")
            low = _finite_number(minimum, f"sensor_specs.{sensor}.{unit}.min")
            high = _finite_number(maximum, f"sensor_specs.{sensor}.{unit}.max")
            if low > high:
                raise ValueError(f"sensor_specs.{sensor}.ranges[{unit!r}] is reversed")
    return set(sensors)


def _validate_variable_sensor_map(
    root: Mapping[str, Any], variables: set[str], sensors: set[str]
) -> None:
    for variable, sensor in root.items():
        if variable not in variables:
            raise ValueError(f"variable_sensor_map references unmapped variable {variable!r}")
        if not isinstance(sensor, str) or sensor not in sensors:
            raise ValueError(f"variable_sensor_map references unknown sensor {sensor!r}")


def _validate_spike_thresholds(root: Mapping[str, Any], variables: set[str]) -> None:
    if "variables" in root:
        raise ValueError("spike_thresholds legacy 'variables' wrapper is not supported")
    for variable, config in root.items():
        if variable not in variables or not isinstance(config, dict):
            raise ValueError(f"spike_thresholds has invalid variable {variable!r}")
        suspect = config.get("suspect_threshold")
        fail = config.get("fail_threshold")
        if suspect is None and fail is None:
            continue
        if suspect is None or fail is None:
            raise ValueError(f"spike_thresholds.{variable} must set both thresholds or null")
        suspect_value = _finite_number(suspect, f"{variable}.suspect_threshold")
        fail_value = _finite_number(fail, f"{variable}.fail_threshold")
        if suspect_value < 0 or fail_value < 0:
            raise ValueError(f"spike_thresholds.{variable} thresholds must be nonnegative")
        if suspect_value > fail_value:
            raise ValueError(
                f"spike_thresholds.{variable} suspect_threshold exceeds fail_threshold"
            )


def _validate_rate_thresholds(root: Mapping[str, Any], variables: set[str]) -> None:
    if "variables" in root:
        raise ValueError(
            "rate_of_change_thresholds legacy 'variables' wrapper is not supported"
        )
    for variable, config in root.items():
        if variable not in variables or not isinstance(config, dict):
            raise ValueError(f"rate_of_change_thresholds has invalid variable {variable!r}")
        threshold = config.get("threshold")
        if threshold is None:
            continue
        if _finite_number(threshold, f"{variable}.threshold") <= 0:
            raise ValueError(
                f"rate_of_change_thresholds.{variable}.threshold must be positive"
            )


def _validate_flat_line(root: Mapping[str, Any]) -> None:
    suspect = _positive_int(root.get("rep_cnt_suspect"), "rep_cnt_suspect")
    fail = _positive_int(root.get("rep_cnt_fail"), "rep_cnt_fail")
    eps = _finite_number(root.get("eps"), "flat_line_config.eps")
    if fail < suspect:
        raise ValueError("flat_line_config rep_cnt_fail must be >= rep_cnt_suspect")
    if eps < 0:
        raise ValueError("flat_line_config.eps must be nonnegative")


def validate_qc_profile(profile: DatasetProfile) -> dict[str, Path]:
    """Validate every configured QC input before any NetCDF file is processed."""
    paths: dict[str, Path] = {}
    for key in REQUIRED_QC_PATHS:
        path = profile.paths.get(key)
        if path is None or not path.is_file():
            raise ValueError(f"dataset profile {key} is missing or not a file: {path}")
        paths[key] = path

    for test_name, mode in profile.qc_test_modes.items():
        if mode == "run":
            raise ValueError(f"{test_name} run mode is not implemented")

    mapping = _json_object(paths["variable_mapping"], "variable_mapping")
    variables = _validate_variable_mapping(mapping)
    _validate_station_coords(paths["station_coords"])
    _validate_location(_json_object(paths["location_config"], "location_config"))
    _validate_station_classes(
        _json_object(
            paths["station_depth_classification"], "station_depth_classification"
        )
    )
    _validate_climatology(
        _json_object(paths["station_climatology"], "station_climatology"), variables
    )
    sensors = _validate_sensor_specs(
        _json_object(paths["sensor_specs"], "sensor_specs")
    )
    _validate_variable_sensor_map(
        _json_object(paths["variable_sensor_map"], "variable_sensor_map"),
        variables,
        sensors,
    )
    _validate_spike_thresholds(
        _json_object(paths["spike_thresholds"], "spike_thresholds"), variables
    )
    _validate_rate_thresholds(
        _json_object(
            paths["rate_of_change_thresholds"], "rate_of_change_thresholds"
        ),
        variables,
    )
    _validate_flat_line(
        _json_object(paths["flat_line_config"], "flat_line_config")
    )
    return paths
