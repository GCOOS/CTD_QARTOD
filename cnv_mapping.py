"""Load the dataset-owned contract for interpreting Sea-Bird CNV fields."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Pattern


STRUCTURAL_ROLES = {"depth", "time", "latitude", "longitude"}
SUPPORTED_TRANSFORMS = {"identity", "epoch_offset"}
SUPPORTED_REDUCERS = {"median"}


@dataclass(frozen=True)
class IdentityMapping:
    filename_pattern: Pattern[str]
    cruise_case: str
    station_case: str
    normalize_numeric_station: bool


@dataclass(frozen=True)
class TransformMapping:
    kind: str
    offset_seconds: float = 0.0


@dataclass(frozen=True)
class StructuralFieldMapping:
    source_candidates: tuple[str, ...]
    required: bool
    transform: TransformMapping
    reducer: str | None = None
    units: str | None = None
    calendar: str | None = None
    retain_samples_as: str | None = None


@dataclass(frozen=True)
class ScienceVariableMapping:
    target: str
    units: str


@dataclass(frozen=True)
class CnvMapping:
    source_path: Path
    identity: IdentityMapping
    structural_fields: Mapping[str, StructuralFieldMapping]
    science_variables: Mapping[str, ScienceVariableMapping]


def _require_object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_identity(data: Mapping[str, object]) -> IdentityMapping:
    pattern_text = str(data.get("filename_pattern") or "")
    try:
        pattern = re.compile(pattern_text)
    except re.error as exc:
        raise ValueError(f"identity.filename_pattern is invalid: {exc}") from exc

    missing_groups = {"cruise", "station", "sequence"} - set(pattern.groupindex)
    if missing_groups:
        names = ", ".join(sorted(missing_groups))
        raise ValueError(f"identity.filename_pattern is missing named groups: {names}")

    cruise_case = str(data.get("cruise_case") or "upper")
    station_case = str(data.get("station_case") or "upper")
    for label, case in (("cruise_case", cruise_case), ("station_case", station_case)):
        if case not in {"upper", "lower", "preserve"}:
            raise ValueError(f"identity.{label} must be upper, lower, or preserve")

    return IdentityMapping(
        filename_pattern=pattern,
        cruise_case=cruise_case,
        station_case=station_case,
        normalize_numeric_station=bool(data.get("normalize_numeric_station", False)),
    )


def _load_transform(value: object, role: str) -> TransformMapping:
    data = _require_object(value, f"structural_fields.{role}.transform")
    kind = str(data.get("type") or "")
    if kind not in SUPPORTED_TRANSFORMS:
        raise ValueError(
            f"structural_fields.{role}.transform type {kind!r} is not supported"
        )
    return TransformMapping(
        kind=kind,
        offset_seconds=float(data.get("offset_seconds", 0.0)),
    )


def _load_structural_fields(
    data: Mapping[str, object],
) -> dict[str, StructuralFieldMapping]:
    roles = set(data)
    if roles != STRUCTURAL_ROLES:
        missing = sorted(STRUCTURAL_ROLES - roles)
        extra = sorted(roles - STRUCTURAL_ROLES)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise ValueError("structural_fields must define exactly four roles: " + "; ".join(details))

    fields: dict[str, StructuralFieldMapping] = {}
    for role in sorted(STRUCTURAL_ROLES):
        field_data = _require_object(data[role], f"structural_fields.{role}")
        candidates_value = field_data.get("source_candidates")
        if not isinstance(candidates_value, list) or not candidates_value:
            raise ValueError(
                f"structural_fields.{role}.source_candidates must be a non-empty list"
            )
        candidates = tuple(str(item) for item in candidates_value if str(item))
        if len(candidates) != len(candidates_value):
            raise ValueError(
                f"structural_fields.{role}.source_candidates cannot contain empty names"
            )

        reducer_value = field_data.get("reducer")
        reducer = str(reducer_value) if reducer_value is not None else None
        if reducer is not None and reducer not in SUPPORTED_REDUCERS:
            raise ValueError(
                f"structural_fields.{role}.reducer {reducer!r} is not supported"
            )

        fields[role] = StructuralFieldMapping(
            source_candidates=candidates,
            required=bool(field_data.get("required", False)),
            transform=_load_transform(field_data.get("transform"), role),
            reducer=reducer,
            units=(str(field_data["units"]) if field_data.get("units") is not None else None),
            calendar=(str(field_data["calendar"]) if field_data.get("calendar") is not None else None),
            retain_samples_as=(
                str(field_data["retain_samples_as"])
                if field_data.get("retain_samples_as") is not None
                else None
            ),
        )
    return fields


def _load_science_variables(
    data: Mapping[str, object],
) -> dict[str, ScienceVariableMapping]:
    variables: dict[str, ScienceVariableMapping] = {}
    targets: set[str] = set()
    for source_name, value in data.items():
        variable_data = _require_object(value, f"science_variables.{source_name}")
        target = str(variable_data.get("target") or "")
        units = str(variable_data.get("units") or "")
        if not target:
            raise ValueError(f"science_variables.{source_name}.target is required")
        if target in targets:
            raise ValueError(f"duplicate science target {target!r}")
        targets.add(target)
        variables[source_name] = ScienceVariableMapping(target=target, units=units)
    return variables


def load_cnv_mapping(path: Path | str) -> CnvMapping:
    """Read and validate a complete CNV source mapping."""
    source_path = Path(path).expanduser()
    with source_path.open("r", encoding="utf-8") as stream:
        root = json.load(stream)
    data = _require_object(root, "CNV mapping")

    if data.get("schema_version") != 1:
        raise ValueError("CNV mapping schema_version must be 1")

    identity_data = _require_object(data.get("identity"), "identity")
    structural_data = _require_object(
        data.get("structural_fields"), "structural_fields"
    )
    science_data = _require_object(data.get("science_variables"), "science_variables")
    return CnvMapping(
        source_path=source_path,
        identity=_load_identity(identity_data),
        structural_fields=_load_structural_fields(structural_data),
        science_variables=_load_science_variables(science_data),
    )
