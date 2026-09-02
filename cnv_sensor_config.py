"""Generate gross-range sensor configuration from converted CNV NetCDF files."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import json
import os
from pathlib import Path
import tempfile

import xarray as xr

from cnv_mapping import DERIVED_SENSOR_TAG, load_cnv_mapping
from dataset_profile import DatasetProfile, resolve_config_path


def _json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"configuration must be a JSON object: {path}")
    return value


def _qc_variables(path: Path) -> set[str]:
    mapping = _json_object(path)
    variables: set[str] = set()
    for category, names in mapping.items():
        if not isinstance(names, list) or not all(
            isinstance(name, str) and name for name in names
        ):
            raise ValueError(f"QC category {category!r} must contain a list of names")
        variables.update(names)
    return variables


def _can_replace(path: Path, kind: str, overwrite: bool) -> None:
    if overwrite or not path.exists():
        return
    existing = _json_object(path)
    is_empty = existing == {} if kind == "map" else existing.get("sensors") == {}
    if not is_empty:
        raise FileExistsError(
            f"refusing to replace reviewed sensor configuration {path}; "
            "use --overwrite to replace it"
        )


def _stage_json(path: Path, payload: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        return Path(handle.name)


def generate_sensor_configs(
    profile: DatasetProfile,
    *,
    overwrite: bool = False,
) -> dict[str, object]:
    """Write sensor specs and variable links with human-fillable null limits."""

    mapping_path = resolve_config_path("cnv_mapping", profile)
    variable_mapping_path = resolve_config_path("variable_mapping", profile)
    sensor_specs_path = resolve_config_path("sensor_specs", profile)
    variable_sensor_map_path = resolve_config_path("variable_sensor_map", profile)

    _can_replace(sensor_specs_path, "specs", overwrite)
    _can_replace(variable_sensor_map_path, "map", overwrite)

    netcdf_paths = tuple(
        sorted(
            (
                path
                for path in profile.data_root.rglob("*.nc")
                if path.is_file()
            ),
            key=lambda path: path.as_posix().casefold(),
        )
    )
    if not netcdf_paths:
        raise ValueError(f"no NetCDF files found under {profile.data_root}")

    mapping = load_cnv_mapping(mapping_path)
    selected_variables = _qc_variables(variable_mapping_path)
    if not selected_variables:
        raise ValueError("QC variable mapping selects no variables")

    observed_variables: set[str] = set()
    unresolved_variables: set[str] = set()
    variables_without_units: set[str] = set()
    variable_sensors: dict[str, set[str]] = defaultdict(set)
    sensor_units: dict[str, set[str]] = defaultdict(set)
    derived_sensors: set[str] = set()

    for path in netcdf_paths:
        with xr.open_dataset(path, decode_cf=False) as dataset:
            for variable_name in selected_variables.intersection(dataset.variables):
                observed_variables.add(variable_name)
                variable = dataset[variable_name]
                reference = variable.attrs.get("instrument")
                if reference is not None:
                    instrument_name = str(reference).strip()
                    if not instrument_name or instrument_name not in dataset.variables:
                        raise ValueError(
                            f"{path.name}: {variable_name} has invalid instrument "
                            f"reference {reference!r}"
                        )
                    sensor_name = str(
                        dataset[instrument_name].attrs.get("long_name", "")
                    ).strip()
                    if not sensor_name:
                        raise ValueError(
                            f"{path.name}: {instrument_name} has no sensor long_name"
                        )
                else:
                    source_name = str(variable.attrs.get("source_name", "")).strip()
                    specification = mapping.science_variables.get(source_name)
                    if (
                        specification is None
                        or specification.sensor_tag != DERIVED_SENSOR_TAG
                    ):
                        unresolved_variables.add(variable_name)
                        continue
                    sensor_name = f"derived:{specification.target_name}"
                    derived_sensors.add(sensor_name)

                variable_sensors[variable_name].add(sensor_name)
                sensor_units[sensor_name]
                unit = variable.attrs.get("units")
                if unit is None or not str(unit).strip():
                    variables_without_units.add(variable_name)
                else:
                    sensor_units[sensor_name].add(str(unit).strip())

    missing_variables = selected_variables - observed_variables
    if missing_variables:
        raise ValueError(
            "QC variables were not found in converted NetCDF files: "
            + ", ".join(sorted(missing_variables))
        )
    if unresolved_variables:
        raise ValueError(
            "QC variables need an exact CNV sensor_tag or the reserved value "
            f"{DERIVED_SENSOR_TAG!r}: "
            + ", ".join(sorted(unresolved_variables))
        )

    sensor_names = sorted(sensor_units.keys() | derived_sensors, key=str.casefold)
    sensors: dict[str, object] = {}
    for sensor_name in sensor_names:
        spec: dict[str, object] = {
            "description": (
                "Derived variable with no dedicated instrument"
                if sensor_name in derived_sensors
                else "Detected from converted CNV instrument metadata"
            ),
            "identifiers": {
                "long_names": []
                if sensor_name in derived_sensors
                else [sensor_name]
            },
            "ranges": {
                unit: {"min": None, "max": None}
                for unit in sorted(sensor_units[sensor_name], key=str.casefold)
            },
        }
        if sensor_name in derived_sensors:
            spec["requires_instrument"] = False
        sensors[sensor_name] = spec

    variable_sensor_map: dict[str, object] = {}
    for variable_name in sorted(variable_sensors, key=str.casefold):
        names = sorted(variable_sensors[variable_name], key=str.casefold)
        variable_sensor_map[variable_name] = names[0] if len(names) == 1 else names

    sensor_specs = {"sensors": sensors}
    temporary_paths: list[Path] = []
    try:
        temporary_paths = [
            _stage_json(sensor_specs_path, sensor_specs),
            _stage_json(variable_sensor_map_path, variable_sensor_map),
        ]
        os.replace(temporary_paths[0], sensor_specs_path)
        os.replace(temporary_paths[1], variable_sensor_map_path)
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)

    return {
        "netcdf_file_count": len(netcdf_paths),
        "qc_variable_count": len(selected_variables),
        "sensor_count": len(sensors),
        "variables_without_units": sorted(variables_without_units),
        "sensor_specs": str(sensor_specs_path),
        "variable_sensor_map": str(variable_sensor_map_path),
    }
