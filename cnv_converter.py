"""Parse NOAA AOML Sea-Bird CNV casts for the CTD QARTOD workflow."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import numpy as np
import xarray as xr

from cnv_mapping import (
    CnvMapping,
    StructuralFieldMapping,
    TransformMapping,
    load_cnv_mapping,
)
from dataset_profile import DatasetProfile, MetadataConfig, resolve_config_path
from qc_data_loader import flatten_mapping, load_mapping

_NUMERIC_STATION_RE = re.compile(r"^(?P<integer>\d+)(?:\.(?P<fraction>\d+))?$")
_NAME_RE = re.compile(r"^# name (?P<index>\d+) = (?P<name>[^:]+):\s*(?P<description>.*)$")


@dataclass(frozen=True)
class CnvColumn:
    index: int
    source_name: str
    description: str


@dataclass(frozen=True)
class CnvSensor:
    channel: int
    sensor_type: str
    serial_number: str
    calibration_date: str
    coefficients: Mapping[str, str]


@dataclass(frozen=True)
class CnvCast:
    source_path: Path
    cruise_id: str
    station_source: str
    station_id: str
    sequence: int
    header: Mapping[str, str]
    columns: tuple[CnvColumn, ...]
    values: np.ndarray
    sensors: tuple[CnvSensor, ...]
    sensor_xml: str

    def column(self, source_name: str) -> np.ndarray:
        try:
            index = next(
                column.index for column in self.columns if column.source_name == source_name
            )
        except StopIteration as exc:
            raise KeyError(source_name) from exc
        return self.values[:, index]


class CnvFormatError(ValueError):
    """Raised when a CNV or companion file violates the expected format."""


@dataclass(frozen=True)
class ConversionReport:
    """Result and on-disk location of one directory conversion."""

    report_path: Path
    data: Mapping[str, object]

    def as_dict(self) -> dict[str, object]:
        """Return the JSON-normalized report payload."""

        return json.loads(json.dumps(self.data))

    @property
    def has_failures(self) -> bool:
        counts = self.data["counts"]
        return bool(counts["failed"])


def _apply_case(value: str, case: str) -> str:
    if case == "upper":
        return value.upper()
    if case == "lower":
        return value.lower()
    return value


def canonical_station(
    value: str,
    case: str = "upper",
    normalize_numeric: bool = True,
) -> str:
    """Apply configured case and numeric normalization to a station identifier."""

    stripped = value.strip()
    if not normalize_numeric:
        return _apply_case(stripped, case)

    match = _NUMERIC_STATION_RE.fullmatch(stripped)
    if match is None:
        return _apply_case(stripped, case)

    integer = str(int(match.group("integer")))
    fraction = (match.group("fraction") or "").rstrip("0")
    return f"{integer}.{fraction}" if fraction else integer


def _format_error(path: Path, detail: str) -> CnvFormatError:
    return CnvFormatError(f"{path.name}: {detail}")


def _sensor_coefficients(sensor_element: ET.Element) -> dict[str, str]:
    coefficients: dict[str, str] = {}

    def visit(element: ET.Element, prefix: str = "") -> None:
        for child in element:
            key = child.tag
            if child.attrib:
                attributes = ",".join(
                    f"{name}={value}" for name, value in sorted(child.attrib.items())
                )
                key = f"{key}[{attributes}]"
            path = f"{prefix}.{key}" if prefix else key
            if len(child):
                visit(child, path)
            elif child.tag not in {"SerialNumber", "CalibrationDate"}:
                coefficients[path] = (child.text or "").strip()

    visit(sensor_element)
    return coefficients


def _sensors_from_wrappers(wrappers: Sequence[ET.Element]) -> tuple[CnvSensor, ...]:
    sensors: list[CnvSensor] = []
    for fallback_channel, wrapper in enumerate(wrappers, start=1):
        sensor_element = next(iter(wrapper), None)
        if sensor_element is None or sensor_element.tag == "NotInUse":
            continue
        channel_text = wrapper.attrib.get("Channel")
        channel = int(channel_text) if channel_text is not None else fallback_channel
        sensors.append(
            CnvSensor(
                channel=channel,
                sensor_type=sensor_element.tag,
                serial_number=(sensor_element.findtext("SerialNumber") or "").strip(),
                calibration_date=(
                    sensor_element.findtext("CalibrationDate") or ""
                ).strip(),
                coefficients=_sensor_coefficients(sensor_element),
            )
        )
    return tuple(sensors)


def _parse_embedded_sensors(path: Path, header_text: str) -> tuple[tuple[CnvSensor, ...], str]:
    sensor_lines: list[str] = []
    inside_sensors = False
    for line in header_text.splitlines():
        if line.startswith("# <Sensors"):
            inside_sensors = True
        if inside_sensors:
            sensor_lines.append(line[2:] if line.startswith("# ") else line[1:])
        if line.startswith("# </Sensors>"):
            break

    if not sensor_lines or not sensor_lines[-1].strip().endswith("</Sensors>"):
        raise _format_error(path, "missing complete embedded Sensors XML")

    sensor_xml = "\n".join(sensor_lines)
    try:
        root = ET.fromstring(sensor_xml)
    except ET.ParseError as exc:
        raise _format_error(path, f"invalid embedded Sensors XML: {exc}") from exc
    return _sensors_from_wrappers(root.findall("sensor")), sensor_xml


def parse_cnv(path: Path | str, mapping: CnvMapping) -> CnvCast:
    """Parse one Sea-Bird DatCnv ASCII file and validate its declared schema."""

    source_path = Path(path)
    filename_match = mapping.identity.filename_pattern.fullmatch(source_path.name)
    if filename_match is None:
        raise _format_error(source_path, "filename does not match the DatCnv convention")

    text = source_path.read_text(encoding="utf-8", errors="strict")
    if text.count("*END*") != 1:
        raise _format_error(source_path, "expected one *END* delimiter")
    header_text, data_text = text.split("*END*", maxsplit=1)

    header: dict[str, str] = {}
    columns: list[CnvColumn] = []
    for line in header_text.splitlines():
        name_match = _NAME_RE.fullmatch(line)
        if name_match is not None:
            columns.append(
                CnvColumn(
                    index=int(name_match.group("index")),
                    source_name=name_match.group("name").strip(),
                    description=name_match.group("description").strip(),
                )
            )
            continue
        if line.startswith("# ") and " = " in line:
            key, value = line[2:].split(" = ", maxsplit=1)
            header[key.strip()] = value.strip()

    try:
        declared_columns = int(header["nquan"])
        declared_rows = int(header["nvalues"])
        bad_flag = float(header["bad_flag"])
    except (KeyError, ValueError) as exc:
        raise _format_error(source_path, f"invalid required header value: {exc}") from exc

    ordered_columns = sorted(columns, key=lambda column: column.index)
    indices = [column.index for column in ordered_columns]
    if len(ordered_columns) != declared_columns or indices != list(range(declared_columns)):
        raise _format_error(
            source_path,
            f"declared {declared_columns} columns but parsed indices {indices}",
        )

    rows: list[np.ndarray] = []
    for row_number, line in enumerate(
        (line for line in data_text.splitlines() if line.strip()), start=1
    ):
        row = np.fromstring(line, sep=" ", dtype=float)
        if row.size != declared_columns:
            raise _format_error(
                source_path,
                f"data row {row_number} expected {declared_columns} columns but found {row.size}",
            )
        rows.append(row)

    if len(rows) != declared_rows:
        raise _format_error(
            source_path,
            f"declared {declared_rows} data rows but found {len(rows)}",
        )

    values = np.vstack(rows).astype(float, copy=False)
    values[values == bad_flag] = np.nan
    elapsed_source = next(
        (
            source_name
            for source_name, variable in mapping.science_variables.items()
            if variable.target == "time_elapsed"
        ),
        None,
    )
    elapsed_index = next(
        (
            column.index
            for column in ordered_columns
            if column.source_name == elapsed_source
        ),
        None,
    )
    if elapsed_index is not None:
        elapsed = values[:, elapsed_index]
        if np.any(np.diff(elapsed[np.isfinite(elapsed)]) < 0):
            raise _format_error(
                source_path, f"nonmonotonic {elapsed_source} values"
            )

    sensors, sensor_xml = _parse_embedded_sensors(source_path, header_text)
    station_source = filename_match.group("station")
    cruise_source = filename_match.group("cruise")
    return CnvCast(
        source_path=source_path,
        cruise_id=_apply_case(cruise_source, mapping.identity.cruise_case),
        station_source=station_source,
        station_id=canonical_station(
            station_source,
            case=mapping.identity.station_case,
            normalize_numeric=mapping.identity.normalize_numeric_station,
        ),
        sequence=int(filename_match.group("sequence")),
        header=header,
        columns=tuple(ordered_columns),
        values=values,
        sensors=sensors,
        sensor_xml=sensor_xml,
    )


def sensor_signature(
    sensors: Sequence[CnvSensor],
) -> tuple[tuple[str, str, str], ...]:
    """Return the identity fields suitable for CNV/XMLCON comparison."""

    return tuple(
        (sensor.sensor_type, sensor.serial_number, sensor.calibration_date)
        for sensor in sensors
    )


def validate_xmlcon(cast: CnvCast, xmlcon_path: Path | str) -> dict[str, object]:
    """Compare embedded CNV sensor identities with a companion XMLCON file."""

    path = Path(xmlcon_path)
    cnv_signature = sensor_signature(cast.sensors)
    if not path.is_file():
        return {
            "status": "missing",
            "cnv_signature": list(cnv_signature),
            "xmlcon_signature": [],
        }

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise _format_error(path, f"invalid XMLCON XML: {exc}") from exc
    wrappers = root.findall(".//Sensor")
    xmlcon_signature = sensor_signature(_sensors_from_wrappers(wrappers))
    return {
        "status": "match" if cnv_signature == xmlcon_signature else "mismatch",
        "cnv_signature": list(cnv_signature),
        "xmlcon_signature": list(xmlcon_signature),
    }


def _finite_range(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("cannot derive coverage from values that are all missing")
    return float(np.min(finite)), float(np.max(finite))


def _utc_iso(unix_seconds: float) -> str:
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _source_processing(cast: CnvCast) -> str:
    stages = ["Sea-Bird DatCnv conversion"]
    if cast.header.get("datcnv_ox_hysteresis_correction", "").lower() == "yes":
        stages.append("oxygen hysteresis correction")
    if cast.header.get("datcnv_ox_tau_correction", "").lower() == "yes":
        stages.append("oxygen tau correction")
    return "; ".join(stages)


def _source_for_role(cast: CnvCast, field: StructuralFieldMapping) -> str:
    available = {column.source_name for column in cast.columns}
    for candidate in field.source_candidates:
        if candidate in available:
            return candidate
    if field.required:
        candidates = ", ".join(field.source_candidates)
        raise CnvFormatError(
            f"{cast.source_path.name}: required structural source is missing; "
            f"tried {candidates}"
        )
    return ""


def _transform_values(values: np.ndarray, transform: TransformMapping) -> np.ndarray:
    transformed = values.astype(float, copy=True)
    if transform.kind == "epoch_offset":
        transformed += transform.offset_seconds
    return transformed


def _metadata_target(metadata: MetadataConfig, role: str) -> str:
    names = getattr(metadata, role)
    return names[0]


def _column_description(cast: CnvCast, source_name: str) -> str:
    return next(
        column.description
        for column in cast.columns
        if column.source_name == source_name
    )


def _coordinate_names(metadata: MetadataConfig) -> str:
    return " ".join(
        _metadata_target(metadata, role)
        for role in ("time", "longitude", "latitude", "depth")
    )


def _structural_attrs(
    role: str,
    source_name: str,
    field: StructuralFieldMapping,
) -> dict[str, object]:
    attrs: dict[str, object] = {
        "source_name": source_name,
        "source_transform": field.transform.kind,
        "coverage_content_type": "coordinate",
    }
    if field.transform.kind == "epoch_offset":
        attrs["source_transform_offset_seconds"] = field.transform.offset_seconds
    if field.reducer:
        attrs["source_reducer"] = field.reducer
    if field.units:
        attrs["units"] = field.units
    if field.calendar:
        attrs["calendar"] = field.calendar

    attrs.update(
        {
            "depth": {
                "standard_name": "depth",
                "long_name": "Depth",
                "positive": "down",
                "axis": "Z",
            },
            "time": {
                "standard_name": "time",
                "long_name": "Profile sample time",
                "axis": "T",
            },
            "latitude": {
                "standard_name": "latitude",
                "long_name": "Profile latitude",
                "axis": "Y",
            },
            "longitude": {
                "standard_name": "longitude",
                "long_name": "Profile longitude",
                "axis": "X",
            },
        }[role]
    )
    return attrs


def build_netcdf_dataset(
    cast: CnvCast,
    mapping: CnvMapping,
    metadata: MetadataConfig,
    output_stem: str,
) -> xr.Dataset:
    """Build one QC-compatible profile Dataset from a mapped CNV cast."""

    sample_count = cast.values.shape[0]
    sample_dimension = metadata.sample_dimension
    profile_values = np.array([0], dtype=np.int32)
    sample_values = np.arange(sample_count, dtype=np.int32)
    ds = xr.Dataset(
        coords={
            "profile": xr.DataArray(
                profile_values,
                dims=("profile",),
                attrs={
                    "cf_role": "profile_id",
                    "long_name": output_stem,
                    "ioos_category": "Identifier",
                    "units": "1",
                },
            ),
            sample_dimension: xr.DataArray(
                sample_values,
                dims=(sample_dimension,),
                attrs={"long_name": "sample index"},
            ),
        }
    )

    structural_values: dict[str, np.ndarray] = {}
    selected_structural_sources: dict[str, str] = {}
    for role in ("depth", "time", "latitude", "longitude"):
        field = mapping.structural_fields[role]
        source_name = _source_for_role(cast, field)
        if not source_name:
            continue
        selected_structural_sources[role] = source_name
        source_values = cast.column(source_name)
        transformed = _transform_values(source_values, field.transform)
        structural_values[role] = transformed

        target = _metadata_target(metadata, role)
        if field.retain_samples_as:
            ds[field.retain_samples_as] = xr.DataArray(
                source_values[np.newaxis, :],
                dims=("profile", sample_dimension),
                attrs={
                    "long_name": f"Source samples for {target}",
                    "source_name": source_name,
                    "source_description": _column_description(cast, source_name),
                    **({"units": field.units} if field.units else {}),
                },
            )

        if field.reducer == "median":
            output_values = np.array([float(np.nanmedian(transformed))])
            dims = ("profile",)
        else:
            output_values = transformed[np.newaxis, :]
            dims = ("profile", sample_dimension)
        ds[target] = xr.DataArray(
            output_values,
            dims=dims,
            attrs=_structural_attrs(role, source_name, field),
        )

    science_targets: list[str] = []
    for column in cast.columns:
        specification = mapping.science_variables.get(column.source_name)
        if specification is None:
            continue
        target = specification.target
        values = cast.column(column.source_name).astype(float, copy=True)
        ds[target] = xr.DataArray(
            values[np.newaxis, :],
            dims=("profile", sample_dimension),
            attrs={
                "units": specification.units,
                "source_name": column.source_name,
                "source_description": column.description,
                "coordinates": _coordinate_names(metadata),
                "grid_mapping": "crs",
                "coverage_content_type": "physicalMeasurement",
            },
        )
        if target not in {
            "scan",
            "flag",
            "time_elapsed",
            "seabird_elapsed_minutes",
            "seabird_elapsed_hours",
            "seabird_julian_day",
        }:
            science_targets.append(target)

    latitude_values = structural_values["latitude"]
    longitude_values = structural_values["longitude"]
    depth_values = structural_values["depth"]
    time_values = structural_values["time"]
    latitude_min, latitude_max = _finite_range(latitude_values)
    longitude_min, longitude_max = _finite_range(longitude_values)
    depth_min, depth_max = _finite_range(depth_values)
    time_start, time_end = _finite_range(time_values)

    ds[metadata.station] = xr.DataArray(
        np.full((1, sample_count), cast.station_id, dtype=object),
        dims=("profile", sample_dimension),
        attrs={"long_name": "Station identifier", "ioos_category": "Identifier"},
    )
    ds[metadata.cruise_id] = xr.DataArray(
        np.full((1, sample_count), cast.cruise_id, dtype=object),
        dims=("profile", sample_dimension),
        attrs={"long_name": "Cruise identifier", "ioos_category": "Identifier"},
    )
    ds["crs"] = xr.DataArray(
        np.int32(0),
        attrs={
            "long_name": "WGS 84 geographic coordinate reference system",
            "grid_mapping_name": "latitude_longitude",
            "longitude_of_prime_meridian": 0.0,
            "semi_major_axis": 6_378_137.0,
            "inverse_flattening": 298.257223563,
            "epsg_code": "EPSG:4326",
        },
    )
    ds["instrument"] = xr.DataArray(
        "",
        attrs={
            "long_name": "Sea-Bird SBE 25plus CTD",
            "make_model": "Sea-Bird SBE 25plus",
            "serial_number": "",
            "calibration_date": "",
        },
    )
    for index, sensor in enumerate(cast.sensors, start=1):
        ds[f"instrument{index}"] = xr.DataArray(
            "",
            attrs={
                "long_name": sensor.sensor_type,
                "make_model": sensor.sensor_type,
                "serial_number": sensor.serial_number,
                "calibration_date": sensor.calibration_date,
                "sensor_type": sensor.sensor_type,
                "channel": sensor.channel,
                "calibration_coefficients": json.dumps(
                    dict(sensor.coefficients), sort_keys=True, separators=(",", ":")
                ),
            },
        )

    title_date = _utc_iso(time_start)[:10]
    depth_target = _metadata_target(metadata, "depth")
    derived_attrs: dict[str, object] = {
        "title": (
            f"CTD data from SFER cruise {cast.cruise_id}, "
            f"station {cast.station_id}, {title_date}"
        ),
        "id": output_stem,
        "station_name": output_stem,
        "instrument": "CTD Sea-Bird SBE 25plus",
        "cdm_profile_variables": ", ".join(["profile", *science_targets]),
        "cdm_altitude_proxy": depth_target,
        "source": "NOAA AOML Sea-Bird DatCnv processed ASCII",
        "source_file": cast.source_path.name,
        "source_format": "Sea-Bird CNV (DatCnv ASCII)",
        "source_station": cast.station_source,
        "source_sequence": cast.sequence,
        "source_processing": _source_processing(cast),
        "source_processing_parameters": json.dumps(
            {
                key: value
                for key, value in cast.header.items()
                if key.startswith("datcnv_")
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        "processing_level": "Geophysical units from Sea-Bird DatCnv output",
        "time_coverage_start": _utc_iso(time_start),
        "time_coverage_end": _utc_iso(time_end),
        "geospatial_lat_min": latitude_min,
        "geospatial_lat_max": latitude_max,
        "geospatial_lat_units": "degrees_north",
        "geospatial_lon_min": longitude_min,
        "geospatial_lon_max": longitude_max,
        "geospatial_lon_units": "degrees_east",
        "geospatial_vertical_min": depth_min,
        "geospatial_vertical_max": depth_max,
        "geospatial_vertical_units": "m",
        "geospatial_vertical_positive": "down",
        "geospatial_bounds_crs": "EPSG:4326",
        "history": "Converted from NOAA AOML Sea-Bird CNV by CTD_QARTOD",
        "cnv_structural_sources": json.dumps(
            selected_structural_sources, sort_keys=True, separators=(",", ":")
        ),
    }
    ds.attrs = derived_attrs
    return ds


def assign_output_stems(casts: Sequence[CnvCast]) -> dict[Path, str]:
    """Assign deterministic, collision-free output stems to parsed casts."""

    assignments: dict[Path, str] = {}
    occurrences: Counter[str] = Counter()
    for cast in sorted(
        casts,
        key=lambda item: (
            item.cruise_id,
            item.station_id,
            item.sequence,
            item.source_path.name,
        ),
    ):
        station_token = cast.station_id.replace(".", "_")
        base = f"{cast.cruise_id}_{station_token}"
        occurrences[base] += 1
        number = occurrences[base]
        assignments[cast.source_path] = base if number == 1 else f"{base}-{number}"
    return assignments


def write_netcdf(
    ds: xr.Dataset,
    path: Path | str,
    overwrite: bool = False,
) -> Path:
    """Write a Dataset completely before atomically installing the final file."""

    output_path = Path(path)
    temporary_path = output_path.with_name(f".{output_path.name}.cnvtmp")
    if output_path.exists() and not overwrite:
        ds.close()
        raise FileExistsError(f"refusing to overwrite existing {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoding = {
        name: {"dtype": "float64", "zlib": True, "complevel": 4}
        for name, variable in ds.variables.items()
        if np.issubdtype(variable.dtype, np.floating) and variable.ndim > 0
    }
    try:
        ds.to_netcdf(
            temporary_path,
            mode="w",
            format="NETCDF4",
            engine="netcdf4",
            encoding=encoding,
        )
        ds.close()
        temporary_path.replace(output_path)
    except Exception:
        ds.close()
        if temporary_path.exists():
            temporary_path.unlink()
        raise
    return output_path


def _source_base(path: Path) -> str:
    suffix = "_datcnv_processed"
    stem = path.stem
    return stem[: -len(suffix)] if stem.lower().endswith(suffix) else stem


def _selected_structural_sources(
    cast: CnvCast, mapping: CnvMapping
) -> dict[str, str]:
    return {
        role: _source_for_role(cast, mapping.structural_fields[role])
        for role in ("depth", "time", "latitude", "longitude")
    }


def _coverage_record(cast: CnvCast, mapping: CnvMapping) -> dict[str, object]:
    sources = _selected_structural_sources(cast, mapping)
    values = {
        role: _transform_values(
            cast.column(source_name), mapping.structural_fields[role].transform
        )
        for role, source_name in sources.items()
    }
    depth_min, depth_max = _finite_range(values["depth"])
    latitude_min, latitude_max = _finite_range(values["latitude"])
    longitude_min, longitude_max = _finite_range(values["longitude"])
    time_min, time_max = _finite_range(values["time"])
    return {
        "sample_count": int(cast.values.shape[0]),
        "depth_min_m": depth_min,
        "depth_max_m": depth_max,
        "latitude_min": latitude_min,
        "latitude_max": latitude_max,
        "longitude_min": longitude_min,
        "longitude_max": longitude_max,
        "time_start": _utc_iso(time_min),
        "time_end": _utc_iso(time_max),
    }


def _write_report(path: Path, payload: Mapping[str, object]) -> None:
    temporary_path = path.with_name(f".{path.name}.cnvtmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def convert_cnv_directory(
    input_dir: Path | str,
    output_dir: Path | str,
    profile: DatasetProfile,
    overwrite: bool = False,
) -> ConversionReport:
    """Convert all top-level CNV files and write a complete batch report."""

    source_root = Path(input_dir)
    destination_root = Path(output_dir)
    if not source_root.is_dir():
        raise NotADirectoryError(f"CNV input directory does not exist: {source_root}")
    source_resolved = source_root.resolve()
    destination_resolved = destination_root.resolve()
    if destination_resolved == source_resolved or destination_resolved.is_relative_to(
        source_resolved
    ):
        raise ValueError("output directory must be outside the CNV source directory")

    mapping_path = resolve_config_path("cnv_mapping", profile)
    qc_mapping_path = resolve_config_path("variable_mapping", profile)
    mapping = load_cnv_mapping(mapping_path)
    qc_mapping = load_mapping(qc_mapping_path)
    inventory = sorted(path for path in source_root.iterdir() if path.is_file())
    by_suffix: dict[str, list[Path]] = {}
    for path in inventory:
        by_suffix.setdefault(path.suffix.lower(), []).append(path)
    cnv_paths = by_suffix.get(".cnv", [])
    xmlcon_paths = by_suffix.get(".xmlcon", [])
    cnv_base_keys = {_source_base(path).casefold() for path in cnv_paths}
    xmlcon_by_key = {path.stem.casefold(): path for path in xmlcon_paths}

    parsed_casts: list[CnvCast] = []
    failures: list[dict[str, object]] = []
    for cnv_path in cnv_paths:
        try:
            parsed_casts.append(parse_cnv(cnv_path, mapping))
        except Exception as exc:
            failures.append(
                {
                    "source_file": cnv_path.name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    output_stems = assign_output_stems(parsed_casts)
    converted: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    companion_suffixes = (".xmlcon", ".hex", ".hdr", ".mrk", ".bl")
    companions_by_suffix = {
        suffix: {path.stem.casefold(): path for path in by_suffix.get(suffix, [])}
        for suffix in companion_suffixes
    }
    duplicate_groups: dict[tuple[str, str], list[dict[str, object]]] = {}

    for cast in sorted(
        parsed_casts,
        key=lambda item: (
            item.cruise_id,
            item.station_id,
            item.sequence,
            item.source_path.name,
        ),
    ):
        stem = output_stems[cast.source_path]
        output_path = destination_root / cast.cruise_id / f"{stem}.nc"
        base_key = _source_base(cast.source_path).casefold()
        xmlcon_path = xmlcon_by_key.get(base_key)
        xmlcon_result = validate_xmlcon(
            cast, xmlcon_path if xmlcon_path is not None else source_root / "missing.XMLCON"
        )
        companion_status = {
            suffix.removeprefix("."): (
                companions_by_suffix[suffix][base_key].name
                if base_key in companions_by_suffix[suffix]
                else None
            )
            for suffix in companion_suffixes
        }
        record = {
            "source_file": cast.source_path.name,
            "cruise_id": cast.cruise_id,
            "station_source": cast.station_source,
            "station_id": cast.station_id,
            "sequence": cast.sequence,
            "output_stem": stem,
            "output_file": str(output_path),
            "coverage": _coverage_record(cast, mapping),
            "structural_sources": _selected_structural_sources(cast, mapping),
            "xmlcon": xmlcon_result,
            "companions": companion_status,
        }
        duplicate_groups.setdefault((cast.cruise_id, cast.station_id), []).append(record)
        try:
            ds = build_netcdf_dataset(cast, mapping, profile.metadata, stem)
            write_netcdf(ds, output_path, overwrite=overwrite)
            converted.append(record)
        except FileExistsError as exc:
            skipped.append({**record, "reason": str(exc)})
        except Exception as exc:
            failures.append(
                {
                    "source_file": cast.source_path.name,
                    "cruise_id": cast.cruise_id,
                    "station_id": cast.station_id,
                    "sequence": cast.sequence,
                    "output_file": str(output_path),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    schema_counts: Counter[tuple[str, ...]] = Counter(
        tuple(column.source_name for column in cast.columns) for cast in parsed_casts
    )
    schemas = [
        {"columns": list(schema), "cast_count": count}
        for schema, count in sorted(schema_counts.items())
    ]
    unknown_columns = sorted(
        {
            column.source_name
            for cast in parsed_casts
            for column in cast.columns
            if column.source_name
            not in {
                *mapping.science_variables,
                *(
                    candidate
                    for field in mapping.structural_fields.values()
                    for candidate in field.source_candidates
                ),
            }
        }
    )
    qc_variables = flatten_mapping(qc_mapping)
    science_targets = {
        variable.target for variable in mapping.science_variables.values()
    }
    duplicate_resolutions = [
        {
            "cruise_id": cruise_id,
            "station_id": station_id,
            "outputs": [str(record["output_stem"]) for record in records],
            "source_sequences": [int(record["sequence"]) for record in records],
        }
        for (cruise_id, station_id), records in sorted(duplicate_groups.items())
        if len(records) > 1
    ]
    missing_processed_cnv = [
        {"source_stem": path.stem, "xmlcon_file": path.name}
        for path in sorted(xmlcon_paths, key=lambda item: item.name.casefold())
        if path.stem.casefold() not in cnv_base_keys
    ]
    companion_counts = {
        suffix.removeprefix("."): len(by_suffix.get(suffix, []))
        for suffix in (".cnv", *companion_suffixes)
    }
    known_suffix_count = sum(companion_counts.values())
    companion_counts["other"] = len(inventory) - known_suffix_count

    report_path = destination_root / "conversion_report.json"
    payload: dict[str, object] = {
        "converter": "CTD_QARTOD cnv_converter",
        "converter_version": "0.2.0",
        "conversion_time_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "input_directory": str(source_root),
        "output_directory": str(destination_root),
        "dataset_profile": (
            str(profile.profile_path) if profile.profile_path is not None else None
        ),
        "cnv_mapping": str(mapping_path),
        "qc_variable_mapping": str(qc_mapping_path),
        "counts": {
            "cnv_discovered": len(cnv_paths),
            "parsed": len(parsed_casts),
            "converted": len(converted),
            "skipped": len(skipped),
            "failed": len(failures),
        },
        "companion_counts": companion_counts,
        "converted": converted,
        "skipped": skipped,
        "failed": failures,
        "missing_processed_cnv": missing_processed_cnv,
        "schemas": schemas,
        "unknown_columns": unknown_columns,
        "source_to_target": {
            source: variable.target
            for source, variable in mapping.science_variables.items()
        },
        "structural_fields": {
            role: {
                "source_candidates": list(field.source_candidates),
                "output": _metadata_target(profile.metadata, role),
                "transform": field.transform.kind,
                "offset_seconds": field.transform.offset_seconds,
                "reducer": field.reducer,
                "retain_samples_as": field.retain_samples_as,
            }
            for role, field in mapping.structural_fields.items()
        },
        "mapped_variables_absent_from_qc_mapping": sorted(
            science_targets - qc_variables
        ),
        "qc_mapping_coverage": {
            "mapped_science_variable_count": len(science_targets),
            "qc_mapped_variable_count": len(science_targets & qc_variables),
        },
        "duplicate_resolutions": duplicate_resolutions,
        "metadata_gaps": [
            "platform call sign and external platform identifiers",
            "creator and contributor identities and contact details",
            "publication and metadata modification dates",
            "acknowledgment, references, info URL, and metadata URL",
            "fixed program-level geospatial bounds",
            "processing stages not recorded in the CNV header",
        ],
    }
    report = ConversionReport(report_path=report_path, data=payload)
    _write_report(report_path, report.as_dict())
    return report
