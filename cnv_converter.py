"""Convert the confirmed Sea-Bird CNV format to profile NetCDF files."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

import numpy as np
import xarray as xr

from cnv_catalog import catalog, normalize_unit
from cnv_coordinates import resolve_header_coordinates
from cnv_metadata import FIXED_DEFAULTS, publication_metadata, validate_ownership
from station_names import STATION_ALIASES

from cnv_mapping import (
    CnvHeaderColumn,
    CnvMapping,
    CnvSensor,
    DERIVED_SENSOR_TAG,
    SourceCandidate,
    cf_standard_name_url,
    discover_cnv_files,
    load_cnv_mapping,
    parse_cnv_sensors,
    read_cnv_header,
)


_CRUISE_RE = r"(?:(?:WS|WB|SV|SAV)\d{4,5}|HG\d{2,5})[A-Za-z]?"
_FILENAME_RE = re.compile(
    rf"^(?P<cruise>{_CRUISE_RE})(?:[_. -]?(?:Stn|Sta|Stv)[._ ]*|[_-])(?P<station>[A-Za-z0-9][A-Za-z0-9._-]*)\.cnv$",
    re.IGNORECASE,
)
_NUMERIC_STATION_RE = re.compile(r"^(?P<number>\d+)(?P<suffix>.*)$")
_PROCESSING_STAGES = {
    "datcnv",
    "filter",
    "alignctd",
    "celltm",
    "loopedit",
    "derive",
}


@dataclass(frozen=True)
class CnvCast:
    """One parsed cast with its filename-derived identity."""

    source_path: Path
    cruise_id: str
    station_id: str
    embedded_filename: str | None
    columns: tuple[CnvHeaderColumn, ...]
    values: np.ndarray
    header_coordinates: Mapping[str, float] | None
    destination_sources: Mapping[str, SourceCandidate]
    instrument_model: str
    sensors: tuple[CnvSensor, ...]
    sensor_parse_status: str
    start_time: datetime
    interval_seconds: float
    processing: tuple[str, ...]
    header_keys: tuple[str, ...]
    warnings: tuple[str, ...]

    def column(self, source_name: str, occurrence: int = 1) -> np.ndarray:
        for column in self.columns:
            if (
                column.source_name == source_name
                and column.occurrence == occurrence
            ):
                return self.values[:, column.index]
        raise KeyError((source_name, occurrence))


class CnvFormatError(ValueError):
    """Raised when a CNV file violates the confirmed source contract."""


def _format_error(path: Path, detail: str) -> CnvFormatError:
    return CnvFormatError(f"{path.name}: {detail}")


def _station_id(value: str) -> str:
    match = _NUMERIC_STATION_RE.fullmatch(value.strip())
    if match is None:
        return value.strip()
    return f"{int(match.group('number'))}{match.group('suffix')}"


def filename_identity(path: Path | str) -> tuple[str, str]:
    """Derive cruise and station only from the actual CNV basename."""

    source = Path(path)
    match = _FILENAME_RE.fullmatch(source.name)
    if match is None:
        raise _format_error(
            source,
            "filename must match <cruiseID>_Stn.<station>.cnv or a reviewed cruise/station delimiter pattern",
        )
    cruise = match.group("cruise").upper()
    station = match.group("station")
    station_part, separator, running_number = station.rpartition("_")
    if separator and re.fullmatch(r"\d+", running_number):
        station = station_part
    station = _station_id(station)
    station = STATION_ALIASES.get(station.lower(), station)
    station = station.replace(".", "_")
    if re.search(r"(?i)(deck|dunk|wettest|test|tst|recast|surface|do_up)", station):
        raise _format_error(source, "test/recast filename needs explicit identity review")
    if not re.fullmatch(r"(?:\d+(?:[._-]\d+)?[A-Za-z]*|[A-Za-z]+\d*(?:[._]\d+)?[A-Za-z]?|[A-Za-z]+-\d+)", station):
        raise _format_error(source, "ambiguous station token; identity review required")
    parent_cruise = re.fullmatch(rf"({_CRUISE_RE})_cnv", source.parent.name, re.I)
    if parent_cruise and parent_cruise[1].upper() != cruise:
        raise _format_error(source, "filename cruise differs from cruise folder; identity review required")
    return cruise, station


def _embedded_stem(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).stem


def _header_fields(header_lines: Sequence[str]) -> tuple[dict[str, str], tuple[str, ...]]:
    fields: dict[str, str] = {}
    processing: list[str] = []
    for line in header_lines:
        if not line.startswith(("* ", "# ")):
            continue
        content = line[2:].strip()
        key = content.split(maxsplit=1)[0].split(":", 1)[0]
        if key.split("_", 1)[0].casefold() in _PROCESSING_STAGES:
            processing.append(content)
        elif " = " in content:
            name, value = content.split(" = ", 1)
            fields[name.strip()] = value.strip()
    return fields, tuple(processing)


def _mapped_destinations(
    path: Path,
    columns: Sequence[CnvHeaderColumn],
    mapping: CnvMapping,
) -> dict[str, SourceCandidate]:
    structural = {
        *mapping.required_fields.values(),
        mapping.vertical_field,
    }
    destination_counts: Counter[str] = Counter()
    destinations: dict[str, SourceCandidate] = {}
    for column in columns:
        candidate = SourceCandidate(column.source_name, column.occurrence)
        if candidate in structural:
            continue
        specification = mapping.science_variables.get(column.source_name)
        if specification is None:
            raise _format_error(
                path, f"unmapped source variable {column.source_name!r}"
            )
        if specification.action == "ignore":
            continue
        base = specification.target_name
        if base is None:
            raise AssertionError("loaded map action requires a target")
        destination_counts[base] += 1
        number = destination_counts[base]
        destination = base if number == 1 else f"{base}_{number}"
        if destination in destinations:
            raise _format_error(
                path, f"mapped destination collision for {destination!r}"
            )
        destinations[destination] = candidate
    return destinations


def parse_cnv(path: Path | str, mapping: CnvMapping) -> CnvCast:
    """Parse one final-format CNV file using its actual filename identity."""

    source = Path(path)
    cruise_id, station_id = filename_identity(source)
    lines = source.read_text(encoding="utf-8", errors="strict").splitlines()
    delimiters = [index for index, line in enumerate(lines) if line == "*END*"]
    if len(delimiters) != 1:
        raise _format_error(source, "expected one standalone *END* delimiter")
    header_lines = lines[: delimiters[0]]
    data_lines = lines[delimiters[0] + 1 :]
    if (
        not header_lines
        or not header_lines[0].startswith("* Sea-Bird ")
        or not header_lines[0].endswith(" Data File:")
    ):
        raise _format_error(source, "missing Sea-Bird model line")
    instrument_model = header_lines[0][2:-11]
    columns = read_cnv_header(source, require_coordinates=False)
    try:
        header_coordinates = resolve_header_coordinates([column.source_name for column in columns], header_lines)
    except ValueError as exc:
        raise _format_error(source, str(exc)) from exc
    fields, processing = _header_fields(header_lines)
    try:
        nquan = int(fields["nquan"])
        nvalues = int(fields["nvalues"])
        bad_flag = float(fields["bad_flag"])
        interval_match = re.fullmatch(r"seconds:\s*(.+)", fields["interval"])
        if interval_match is None:
            raise ValueError("interval must use seconds:")
        interval = float(interval_match.group(1))
        start_time = datetime.strptime(
            fields["start_time"].split("[", 1)[0].strip(),
            "%b %d %Y %H:%M:%S",
        ).replace(tzinfo=timezone.utc)
    except (KeyError, ValueError) as exc:
        raise _format_error(source, f"invalid required header value: {exc}") from exc
    if nquan != len(columns):
        raise _format_error(source, "nquan does not match the column declarations")
    if nvalues < 1:
        raise _format_error(source, "CNV file has no data rows")
    if not np.isfinite(interval) or interval <= 0 or not np.isfinite(bad_flag):
        raise _format_error(source, "invalid interval or bad_flag")

    rows: list[np.ndarray] = []
    for row_number, line in enumerate(
        (line for line in data_lines if line.strip()), start=1
    ):
        row = np.fromstring(line, sep=" ", dtype=float)
        if row.size != nquan:
            raise _format_error(
                source,
                f"data row {row_number} does not match the column declarations",
            )
        rows.append(row)
    if len(rows) != nvalues:
        raise _format_error(source, "nvalues does not match the numerical table")
    values = np.vstack(rows).astype(float, copy=False)
    values[values == bad_flag] = np.nan

    declared = {
        SourceCandidate(column.source_name, column.occurrence)
        for column in columns
    }
    required = {*mapping.required_fields.values(), mapping.vertical_field}
    if header_coordinates is not None:
        required -= {mapping.required_fields["latitude"], mapping.required_fields["longitude"]}
    missing = required - declared
    if missing:
        names = ", ".join(
            f"{item.name} occurrence {item.occurrence}"
            for item in sorted(missing, key=lambda item: (item.name, item.occurrence))
        )
        raise _format_error(source, f"missing required source column: {names}")

    destinations = _mapped_destinations(source, columns, mapping)
    time_candidate = mapping.required_fields["time"]
    elapsed = values[
        :,
        next(
            column.index
            for column in columns
            if SourceCandidate(column.source_name, column.occurrence)
            == time_candidate
        ),
    ]
    if not np.all(np.isfinite(elapsed)) or np.any(np.diff(elapsed) < 0):
        raise _format_error(source, "timeS values must be finite and monotonic")
    latitude = (np.array([header_coordinates["latitude"]]) if header_coordinates is not None
                else values[:, next(column.index for column in columns if column.source_name == "latitude")])
    longitude = (np.array([header_coordinates["longitude"]]) if header_coordinates is not None
                 else values[:, next(column.index for column in columns if column.source_name == "longitude")])
    if (
        not np.all(np.isfinite(latitude))
        or np.any((latitude < -90) | (latitude > 90))
        or not np.all(np.isfinite(longitude))
        or np.any((longitude < -180) | (longitude > 180))
    ):
        raise _format_error(source, "latitude or longitude samples are invalid")

    sensors, sensor_status, sensor_warnings = parse_cnv_sensors(header_lines)
    warnings = list(sensor_warnings)
    embedded_filename = fields.get("FileName")
    if (
        embedded_filename
        and _embedded_stem(embedded_filename).casefold() != source.stem.casefold()
    ):
        warnings.append("embedded filename differs from actual filename")
    known_fields = {
        "FileName",
        "NMEA Latitude",
        "NMEA Longitude",
        "nquan",
        "nvalues",
        "bad_flag",
        "interval",
        "start_time",
    }
    return CnvCast(
        source_path=source,
        cruise_id=cruise_id,
        station_id=station_id,
        embedded_filename=embedded_filename,
        columns=columns,
        values=values,
        header_coordinates=header_coordinates,
        destination_sources=destinations,
        instrument_model=instrument_model,
        sensors=sensors,
        sensor_parse_status=sensor_status,
        start_time=start_time,
        interval_seconds=interval,
        processing=processing,
        header_keys=tuple(sorted(set(fields) - known_fields)),
        warnings=tuple(warnings),
    )


def _source_column(cast: CnvCast, candidate: SourceCandidate) -> CnvHeaderColumn:
    return next(
        column
        for column in cast.columns
        if SourceCandidate(column.source_name, column.occurrence) == candidate
    )


def _finite_range(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if not finite.size:
        raise CnvFormatError("cannot calculate metadata from all-missing values")
    return float(finite.min()), float(finite.max())


def _range_attributes(values: np.ndarray) -> dict[str, float]:
    finite = values[np.isfinite(values)]
    if not finite.size:
        return {}
    return {"valid_min": float(finite.min()), "valid_max": float(finite.max())}


def _utc_iso(value: datetime) -> str:
    utc_value = value.astimezone(timezone.utc)
    timespec = "microseconds" if utc_value.microsecond else "seconds"
    return utc_value.isoformat(timespec=timespec).replace("+00:00", "Z")


def _iso_duration(seconds: float) -> str:
    return f"PT{seconds:.7g}S"


def _instrument_reference(
    cast: CnvCast,
    mapping: CnvMapping,
    destination: str,
    candidate: SourceCandidate,
) -> str | None:
    specification = mapping.science_variables[candidate.name]
    if specification.sensor_tag is None:
        return None
    if specification.sensor_tag == DERIVED_SENSOR_TAG:
        return None
    matching = [
        index
        for index, sensor in enumerate(cast.sensors, start=1)
        if sensor.sensor_type == specification.sensor_tag
    ]
    if not matching:
        raise _format_error(
            cast.source_path,
            f"mapped sensor_tag {specification.sensor_tag!r} for "
            f"{candidate.name!r} is absent from the embedded Sensors XML",
        )
    target_number = 0
    for current_destination, current_candidate in cast.destination_sources.items():
        current = mapping.science_variables[current_candidate.name]
        if current.target_name == specification.target_name:
            target_number += 1
        if current_destination == destination:
            break
    # ponytail: embedded sensor type and order are the only portable link; add
    # explicit per-source sensor links only if a dataset disproves that ordering.
    return f"instrument{matching[min(target_number, len(matching)) - 1]}"


def _processing_comment(processing: Sequence[str]) -> str | None:
    useful: list[str] = []
    for item in processing:
        key = item.split(" = ", 1)[0].casefold()
        if key.endswith(("_date", "_in")) or "\\" in item:
            continue
        useful.append(item)
    return "; ".join(useful) or None


def _build_dataset(
    cast: CnvCast,
    mapping: CnvMapping,
    stem: str,
    source: str,
    target: str,
    netcdf_global_attributes: Mapping[str, object],
    fixed_attributes: Mapping[str, object],
    derived_attributes: Mapping[str, object],
) -> xr.Dataset:
    time_candidate = mapping.required_fields["time"]
    latitude_candidate = mapping.required_fields["latitude"]
    longitude_candidate = mapping.required_fields["longitude"]
    depth_candidate = mapping.vertical_field
    time = cast.column(time_candidate.name, time_candidate.occurrence)
    header_coordinates = cast.header_coordinates
    latitude = (np.array([header_coordinates["latitude"]]) if header_coordinates is not None
                else cast.column(latitude_candidate.name, latitude_candidate.occurrence))
    longitude = (np.array([header_coordinates["longitude"]]) if header_coordinates is not None
                 else cast.column(longitude_candidate.name, longitude_candidate.occurrence))
    coordinate_dims = ("profile",) if header_coordinates is not None else ("profile", "z")
    depth = cast.column(depth_candidate.name, depth_candidate.occurrence)
    time_min, time_max = _finite_range(time)
    latitude_min, latitude_max = _finite_range(latitude)
    longitude_min, longitude_max = _finite_range(longitude)
    depth_min, depth_max = _finite_range(depth)
    depth_units = _source_column(cast, depth_candidate).units or "m"
    rows = time.size
    coordinates = "time longitude latitude depth"
    data: dict[str, tuple[tuple[str, ...], object, dict[str, object]]] = {
        "profile": (
            ("profile",),
            np.array([0], dtype=np.int32),
            {"cf_role": "profile_id", "long_name": stem},
        ),
        "time": (
            ("profile", "z"),
            time[None, :],
            {
                "long_name": "Elapsed Time in Seconds",
                "standard_name": "time",
                "axis": "T",
                "units": f"seconds since {cast.start_time.isoformat()}",
                "calendar": "proleptic_gregorian",
                "coordinates": coordinates,
                "coverage_content_type": "physicalMeasurement",
                "ioos_category": "Time",
                **_range_attributes(time),
            },
        ),
        "depth": (
            ("profile", "z"),
            depth[None, :],
            {
                "long_name": "Depth",
                "standard_name": "depth",
                "units": depth_units,
                "positive": "down",
                "axis": "Z",
                "coordinates": coordinates,
                "grid_mapping": "crs",
                "coverage_content_type": "physicalMeasurement",
                **_range_attributes(depth),
            },
        ),
        "latitude": (
            coordinate_dims,
            latitude if header_coordinates is not None else latitude[None, :],
            {
                "standard_name": "latitude",
                "long_name": "Latitude",
                "units": "degrees_north",
                "axis": "Y",
                **({"source_header": "NMEA Latitude"} if header_coordinates is not None else {}),
                **_range_attributes(latitude),
            },
        ),
        "longitude": (
            coordinate_dims,
            longitude if header_coordinates is not None else longitude[None, :],
            {
                "standard_name": "longitude",
                "long_name": "Longitude",
                "units": "degrees_east",
                "axis": "X",
                **({"source_header": "NMEA Longitude"} if header_coordinates is not None else {}),
                **_range_attributes(longitude),
            },
        ),
        "station": (
            ("profile", "z"),
            np.full((1, rows), cast.station_id, dtype=object),
            {"long_name": "Station identifier", "ioos_category": "Identifier"},
        ),
        "cruiseID": (
            ("profile", "z"),
            np.full((1, rows), cast.cruise_id, dtype=object),
            {"long_name": "Cruise identifier", "ioos_category": "Identifier"},
        ),
        "crs": (
            (),
            np.int32(0),
            {
                "grid_mapping_name": "latitude_longitude",
                "longitude_of_prime_meridian": 0.0,
                "semi_major_axis": 6378137.0,
                "inverse_flattening": 298.257223563,
                "epsg_code": "EPSG:4326",
            },
        ),
        "instrument": (
            (),
            "",
            {
                "long_name": f"CTD {cast.instrument_model}",
                "make_model": cast.instrument_model,
                "serial_number": "",
                "calibration_date": "",
            },
        ),
    }
    for number, sensor in enumerate(cast.sensors, start=1):
        attributes: dict[str, object] = {
            "long_name": sensor.sensor_type,
            "make_model": sensor.sensor_type,
            "channel": sensor.channel,
        }
        if sensor.serial_number is not None:
            attributes["serial_number"] = sensor.serial_number
        if sensor.calibration_date is not None:
            attributes["calibration_date"] = sensor.calibration_date
        data[f"instrument{number}"] = ((), "", attributes)
    for destination, candidate in cast.destination_sources.items():
        source_column = _source_column(cast, candidate)
        values = cast.column(candidate.name, candidate.occurrence)
        specification = mapping.science_variables[candidate.name]
        attributes: dict[str, object] = {
            "long_name": source_column.description or destination,
            **specification.attributes,
            "source_name": candidate.name,
            "source_occurrence": candidate.occurrence,
            "source_description": source_column.description,
            "coordinates": coordinates,
            "grid_mapping": "crs",
            "coverage_content_type": "physicalMeasurement",
            **_range_attributes(values),
        }
        unit = normalize_unit(source_column.units)
        definition = catalog()["variables"][specification.target_name]
        if not re.search(definition["description"], source_column.description):
            raise _format_error(cast.source_path, f"{candidate.name}: source description conflicts with catalog target {specification.target_name!r}")
        if unit is not None:
            if unit not in definition["units"]:
                raise _format_error(cast.source_path, f"{candidate.name}: source units {source_column.units!r} are incompatible with catalog target {specification.target_name!r}; unit conversion is required")
            attributes["units"] = unit
            if unit != source_column.units:
                attributes["source_units"] = source_column.units
        instrument = _instrument_reference(
            cast, mapping, destination, candidate
        )
        if instrument is not None:
            attributes["instrument"] = instrument
        data[destination] = (("profile", "z"), values[None, :], attributes)

    dataset = xr.Dataset(data_vars=data)
    for variable in dataset.variables.values():
        standard_name = variable.attrs.get("standard_name")
        if standard_name is not None:
            variable.attrs["standard_name_url"] = cf_standard_name_url(
                str(standard_name)
            )
    profile_variables = [
        "profile",
        "depth",
        *cast.destination_sources,
    ]
    generated_attributes: dict[str, object] = {
        "station_name": stem,
        "instrument": f"CTD {cast.instrument_model}",
        "source_file": cast.source_path.name,
        "cdm_profile_variables": ", ".join(profile_variables),
        "history": (
            f"{_utc_iso(datetime.now(timezone.utc))}: converted {source} "
            f"to {target} by CTD_QARTOD"
        ),
        "time_coverage_start": _utc_iso(
            cast.start_time + timedelta(seconds=time_min)
        ),
        "time_coverage_end": _utc_iso(
            cast.start_time + timedelta(seconds=time_max)
        ),
        "time_coverage_duration": _iso_duration(time_max - time_min),
        "time_coverage_resolution": _iso_duration(cast.interval_seconds),
        "geospatial_lat_min": latitude_min,
        "geospatial_lat_max": latitude_max,
        "geospatial_lon_min": longitude_min,
        "geospatial_lon_max": longitude_max,
        "geospatial_vertical_min": depth_min,
        "geospatial_vertical_max": depth_max,
        "geospatial_vertical_units": depth_units,
    }
    comment = _processing_comment(cast.processing)
    if comment is not None:
        generated_attributes["comment"] = comment
    validate_ownership(netcdf_global_attributes, fixed_attributes, derived_attributes)
    fixed = {**FIXED_DEFAULTS, **{key: value for key, value in fixed_attributes.items() if value is not None}}
    generated_attributes.update(publication_metadata(
        cast.cruise_id, stem, cast.start_time.date().isoformat(),
        float(latitude[0]), float(longitude[0]), fixed, derived_attributes,
    ))
    configured_attributes = {
        name: value
        for name, value in netcdf_global_attributes.items()
        if value is not None
    }
    converter_owned = generated_attributes.keys() | {"comment"}
    conflicts = sorted(configured_attributes.keys() & converter_owned)
    if conflicts:
        raise ValueError(
            "netcdf_global_attributes cannot override generated attributes: "
            + ", ".join(conflicts)
        )
    dataset.attrs = {**configured_attributes, **fixed, **generated_attributes}
    return dataset


def _validate_temporary(path: Path, cast: CnvCast, stem: str) -> None:
    with xr.open_dataset(path, decode_cf=False) as dataset:
        if (
            dataset.sizes.get("profile") != 1
            or dataset.sizes.get("z") != cast.values.shape[0]
        ):
            raise ValueError("temporary NetCDF dimensions do not match the cast")
        for name in ("time", "longitude", "latitude", "depth"):
            expected_dims = (("profile",) if name in {"latitude", "longitude"}
                             and cast.header_coordinates is not None else ("profile", "z"))
            if name not in dataset or dataset[name].dims != expected_dims:
                raise ValueError(
                    f"temporary NetCDF structural variable {name!r} is invalid"
                )
        if (
            dataset.attrs.get("id") != f"SFER_CTD_{stem}"
            or dataset.attrs.get("station_name") != stem
        ):
            raise ValueError("temporary NetCDF identity attributes are invalid")
        if (
            str(dataset["station"].values[0, 0]) != cast.station_id
            or str(dataset["cruiseID"].values[0, 0]) != cast.cruise_id
        ):
            raise ValueError("temporary NetCDF identity values are invalid")


def _write_cast(
    cast: CnvCast,
    mapping: CnvMapping,
    target: Path,
    stem: str,
    source: str,
    output_root: Path,
    overwrite: bool,
    netcdf_global_attributes: Mapping[str, object],
    fixed_attributes: Mapping[str, object],
    derived_attributes: Mapping[str, object],
) -> None:
    if target.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    dataset: xr.Dataset | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp.nc",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        relative_target = target.relative_to(output_root).as_posix()
        dataset = _build_dataset(
            cast,
            mapping,
            stem,
            source,
            relative_target,
            netcdf_global_attributes,
            fixed_attributes,
            derived_attributes,
        )
        dataset.to_netcdf(
            temporary, mode="w", format="NETCDF4", engine="netcdf4"
        )
        dataset.close()
        dataset = None
        _validate_temporary(temporary, cast, stem)
        os.replace(temporary, target)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    finally:
        if dataset is not None:
            dataset.close()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def convert_cnv(
    input_path: Path | str,
    output_root: Path | str,
    mapping_path: Path | str,
    report_path: Path | str | None = None,
    overwrite: bool = False,
    *,
    netcdf_global_attributes: Mapping[str, object] | None = None,
    netcdf_fixed_global_attributes: Mapping[str, object] | None = None,
    netcdf_derived_global_attributes: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Convert one CNV file or every CNV recursively found under a folder."""

    paths = discover_cnv_files(input_path)
    output = Path(output_root)
    source_input = Path(input_path)
    if source_input.is_dir():
        source_resolved = source_input.resolve()
        output_resolved = output.resolve()
        if output_resolved == source_resolved or output_resolved.is_relative_to(
            source_resolved
        ):
            raise ValueError("output directory must be outside the CNV source directory")
        source_base = source_input
    else:
        source_base = source_input.parent
    mapping = load_cnv_mapping(mapping_path)
    global_attributes = netcdf_global_attributes or {}
    fixed_attributes = netcdf_fixed_global_attributes or {}
    derived_attributes = netcdf_derived_global_attributes or {}
    validate_ownership(global_attributes, fixed_attributes, derived_attributes)
    index_path = output / "conversion_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    if not isinstance(index, dict) or not all(isinstance(k, str) and isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\.nc", v) and ".." not in v for k, v in index.items()):
        raise ValueError("invalid conversion_index.json")
    reserved = set(index.values())

    records: dict[Path, dict[str, object]] = {}
    parsed: list[CnvCast] = []
    for path in paths:
        source = path.relative_to(source_base).as_posix()
        record: dict[str, object] = {"source": source, "warnings": []}
        records[path] = record
        try:
            cast = parse_cnv(path, mapping)
        except Exception as exc:
            record.update(
                {
                    "status": "failed",
                    "failure_reason": str(exc),
                }
            )
            continue
        record.update(
            {
                "status": "prepared",
                "cruise_id": cast.cruise_id,
                "station": cast.station_id,
                "embedded_filename": cast.embedded_filename,
                "start_time": cast.start_time.isoformat(),
                "warnings": list(cast.warnings),
                "sensor_parse_status": cast.sensor_parse_status,
                "instrument_count": len(cast.sensors),
                "coordinate_source": "nmea_header" if cast.header_coordinates is not None else "data_columns",
                "schema": [
                    {
                        "name": column.source_name,
                        "occurrence": column.occurrence,
                    }
                    for column in cast.columns
                ],
                "resolved_source_map": {
                    destination: {
                        "name": candidate.name,
                        "occurrence": candidate.occurrence,
                    }
                    for destination, candidate in cast.destination_sources.items()
                },
            }
        )
        parsed.append(cast)

    groups: dict[tuple[str, str], list[CnvCast]] = defaultdict(list)
    for cast in parsed:
        groups[(cast.cruise_id, cast.station_id)].append(cast)
    for group in groups.values():
        group.sort(
            key=lambda cast: (
                cast.start_time,
                cast.source_path.as_posix().casefold(),
            )
        )
        for repeat, cast in enumerate(group, start=1):
            stem = f"{cast.cruise_id}_{cast.station_id}" + (
                "" if repeat == 1 else f"-{repeat}"
            )
            target = output / cast.cruise_id / f"{stem}.nc"
            source_key = str(cast.source_path.resolve())
            if source_key in index:
                target = output / index[source_key]
                stem = target.stem
                base = f"{cast.cruise_id}_{cast.station_id}"
                if target.parent != output / cast.cruise_id or not re.fullmatch(re.escape(base) + r"(?:-\d+)?", stem):
                    raise ValueError(f"conversion index identity differs from source {source_key}")
                repeat = 1 if stem == base else int(stem[len(base) + 1:])
            else:
                while target.relative_to(output).as_posix() in reserved:
                    repeat += 1
                    stem = f"{cast.cruise_id}_{cast.station_id}-{repeat}"
                    target = output / cast.cruise_id / f"{stem}.nc"
                reserved.add(target.relative_to(output).as_posix())
            record = records[cast.source_path]
            record.update(
                {
                    "repeat": repeat,
                    "output": str(target),
                    "output_stem": stem,
                }
            )
            try:
                _write_cast(
                    cast,
                    mapping,
                    target,
                    stem,
                    str(record["source"]),
                    output,
                    overwrite,
                    global_attributes,
                    fixed_attributes,
                    derived_attributes,
                )
            except Exception as exc:
                record.update(
                    {
                        "status": "failed",
                        "failure_reason": str(exc),
                        "validation": "failed",
                    }
                )
            else:
                record.update({"status": "converted", "validation": "passed"})
                index[source_key] = target.relative_to(output).as_posix()
                _atomic_json(index_path, index)

    ordered_records = [records[path] for path in paths]
    statuses = Counter(str(record["status"]) for record in ordered_records)
    payload: dict[str, object] = {
        "input": str(source_input),
        "output_root": str(output),
        "mapping": str(mapping_path),
        "counts": {
            "cnv_discovered": len(paths),
            "converted": statuses["converted"],
            "failed": statuses["failed"],
        },
        "records": ordered_records,
    }
    _atomic_json(
        Path(report_path)
        if report_path is not None
        else output / "conversion_report.json",
        payload,
    )
    return payload
