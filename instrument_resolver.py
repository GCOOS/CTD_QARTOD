"""
Instrument extraction and unit-aware gross range resolution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple, Union

import xarray as xr

from qc_config import SENSOR_SPECS_JSON, VARIABLE_SENSOR_MAP_JSON


InstrumentInfo = Dict[str, str]
GrossRangeConfig = Dict[str, Dict[str, Tuple[float, float]]]
VariableSensorMap = Dict[str, Union[str, List[str]]]


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_sensor_specs(specs_path: Path | str = SENSOR_SPECS_JSON) -> Mapping[str, dict]:
    """
    Load sensor specifications (identifiers + unit-aware ranges).
    """
    data = _load_json(Path(specs_path))
    return data.get("sensors", {})


def load_variable_sensor_map(map_path: Path | str = VARIABLE_SENSOR_MAP_JSON) -> VariableSensorMap:
    """
    Load variable -> sensor mapping.
    """
    return _load_json(Path(map_path))


def extract_instruments(ds: xr.Dataset) -> List[InstrumentInfo]:
    """
    Extract all instrument variables (instrument, instrument1, ..., instrument20).
    Returns a list of instrument attribute dictionaries.
    """
    instruments: List[InstrumentInfo] = []
    for i in range(21):
        var_name = "instrument" if i == 0 else f"instrument{i}"
        if var_name not in ds.variables:
            continue
        var = ds[var_name]
        attrs = getattr(var, "attrs", {}) or {}
        instruments.append(
            {
                "variable_name": var_name,
                "long_name": attrs.get("long_name", "").strip(),
                "make_model": str(attrs.get("make_model", "")).strip(),
                "serial_number": str(attrs.get("serial_number", "")).strip(),
                "calibration_date": str(attrs.get("calibration_date", "")).strip(),
            }
        )
    return instruments


def get_variable_unit(ds: xr.Dataset, var_name: str) -> Optional[str]:
    """
    Get the units attribute from a variable (if present).
    """
    if var_name not in ds.variables:
        return None
    unit = ds[var_name].attrs.get("units")
    if unit is None:
        return None
    return str(unit).strip()


def _normalize_values(values: Iterable[str]) -> set[str]:
    return {str(val).strip().lower() for val in values if str(val).strip()}


def _sensor_present(
    sensor_spec: Mapping[str, object],
    instrument_long_names: set[str],
) -> bool:
    if sensor_spec.get("requires_instrument", True) is False:
        return True

    identifiers = sensor_spec.get("identifiers", {}) or {}
    long_names = _normalize_values(identifiers.get("long_names", []))

    return bool(long_names.intersection(instrument_long_names))


def _find_unit_range(unit: Optional[str], ranges: Mapping[str, Mapping[str, float]]) -> Optional[Tuple[float, float]]:
    """
    Find (min, max) for a unit key (case-insensitive).
    """
    if unit is None:
        return None
    unit_lower = unit.lower()
    for unit_name, span in ranges.items():
        if unit_name.lower() == unit_lower and "min" in span and "max" in span:
            return (float(span["min"]), float(span["max"]))
    return None


def resolve_gross_ranges(
    ds: xr.Dataset,
    specs_path: Path | str = SENSOR_SPECS_JSON,
    variable_map_path: Path | str = VARIABLE_SENSOR_MAP_JSON,
) -> Dict[str, Dict[str, Tuple[float, float]]]:
    """
    Extract instruments and build unit-aware gross range config.

    For each variable mapped to a sensor:
        - verify the sensor exists in the file's instrument list
        - read variable's units attribute
        - lookup range in sensor_specs.json for that unit

    Returns:
        {variable_name: {"fail_span": (min, max)}} for matched ranges.
        Variables without a matching unit/range are omitted (so upstream will NOT_EVALUATED).
    """
    sensor_specs = load_sensor_specs(specs_path)
    variable_map = load_variable_sensor_map(variable_map_path)

    resolved: Dict[str, Dict[str, Tuple[float, float]]] = {}

    instruments = extract_instruments(ds)
    instrument_long_names = _normalize_values(inst.get("long_name", "") for inst in instruments)

    for var_name, sensor_keys in variable_map.items():
        if var_name not in ds:
            continue
        resolved_for_var = False
        keys = sensor_keys if isinstance(sensor_keys, list) else [sensor_keys]
        for sensor_key in keys:
            sensor_spec = sensor_specs.get(sensor_key)
            if not sensor_spec:
                continue
            if not _sensor_present(sensor_spec, instrument_long_names):
                continue
            unit = get_variable_unit(ds, var_name)
            ranges = sensor_spec.get("ranges", {}) or {}
            span = _find_unit_range(unit, ranges)
            if span:
                resolved[var_name] = {"fail_span": span}
                resolved_for_var = True
                break
        if not resolved_for_var:
            # Explicitly override static defaults when the mapped sensor/unit does not match.
            resolved[var_name] = {}

    return resolved
