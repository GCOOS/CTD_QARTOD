"""Prepare reviewable filename identities for the heterogeneous 02_CNV archive."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class StationPattern:
    name: str
    expression: re.Pattern[str]


@dataclass(frozen=True)
class IdentityConfig:
    station_patterns: tuple[StationPattern, ...]
    overrides: Mapping[str, Mapping[str, str | None]]
    exclusion_patterns: tuple[re.Pattern[str], ...]
    nonstandard_extensions: Mapping[str, str]


_NUMERIC_STATION = re.compile(r"^(?P<integer>\d+)(?:[._-](?P<fraction>\d+))?$")
_ALPHANUMERIC_STATION = re.compile(r"^(?P<integer>\d+)(?P<suffix>[a-z]+)$", re.I)
_EMBEDDED_FILENAME = re.compile(r"^\*\s*FileName\s*=\s*(?P<value>.+)$", re.MULTILINE)
_EMBEDDED_CRUISE = re.compile(r"(?i)(?:ws|sav)\d+")
_RECORD_FIELDS = {
    "source",
    "disposition",
    "cruise_id",
    "station",
    "station_token",
    "variant",
    "embedded_filename",
    "warnings",
    "reason",
}
_DISPOSITIONS = {"include", "exclude", "unresolved"}


def load_identity_config(path: Path | str) -> IdentityConfig:
    """Load ordered filename rules without coupling them to conversion code."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("filename identity schema_version must be 1")

    patterns: list[StationPattern] = []
    for item in data.get("station_patterns", []):
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise ValueError("station_patterns entries must have a name")
        try:
            expression = re.compile(str(item.get("pattern") or ""))
        except re.error as exc:
            raise ValueError(f"invalid station pattern {item['name']!r}: {exc}") from exc
        if "station" not in expression.groupindex:
            raise ValueError(f"station pattern {item['name']!r} must define a station group")
        patterns.append(StationPattern(item["name"], expression))

    raw_overrides = data.get("overrides", {})
    if not isinstance(raw_overrides, Mapping):
        raise ValueError("overrides must be an object")
    overrides: dict[str, Mapping[str, str | None]] = {}
    for source, item in raw_overrides.items():
        if not isinstance(item, Mapping) or not isinstance(item.get("station"), str):
            raise ValueError(f"override {source!r} must define station")
        overrides[str(source)] = {
            "station": item["station"],
            "variant": item.get("variant"),
        }

    exclusion_patterns = tuple(
        re.compile(str(pattern)) for pattern in data.get("exclusion_patterns", [])
    )
    extensions = data.get("nonstandard_extensions", {})
    if not isinstance(extensions, Mapping):
        raise ValueError("nonstandard_extensions must be an object")
    return IdentityConfig(
        station_patterns=tuple(patterns),
        overrides=overrides,
        exclusion_patterns=exclusion_patterns,
        nonstandard_extensions={str(key).lower(): str(value) for key, value in extensions.items()},
    )


def _cruise_id(source: Path) -> str | None:
    parent = source.parent.name
    if not parent.endswith("_cnv"):
        return None
    return parent.removesuffix("_cnv").upper()


def _embedded_filename(path: Path) -> str | None:
    match = _EMBEDDED_FILENAME.search(path.read_text(encoding="utf-8"))
    return match.group("value").strip() if match else None


def _canonical_station(value: str) -> tuple[str, str]:
    station = value.strip()
    numeric = _NUMERIC_STATION.fullmatch(station)
    if numeric:
        station = str(int(numeric.group("integer")))
        if numeric.group("fraction") is not None:
            station = f"{station}.{numeric.group('fraction')}"
    else:
        alphanumeric = _ALPHANUMERIC_STATION.fullmatch(station)
        if alphanumeric:
            station = f"{int(alphanumeric.group('integer'))}{alphanumeric.group('suffix').upper()}"
        else:
            station = station.upper()
    token = re.sub(r"[^A-Z0-9]+", "_", station).strip("_")
    if not token or not re.fullmatch(r"[A-Z0-9_]+", token):
        raise ValueError(f"station {station!r} cannot form a filename-safe token")
    return station, token


