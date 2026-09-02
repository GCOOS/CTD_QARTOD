"""Inspect CNV headers and load the source-keyed mapping contract."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET


_NAME_RE = re.compile(
    r"^# name (?P<index>\d+) = (?P<name>[^:]+):\s*(?P<description>.*)$"
)
_UNIT_RE = re.compile(r"\[([^][]+)\]")
_PROCESSING_SUFFIX_RE = re.compile(
    r"(?:,\s*)?WS\s*=\s*[-+]?\d+(?:\.\d+)?\s*$", re.IGNORECASE
)
_CHANNEL_SUFFIX_RE = re.compile(r",\s*\d+\s*$")
_NON_NAME_RE = re.compile(r"[^A-Za-z0-9]+")
_NETCDF_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_TARGETS = {"time", "longitude", "latitude", "depth"}
CNV_MAPPING_SCHEMA_VERSION = 4
DERIVED_SENSOR_TAG = "derived"
CF_STANDARD_NAME_TABLE_VERSION = 94
CF_STANDARD_NAME_VOCABULARY = (
    f"CF Standard Name Table v{CF_STANDARD_NAME_TABLE_VERSION}"
)
_CF_STANDARD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_REQUIRED_FIELDS = {
    "time": {"source_name": "timeS", "target_name": "time"},
    "longitude": {"source_name": "longitude", "target_name": "longitude"},
    "latitude": {"source_name": "latitude", "target_name": "latitude"},
}


@dataclass(frozen=True)
class SourceCandidate:
    """One occurrence-aware CNV source column."""

    name: str
    occurrence: int


@dataclass(frozen=True)
class CnvHeaderColumn:
    """One parsed CNV column declaration."""

    index: int
    source_name: str
    description: str
    units: str | None
    occurrence: int


@dataclass(frozen=True)
class CnvSensor:
    """One embedded Sea-Bird sensor record."""

    channel: int
    sensor_type: str
    serial_number: str | None
    calibration_date: str | None


@dataclass(frozen=True)
class ScienceVariableMapping:
    """Human-reviewed handling for every occurrence of one source name."""

    action: str
    target_name: str | None
    attributes: Mapping[str, str]
    sensor_tag: str | None


@dataclass(frozen=True)
class CnvMapping:
    """Ready-to-convert schema-4 mapping."""

    source_path: Path
    required_fields: Mapping[str, SourceCandidate]
    vertical_field: SourceCandidate
    vertical_target: str
    science_variables: Mapping[str, ScienceVariableMapping]


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _occurrence(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be an integer >= 1")
    return value


def _column_units(description: str) -> str | None:
    matches = _UNIT_RE.findall(description)
    if not matches:
        return None
    return matches[-1].rsplit(",", maxsplit=1)[-1].strip() or None


def _description_mapping(
    descriptions: Sequence[str], units: Sequence[str]
) -> tuple[str, str] | None:
    """Return one safe destination and long name derived from CNV comments."""

    if not descriptions or len(set(units)) > 1:
        return None
    labels: set[str] = set()
    targets: set[str] = set()
    for description in descriptions:
        label = _UNIT_RE.sub("", description)
        label = _PROCESSING_SUFFIX_RE.sub("", label).strip(" ,")
        label = _CHANNEL_SUFFIX_RE.sub("", label).strip(" ,")
        target = _NON_NAME_RE.sub("_", label).strip("_").lower()
        if not target:
            return None
        if target[0].isdigit():
            target = f"variable_{target}"
        labels.add(label)
        targets.add(target)
    if len(targets) != 1 or next(iter(targets)) in _RESERVED_TARGETS:
        return None
    long_name = min(labels, key=lambda value: (len(value), value.casefold()))
    return next(iter(targets)), long_name


def discover_cnv_files(input_path: Path | str) -> tuple[Path, ...]:
    """Return one CNV file or every recursively discovered CNV file."""

    source = Path(input_path)
    if source.is_file():
        if source.suffix.casefold() != ".cnv":
            raise ValueError(f"CNV input file must end with .cnv: {source}")
        return (source,)
    if not source.is_dir():
        raise FileNotFoundError(f"CNV input does not exist: {source}")
    discovered = tuple(
        sorted(
            (
                path
                for path in source.rglob("*")
                if path.is_file() and path.suffix.casefold() == ".cnv"
            ),
            key=lambda path: path.as_posix().casefold(),
        )
    )
    if not discovered:
        raise ValueError(f"no .cnv files found under {source}")
    return discovered


def read_cnv_header(path: Path | str) -> tuple[CnvHeaderColumn, ...]:
    """Read only the column declarations before the CNV data delimiter."""

    source = Path(path)
    columns: list[CnvHeaderColumn] = []
    counts: Counter[str] = Counter()
    header_counts: dict[str, int] = {}
    found_delimiter = False
    with source.open("r", encoding="utf-8", errors="strict") as stream:
        for raw_line in stream:
            line = raw_line.rstrip("\r\n")
            if line == "*END*":
                found_delimiter = True
                break
            for key in ("nquan", "nvalues"):
                prefix = f"# {key} = "
                if line.startswith(prefix):
                    try:
                        header_counts[key] = int(line[len(prefix) :])
                    except ValueError as exc:
                        raise ValueError(
                            f"{source.name}: invalid {key} header value"
                        ) from exc
            match = _NAME_RE.fullmatch(line)
            if match is None:
                continue
            source_name = match.group("name").strip()
            description = match.group("description").strip()
            counts[source_name] += 1
            columns.append(
                CnvHeaderColumn(
                    index=int(match.group("index")),
                    source_name=source_name,
                    description=description,
                    units=_column_units(description),
                    occurrence=counts[source_name],
                )
            )
    if not found_delimiter:
        raise ValueError(f"{source.name}: expected one standalone *END* delimiter")
    ordered = tuple(sorted(columns, key=lambda column: column.index))
    if not ordered or [column.index for column in ordered] != list(range(len(ordered))):
        raise ValueError(f"{source.name}: invalid CNV column declarations")
    if header_counts.get("nquan") != len(ordered):
        raise ValueError(f"{source.name}: nquan does not match column declarations")
    if header_counts.get("nvalues", 0) < 1:
        raise ValueError(f"{source.name}: CNV file has no declared data rows")
    for required in ("timeS", "longitude", "latitude"):
        if counts[required] != 1:
            raise ValueError(
                f"{source.name}: required source {required!r} must occur exactly once"
            )
    return ordered


def parse_cnv_sensors(
    header_lines: Sequence[str],
) -> tuple[tuple[CnvSensor, ...], str, tuple[str, ...]]:
    """Parse the embedded Sea-Bird Sensors XML from header lines."""

    xml_lines: list[str] = []
    in_sensors = False
    for line in header_lines:
        if line.startswith("# <Sensors"):
            in_sensors = True
        if in_sensors:
            xml_lines.append(line[2:] if line.startswith("# ") else line[1:])
        if line.startswith("# </Sensors>"):
            break
    if not xml_lines:
        return (), "absent", ()
    if not xml_lines[-1].strip().endswith("</Sensors>"):
        return (), "malformed", ("embedded Sensors XML is malformed",)
    try:
        root = ET.fromstring("\n".join(xml_lines))
    except ET.ParseError:
        return (), "malformed", ("embedded Sensors XML is malformed",)

    sensors: list[CnvSensor] = []
    warnings: list[str] = []
    for fallback_channel, wrapper in enumerate(root.findall("sensor"), start=1):
        element = next(iter(wrapper), None)
        if element is None or element.tag == "NotInUse":
            continue
        try:
            channel = int(wrapper.attrib.get("Channel", fallback_channel))
        except ValueError:
            channel = fallback_channel
            warnings.append(f"invalid sensor channel for {element.tag}")
        sensors.append(
            CnvSensor(
                channel=channel,
                sensor_type=element.tag,
                serial_number=(element.findtext("SerialNumber") or "").strip()
                or None,
                calibration_date=(
                    element.findtext("CalibrationDate") or ""
                ).strip()
                or None,
            )
        )
    return tuple(sensors), "parsed", tuple(warnings)


def read_cnv_sensors(
    path: Path | str,
) -> tuple[tuple[CnvSensor, ...], str, tuple[str, ...]]:
    """Read and parse only the sensor section before the data delimiter."""

    header_lines: list[str] = []
    with Path(path).open("r", encoding="utf-8", errors="strict") as stream:
        for raw_line in stream:
            line = raw_line.rstrip("\r\n")
            if line == "*END*":
                break
            header_lines.append(line)
    return parse_cnv_sensors(header_lines)


def cf_standard_name_url(standard_name: str) -> str:
    """Return the pinned CF table URL for one human-reviewed standard name."""

    if not _CF_STANDARD_NAME_RE.fullmatch(standard_name):
        raise ValueError(f"invalid CF standard_name syntax: {standard_name!r}")
    return (
        "https://cfconventions.org/Data/cf-standard-names/"
        f"{CF_STANDARD_NAME_TABLE_VERSION}/build/"
        f"cf-standard-name-table.html#{standard_name}"
    )


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing {path}")
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


def inspect_cnv(input_path: Path | str, output_path: Path | str) -> dict[str, object]:
    """Inventory all source columns and write one human-review mapping file."""

    paths = discover_cnv_files(input_path)
    observed: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "files": set(),
            "max_occurrences_per_file": 0,
            "units": set(),
            "descriptions": set(),
        }
    )
    failures: list[dict[str, str]] = []
    sensor_parse_status: Counter[str] = Counter()
    sensor_files: dict[str, set[Path]] = defaultdict(set)
    sensor_max_occurrences: Counter[str] = Counter()
    vertical_files = 0
    inspected_files = 0
    fixed_sources = {"timeS", "longitude", "latitude"}
    for path in paths:
        try:
            columns = read_cnv_header(path)
        except Exception as exc:
            failures.append({"source": str(path), "reason": str(exc)})
            continue
        inspected_files += 1
        sensors, status, _ = read_cnv_sensors(path)
        sensor_parse_status[status] += 1
        per_file_sensors = Counter(sensor.sensor_type for sensor in sensors)
        for sensor_type, count in per_file_sensors.items():
            sensor_files[sensor_type].add(path)
            sensor_max_occurrences[sensor_type] = max(
                sensor_max_occurrences[sensor_type], count
            )
        per_file = Counter(column.source_name for column in columns)
        if per_file["depSM"] == 1:
            vertical_files += 1
        for column in columns:
            if column.source_name in fixed_sources:
                continue
            item = observed[column.source_name]
            item["files"].add(path)
            item["max_occurrences_per_file"] = max(
                int(item["max_occurrences_per_file"]),
                per_file[column.source_name],
            )
            if column.units is not None:
                item["units"].add(column.units)
            if column.description:
                item["descriptions"].add(column.description)
    if not inspected_files:
        raise ValueError("no valid CNV headers were found")

    vertical_source = "depSM" if vertical_files == inspected_files else None
    science_variables: dict[str, object] = {}
    for source_name in sorted(observed, key=str.casefold):
        if source_name == vertical_source:
            continue
        item = observed[source_name]
        descriptions = sorted(item["descriptions"])
        units = sorted(item["units"])
        resolved = _description_mapping(descriptions, units)
        attributes = {
            "long_name": resolved[1] if resolved else None,
            "units": units[0] if len(units) == 1 else None,
            "standard_name": None,
            "ioos_category": None,
            "ncei_name": None,
        }
        science_variables[source_name] = {
            "observed": {
                "file_count": len(item["files"]),
                "max_occurrences_per_file": item["max_occurrences_per_file"],
                "units": units,
                "descriptions": descriptions,
            },
            "action": "map" if resolved else "review",
            "target_name": resolved[0] if resolved else None,
            "attributes": attributes,
            "sensor_tag": None,
        }

    payload: dict[str, object] = {
        "schema_version": CNV_MAPPING_SCHEMA_VERSION,
        "inspection": {
            "cnv_file_count": len(paths),
            "inspected_file_count": inspected_files,
            "failed_files": failures,
            "sensor_parse_status": dict(sorted(sensor_parse_status.items())),
            "sensor_tags": {
                sensor_type: {
                    "file_count": len(sensor_files[sensor_type]),
                    "max_occurrences_per_file": sensor_max_occurrences[sensor_type],
                }
                for sensor_type in sorted(sensor_files, key=str.casefold)
            },
        },
        "required_fields": _REQUIRED_FIELDS,
        "vertical_field": {
            "source_name": vertical_source,
            "source_occurrence": 1,
            "target_name": "depth",
        },
        "science_variables": science_variables,
    }
    _atomic_json(Path(output_path), payload)
    return payload


def load_cnv_mapping(path: Path | str) -> CnvMapping:
    """Load a complete, human-reviewed schema-4 mapping."""

    source_path = Path(path)
    data = _object(
        json.loads(source_path.read_text(encoding="utf-8"), object_pairs_hook=_pairs),
        "CNV mapping",
    )
    if data.get("schema_version") != CNV_MAPPING_SCHEMA_VERSION:
        raise ValueError(
            f"CNV mapping schema_version must be {CNV_MAPPING_SCHEMA_VERSION}"
        )

    inspection = _object(data.get("inspection"), "inspection")
    sensor_inventory = _object(
        inspection.get("sensor_tags"), "inspection.sensor_tags"
    )
    available_sensor_tags = set(sensor_inventory)

    required = _object(data.get("required_fields"), "required_fields")
    if required != _REQUIRED_FIELDS:
        raise ValueError(
            "required_fields must map timeS, longitude, and latitude exactly"
        )
    required_fields = {
        role: SourceCandidate(str(item["source_name"]), 1)
        for role, item in _REQUIRED_FIELDS.items()
    }

    vertical = _object(data.get("vertical_field"), "vertical_field")
    vertical_source = _nonempty_string(
        vertical.get("source_name"), "vertical_field.source_name"
    )
    vertical_occurrence = _occurrence(
        vertical.get("source_occurrence"), "vertical_field.source_occurrence"
    )
    vertical_target = _nonempty_string(
        vertical.get("target_name"), "vertical_field.target_name"
    )
    if vertical_target != "depth":
        raise ValueError("vertical_field.target_name must be 'depth'")

    science_data = _object(data.get("science_variables"), "science_variables")
    structural_sources = {
        candidate.name for candidate in required_fields.values()
    } | {vertical_source}
    science_variables: dict[str, ScienceVariableMapping] = {}
    for raw_source_name, raw_value in science_data.items():
        source_name = _nonempty_string(raw_source_name, "science_variables source")
        if source_name in structural_sources:
            raise ValueError(
                f"science_variables.{source_name} reuses a structural source"
            )
        item = _object(raw_value, f"science_variables.{source_name}")
        action = _nonempty_string(
            item.get("action"), f"science_variables.{source_name}.action"
        )
        if action == "review":
            raise ValueError(
                f"science_variables.{source_name} still requires review"
            )
        if action not in {"map", "ignore"}:
            raise ValueError(
                f"science_variables.{source_name}.action must be map, ignore, or review"
            )
        target_value = item.get("target_name")
        target_name: str | None
        if action == "map":
            target_name = _nonempty_string(
                target_value, f"science_variables.{source_name}.target_name"
            )
            if not _NETCDF_NAME_RE.fullmatch(target_name):
                raise ValueError(
                    f"science_variables.{source_name}.target_name is not a valid NetCDF name"
                )
            if target_name in {"time", "longitude", "latitude", "depth"}:
                raise ValueError(
                    f"science_variables.{source_name}.target_name conflicts with a structural target"
                )
        else:
            if target_value is not None:
                raise ValueError(
                    f"science_variables.{source_name}.target_name must be null when ignored"
                )
            target_name = None
        raw_attributes = _object(
            item.get("attributes", {}),
            f"science_variables.{source_name}.attributes",
        )
        if raw_attributes.get("standard_name_url") is not None:
            raise ValueError(
                f"science_variables.{source_name}.attributes.standard_name_url "
                "is generated from standard_name and must be omitted"
            )
        attributes = {
            _nonempty_string(key, f"science_variables.{source_name}.attribute key"):
            _nonempty_string(
                value, f"science_variables.{source_name}.attributes.{key}"
            )
            for key, value in raw_attributes.items()
            if value is not None
        }
        standard_name = attributes.get("standard_name")
        if standard_name is not None:
            cf_standard_name_url(standard_name)
        sensor_value = item.get("sensor_tag")
        sensor_tag = (
            _nonempty_string(
                sensor_value, f"science_variables.{source_name}.sensor_tag"
            )
            if sensor_value is not None
            else None
        )
        if (
            sensor_tag is not None
            and sensor_tag != DERIVED_SENSOR_TAG
            and sensor_tag not in available_sensor_tags
        ):
            raise ValueError(
                f"science_variables.{source_name}.sensor_tag {sensor_tag!r} "
                "was not found during inspection"
            )
        science_variables[source_name] = ScienceVariableMapping(
            action=action,
            target_name=target_name,
            attributes=attributes,
            sensor_tag=sensor_tag,
        )

    return CnvMapping(
        source_path=source_path,
        required_fields=required_fields,
        vertical_field=SourceCandidate(vertical_source, vertical_occurrence),
        vertical_target=vertical_target,
        science_variables=science_variables,
    )
