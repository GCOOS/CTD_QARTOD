"""Named station classes for climatology limits: explicit station lists first, then ordered rules."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Mapping

from station_identity import StationNormalization

_RULE_KEYS = {"class", "min_cast_max_depth", "max_cast_max_depth", "bbox"}


@dataclass(frozen=True)
class ClassRule:
    """Cast-level fallback; every configured condition must hold."""

    class_name: str
    min_cast_max_depth: float | None = None
    max_cast_max_depth: float | None = None
    bbox: tuple[float, float, float, float] | None = None

    def matches(self, max_depth: float | None, latitude: float | None, longitude: float | None) -> bool:
        if self.min_cast_max_depth is not None and (max_depth is None or max_depth < self.min_cast_max_depth):
            return False
        if self.max_cast_max_depth is not None and (max_depth is None or max_depth >= self.max_cast_max_depth):
            return False
        if self.bbox is not None:
            if latitude is None or longitude is None:
                return False
            lon_min, lat_min, lon_max, lat_max = self.bbox
            if not (lon_min <= longitude <= lon_max and lat_min <= latitude <= lat_max):
                return False
        return True


@dataclass(frozen=True)
class StationClassification:
    class_names: tuple[str, ...]
    stations: Mapping[str, str]
    rules: tuple[ClassRule, ...]
    normalization: StationNormalization

    def classify(
        self,
        station_id: str | None,
        *,
        max_depth: float | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> str | None:
        if station_id:
            explicit = self.stations.get(self.normalization.key(station_id))
            if explicit is not None:
                return explicit
        return next((rule.class_name for rule in self.rules if rule.matches(max_depth, latitude, longitude)), None)


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"station_classification {label} must be a finite number")
    return float(value)


def _rule(index: int, item: object, class_names: set[str]) -> ClassRule:
    label = f"rules[{index}]"
    if not isinstance(item, Mapping):
        raise ValueError(f"station_classification {label} must be an object")
    unknown = sorted(set(item) - _RULE_KEYS)
    if unknown:
        raise ValueError(f"station_classification {label} has unknown keys: {', '.join(unknown)}")
    name = item.get("class")
    if name not in class_names:
        raise ValueError(f"station_classification {label} references unknown class {name!r}")
    minimum = None if item.get("min_cast_max_depth") is None else _number(item["min_cast_max_depth"], f"{label}.min_cast_max_depth")
    maximum = None if item.get("max_cast_max_depth") is None else _number(item["max_cast_max_depth"], f"{label}.max_cast_max_depth")
    if minimum is not None and maximum is not None and minimum >= maximum:
        raise ValueError(f"station_classification {label} depth range is empty")
    bbox = item.get("bbox")
    if bbox is not None:
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"station_classification {label}.bbox must be [lon_min, lat_min, lon_max, lat_max]")
        lon_min, lat_min, lon_max, lat_max = (_number(value, f"{label}.bbox") for value in bbox)
        if lon_min > lon_max or lat_min > lat_max or not (-180 <= lon_min and lon_max <= 180 and -90 <= lat_min and lat_max <= 90):
            raise ValueError(f"station_classification {label}.bbox is invalid")
        bbox = (lon_min, lat_min, lon_max, lat_max)
    if minimum is None and maximum is None and bbox is None:
        raise ValueError(f"station_classification {label} needs a depth or bbox condition")
    return ClassRule(name, minimum, maximum, bbox)


def parse_station_classification(
    root: object, normalization: StationNormalization = StationNormalization()
) -> StationClassification:
    if not isinstance(root, Mapping) or set(root) != {"classes", "rules"}:
        raise ValueError("station_classification must contain exactly 'classes' and 'rules'")
    classes, rules = root["classes"], root["rules"]
    if not isinstance(classes, Mapping) or not isinstance(rules, list):
        raise ValueError("station_classification classes must be an object and rules a list")
    stations: dict[str, str] = {}
    for name, entry in classes.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("station_classification class names must be non-empty strings")
        members = entry.get("stations") if isinstance(entry, Mapping) and set(entry) == {"stations"} else None
        if not isinstance(members, list) or not all(isinstance(value, str) and value.strip() for value in members):
            raise ValueError(f"station_classification.classes.{name} must be {{\"stations\": [...]}}")
        for station in members:
            key = normalization.key(station)
            if key in stations:
                owner = stations[key]
                detail = "duplicate station" if owner == name else f"station in both {owner} and {name}"
                raise ValueError(f"station_classification {detail}: {station!r}")
            stations[key] = name
    names = set(classes)
    return StationClassification(
        class_names=tuple(classes),
        stations=stations,
        rules=tuple(_rule(index, item, names) for index, item in enumerate(rules)),
        normalization=normalization,
    )


def load_station_classification(
    path: Path | str, normalization: StationNormalization = StationNormalization()
) -> StationClassification:
    with Path(path).open("r", encoding="utf-8") as file:
        return parse_station_classification(json.load(file), normalization)