def _record(
    source: str,
    disposition: str,
    cruise_id: str | None,
    station: str | None = None,
    station_token: str | None = None,
    variant: str | None = None,
    embedded_filename: str | None = None,
    warnings: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "disposition": disposition,
        "cruise_id": cruise_id,
        "station": station,
        "station_token": station_token,
        "variant": variant,
        "embedded_filename": embedded_filename,
        "warnings": warnings or [],
        "reason": reason,
    }


def prepare_identity_manifest(source_root: Path | str, config: IdentityConfig) -> dict[str, Any]:
    """Inventory exact `.cnv` files and explicit nonstandard counterparts."""

    root = Path(source_root)
    records: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        suffix = path.suffix.lower()
        if suffix != ".cnv" and suffix not in config.nonstandard_extensions:
            continue
        source = path.relative_to(root).as_posix()
        cruise_id = _cruise_id(path.relative_to(root))
        if suffix != ".cnv":
            records.append(
                _record(
                    source,
                    "exclude",
                    cruise_id,
                    reason=config.nonstandard_extensions[suffix],
                )
            )
            continue
        if cruise_id is None:
            records.append(_record(source, "unresolved", None, reason="parent is not a *_cnv directory"))
            continue
        stem = path.stem
        if any(pattern.search(stem) for pattern in config.exclusion_patterns):
            records.append(_record(source, "exclude", cruise_id, reason="matches test-cast exclusion"))
            continue

        override = config.overrides.get(source)
        match = None
        if override is None:
            for pattern in config.station_patterns:
                match = pattern.expression.search(stem)
                if match is not None:
                    break
        if override is not None:
            station_source = str(override["station"])
            variant = override.get("variant")
        elif match is not None:
            station_source = match.group("station")
            variant = match.groupdict().get("variant")
        else:
            records.append(_record(source, "unresolved", cruise_id, reason="station is not resolved by configured rules"))
            continue

        station, station_token = _canonical_station(station_source)
        embedded_filename = _embedded_filename(path)
        warnings: list[str] = []
        if embedded_filename:
            embedded_cruise = _EMBEDDED_CRUISE.search(embedded_filename)
            if embedded_cruise and embedded_cruise.group(0).upper() != cruise_id:
                warnings.append(
                    f"embedded filename cruise {embedded_cruise.group(0).upper()} differs from path cruise {cruise_id}"
                )
        records.append(
            _record(
                source,
                "include",
                cruise_id,
                station=station,
                station_token=station_token,
                variant=variant,
                embedded_filename=embedded_filename,
                warnings=warnings,
            )
        )
    return {"schema_version": 1, "records": records}


def write_identity_manifest(path: Path | str, manifest: Mapping[str, Any]) -> None:
    """Atomically publish a reviewable manifest in the destination directory."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=destination.parent, prefix=f".{destination.stem}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, destination)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def load_identity_manifest(path: Path | str) -> dict[str, Any]:
    """Load a manifest produced by :func:`write_identity_manifest`."""

    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("manifest records must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"manifest record {index} must be an object")
        missing = _RECORD_FIELDS - set(record)
        if missing:
            raise ValueError(f"manifest record {index} is missing {sorted(missing)[0]}")
        if not isinstance(record["source"], str) or not record["source"]:
            raise ValueError(f"manifest record {index} source must be a non-empty string")
        if record["disposition"] not in _DISPOSITIONS:
            raise ValueError(f"manifest record {index} disposition is invalid")
        for field in ("cruise_id", "station", "station_token", "variant", "embedded_filename", "reason"):
            if record[field] is not None and not isinstance(record[field], str):
                raise ValueError(f"manifest record {index} {field} must be a string or null")
        if not isinstance(record["warnings"], list) or not all(
            isinstance(warning, str) for warning in record["warnings"]
        ):
            raise ValueError(f"manifest record {index} warnings must be a list of strings")
        if record["disposition"] == "include":
            for field in ("cruise_id", "station", "station_token"):
                if not isinstance(record[field], str) or not record[field]:
                    raise ValueError(f"manifest record {index} include {field} is required")
        elif not isinstance(record["reason"], str) or not record["reason"].strip():
            raise ValueError(f"manifest record {index} {record['disposition']} reason is required")
    return manifest
