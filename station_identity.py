"""Config-driven cruise and station identity for CNV filenames and reference tables."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Mapping

STATION_IDENTITY_SCHEMA_VERSION = 1
_SAFE_STATION = re.compile(r"[A-Za-z0-9._-]+")
_NUMERIC_PREFIX = re.compile(r"(?P<number>\d+)(?P<suffix>.*)")
_CRUISE_PLACEHOLDER = "{cruise}"


class IdentityError(ValueError):
    """Raised when a filename cannot be resolved to a reviewed cruise/station identity."""


@dataclass(frozen=True)
class StationNormalization:
    """Canonical station spelling shared by converted files and QC reference tables."""

    strip_leading_zeros: bool = False
    decimal_separator: str = "."

    def canonical(self, value: str) -> str:
        station = value.strip()
        if self.strip_leading_zeros:
            match = _NUMERIC_PREFIX.fullmatch(station)
            if match is not None:
                station = f"{int(match['number'])}{match['suffix']}"
        return station.replace(".", self.decimal_separator)

    def key(self, value: str) -> str:
        """Case-insensitive lookup key; source aliases are deliberately not applied."""
        return self.canonical(value).casefold()


@dataclass(frozen=True)
class CastIdentity:
    cruise_id: str
    station_id: str
    platform: str | None
    variant: str | None


@dataclass(frozen=True)
class StationIdentityConfig:
    source_path: Path
    cruise_expression: re.Pattern[str]
    filename_patterns: tuple[tuple[str, re.Pattern[str]], ...]
    cruise_folder_pattern: re.Pattern[str] | None
    exclusion_patterns: tuple[re.Pattern[str], ...]
    station_shape: re.Pattern[str] | None
    strip_run_number: bool
    uppercase_cruise: bool
    aliases: Mapping[str, str]
    normalization: StationNormalization
    overrides: Mapping[str, Mapping[str, str | None]]


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"station_identity {label} must be a non-empty string")
    return value


def _compile(pattern: str, label: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"station_identity {label} is not a valid regex: {exc}") from exc


def _bool(data: Mapping[str, object], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"station_identity {key} must be true or false")
    return value


@lru_cache(maxsize=None)
def load_station_identity(path: Path | str) -> StationIdentityConfig:
    """Load and validate one reviewed station_identity.json."""

    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping) or data.get("schema_version") != STATION_IDENTITY_SCHEMA_VERSION:
        raise ValueError(f"station_identity schema_version must be {STATION_IDENTITY_SCHEMA_VERSION}: {source}")

    cruise_pattern = _string(data.get("cruise_pattern"), "cruise_pattern")
    cruise_expression = _compile(cruise_pattern, "cruise_pattern")
    if "cruise" not in cruise_expression.groupindex:
        raise ValueError("station_identity cruise_pattern must define a (?P<cruise>...) group")

    def expand(pattern: str, label: str) -> re.Pattern[str]:
        return _compile(pattern.replace(_CRUISE_PLACEHOLDER, f"(?:{cruise_pattern})"), label)

    folder_value = data.get("cruise_folder_pattern")
    cruise_folder = None if folder_value is None else expand(_string(folder_value, "cruise_folder_pattern"), "cruise_folder_pattern")
    if cruise_folder is not None and "cruise" not in cruise_folder.groupindex:
        raise ValueError("station_identity cruise_folder_pattern must contain {cruise}")

    raw_patterns = data.get("filename_patterns")
    if not isinstance(raw_patterns, list) or not raw_patterns:
        raise ValueError("station_identity filename_patterns must be a non-empty list")
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for item in raw_patterns:
        if not isinstance(item, Mapping):
            raise ValueError("station_identity filename_patterns entries must be objects")
        name = _string(item.get("name"), "filename_patterns name")
        expression = expand(_string(item.get("pattern"), f"filename_patterns.{name}"), f"filename_patterns.{name}")
        if "station" not in expression.groupindex:
            raise ValueError(f"station_identity filename pattern {name!r} must define a (?P<station>...) group")
        if "cruise" not in expression.groupindex and cruise_folder is None:
            raise ValueError(f"station_identity filename pattern {name!r} needs {{cruise}} or a cruise_folder_pattern")
        patterns.append((name, expression))

    exclusions = data.get("exclusion_patterns", [])
    if not isinstance(exclusions, list):
        raise ValueError("station_identity exclusion_patterns must be a list")
    shape_value = data.get("station_shape")
    normalize = data.get("normalize", {})
    if not isinstance(normalize, Mapping):
        raise ValueError("station_identity normalize must be an object")
    separator = normalize.get("decimal_separator", ".")
    if separator not in {".", "_", "-"}:
        raise ValueError("station_identity normalize.decimal_separator must be '.', '_' or '-'")
    normalization = StationNormalization(
        strip_leading_zeros=_bool(normalize, "strip_leading_zeros"),
        decimal_separator=separator,
    )

    aliases = data.get("aliases", {})
    if not isinstance(aliases, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str) and value for key, value in aliases.items()
    ):
        raise ValueError("station_identity aliases must map source tokens to station IDs")
    overrides = data.get("overrides", {})
    if not isinstance(overrides, Mapping):
        raise ValueError("station_identity overrides must be an object")
    for filename, item in overrides.items():
        if not isinstance(item, Mapping) or not isinstance(item.get("station"), str):
            raise ValueError(f"station_identity override {filename!r} must define station")

    return StationIdentityConfig(
        source_path=source,
        cruise_expression=cruise_expression,
        filename_patterns=tuple(patterns),
        cruise_folder_pattern=cruise_folder,
        exclusion_patterns=tuple(_compile(_string(value, "exclusion_patterns"), "exclusion_patterns") for value in exclusions),
        station_shape=None if shape_value is None else _compile(_string(shape_value, "station_shape"), "station_shape"),
        strip_run_number=_bool(data, "strip_run_number"),
        uppercase_cruise=_bool(data, "uppercase_cruise"),
        aliases={normalization.key(key): value for key, value in aliases.items()},
        normalization=normalization,
        overrides={str(key): dict(value) for key, value in overrides.items()},
    )


def station_normalization(path: Path | str | None) -> StationNormalization:
    """Normalization for reference lookups; datasets without identity rules match literally."""
    return StationNormalization() if path is None else load_station_identity(path).normalization


def _station(raw: str, config: StationIdentityConfig, source: Path) -> str:
    station = raw.strip()
    if config.strip_run_number:
        head, separator, tail = station.rpartition("_")
        if separator and tail.isdigit():
            station = head
    station = config.normalization.canonical(station)
    station = config.aliases.get(station.casefold(), station)
    if any(pattern.search(station) for pattern in config.exclusion_patterns):
        raise IdentityError(f"{source.name}: test/recast filename needs explicit identity review")
    if not _SAFE_STATION.fullmatch(station) or (
        config.station_shape is not None and not config.station_shape.fullmatch(station)
    ):
        raise IdentityError(f"{source.name}: ambiguous station token {station!r}; identity review required")
    return station


def resolve_identity(path: Path | str, config: StationIdentityConfig) -> CastIdentity:
    """Derive cruise, station, platform and variant from the CNV path only."""

    source = Path(path)
    stem = source.stem if source.suffix.casefold() == ".cnv" else source.name
    folder = config.cruise_folder_pattern.fullmatch(source.parent.name) if config.cruise_folder_pattern else None
    folder_cruise = folder["cruise"] if folder else None
    override = config.overrides.get(source.name)
    if override is not None:
        cruise = override.get("cruise") or folder_cruise
        station_raw = str(override["station"])
        variant = override.get("variant")
    else:
        match = next(
            (found for _, expression in config.filename_patterns if (found := expression.fullmatch(stem))),
            None,
        )
        if match is None:
            raise IdentityError(f"{source.name}: filename does not match station_identity filename_patterns; identity review required")
        groups = match.groupdict()
        cruise = groups.get("cruise") or folder_cruise
        if groups.get("cruise") and folder_cruise and groups["cruise"].casefold() != folder_cruise.casefold():
            raise IdentityError(f"{source.name}: filename cruise differs from cruise folder; identity review required")
        station_raw = groups["station"]
        variant = groups.get("variant") or None
    if not cruise:
        raise IdentityError(f"{source.name}: no cruise ID in filename or cruise folder; identity review required")
    cruise_match = config.cruise_expression.fullmatch(cruise)
    if cruise_match is None:
        raise IdentityError(f"{source.name}: cruise {cruise!r} does not match cruise_pattern; identity review required")
    platform = cruise_match.groupdict().get("platform")
    if config.uppercase_cruise:
        cruise = cruise.upper()
        platform = platform.upper() if platform else platform
    return CastIdentity(
        cruise_id=cruise,
        station_id=_station(station_raw, config, source),
        platform=platform or None,
        variant=variant,
    )
