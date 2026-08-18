"""Dataset-level profile loading and layout helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent
WALTON_SMITH_CONFIG_DIR = REPO_ROOT / "config" / "walton_smith"
DEFAULT_PROFILE_PATH = WALTON_SMITH_CONFIG_DIR / "dataset_profile.json"
DEFAULT_CNV_PROFILE_PATH = (
    REPO_ROOT / "config" / "hogarth_cnv" / "dataset_profile.json"
)


def _as_name_list(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        names = tuple(str(item) for item in value if str(item))
        return names or default
    return default


def _resolve_path(value: object, base_dir: Path = REPO_ROOT) -> Path | None:
    if value is None:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


@dataclass(frozen=True)
class MetadataConfig:
    """NetCDF variable names and profile axis for a dataset family."""

    station: str = "station"
    cruise_id: str = "cruiseID"
    longitude: tuple[str, ...] = ("longitude", "lon", "LONGITUDE", "LON")
    latitude: tuple[str, ...] = ("latitude", "lat", "LATITUDE", "LAT")
    depth: tuple[str, ...] = ("depth",)
    time: tuple[str, ...] = ("time",)
    sample_dimension: str = "z"

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None) -> "MetadataConfig":
        if not data:
            return cls()
        defaults = cls()
        return cls(
            station=str(data.get("station") or defaults.station),
            cruise_id=str(data.get("cruise_id") or defaults.cruise_id),
            longitude=_as_name_list(data.get("longitude"), defaults.longitude),
            latitude=_as_name_list(data.get("latitude"), defaults.latitude),
            depth=_as_name_list(data.get("depth"), defaults.depth),
            time=_as_name_list(data.get("time"), defaults.time),
            sample_dimension=str(data.get("sample_dimension") or defaults.sample_dimension),
        )


@dataclass(frozen=True)
class OutputConfig:
    """How QC output NetCDF files are written."""

    mode: str = "in_place"
    directory: Path = REPO_ROOT / "output"

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None, base_dir: Path = REPO_ROOT) -> "OutputConfig":
        if not data:
            return cls()
        defaults = cls()
        mode = str(data.get("mode") or defaults.mode)
        if mode not in {"in_place", "duplicate"}:
            raise ValueError("output.mode must be 'in_place' or 'duplicate'")
        directory = _resolve_path(data.get("directory", "output"), base_dir) or (base_dir / "output")
        return cls(mode=mode, directory=directory)


@dataclass(frozen=True)
class PathsConfig:
    """Optional dataset-specific conversion and QC configuration paths."""

    cnv_mapping: Path | None = None
    variable_mapping: Path | None = None
    station_coords: Path | None = None
    location_config: Path | None = None
    station_climatology: Path | None = None
    station_depth_classification: Path | None = None
    sensor_specs: Path | None = None
    variable_sensor_map: Path | None = None
    spike_thresholds: Path | None = None
    rate_of_change_thresholds: Path | None = None
    flat_line_config: Path | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None, base_dir: Path = REPO_ROOT) -> "PathsConfig":
        if not data:
            return cls()
        return cls(
            cnv_mapping=_resolve_path(data.get("cnv_mapping"), base_dir),
            variable_mapping=_resolve_path(data.get("variable_mapping"), base_dir),
            station_coords=_resolve_path(data.get("station_coords"), base_dir),
            location_config=_resolve_path(data.get("location_config"), base_dir),
            station_climatology=_resolve_path(data.get("station_climatology"), base_dir),
            station_depth_classification=_resolve_path(data.get("station_depth_classification"), base_dir),
            sensor_specs=_resolve_path(data.get("sensor_specs"), base_dir),
            variable_sensor_map=_resolve_path(data.get("variable_sensor_map"), base_dir),
            spike_thresholds=_resolve_path(data.get("spike_thresholds"), base_dir),
            rate_of_change_thresholds=_resolve_path(data.get("rate_of_change_thresholds"), base_dir),
            flat_line_config=_resolve_path(data.get("flat_line_config"), base_dir),
        )

    def get(self, key: str) -> Path | None:
        if not hasattr(self, key):
            raise KeyError(f"Unknown dataset profile config path key: {key}")
        return getattr(self, key)


@dataclass(frozen=True)
class DatasetProfile:
    """Dataset-level configuration for file layout, metadata names, and output."""

    data_root: Path = REPO_ROOT / "datasets" / "SFER_CTD_SOAK_REMOVED"
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    profile_path: Path | None = None

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, object],
        base_dir: Path = REPO_ROOT,
        profile_path: Path | None = None,
    ) -> "DatasetProfile":
        defaults = cls()
        data_root = _resolve_path(data.get("data_root"), base_dir) or defaults.data_root
        return cls(
            data_root=data_root,
            metadata=MetadataConfig.from_mapping(data.get("metadata") if isinstance(data.get("metadata"), Mapping) else None),
            output=OutputConfig.from_mapping(data.get("output") if isinstance(data.get("output"), Mapping) else None, base_dir),
            paths=PathsConfig.from_mapping(data.get("paths") if isinstance(data.get("paths"), Mapping) else None, base_dir),
            profile_path=profile_path,
        )

    def with_data_root(self, data_root: Path | str) -> "DatasetProfile":
        return DatasetProfile(
            data_root=_resolve_path(str(data_root)) or Path(data_root),
            metadata=self.metadata,
            output=self.output,
            paths=self.paths,
            profile_path=self.profile_path,
        )


def load_dataset_profile(path: Path | str = DEFAULT_PROFILE_PATH) -> DatasetProfile:
    profile_path = Path(path).expanduser()
    if not profile_path.is_absolute():
        profile_path = REPO_ROOT / profile_path
    with profile_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, Mapping):
        raise ValueError(f"Dataset profile must be a JSON object: {profile_path}")
    return DatasetProfile.from_mapping(data, base_dir=REPO_ROOT, profile_path=profile_path)


@lru_cache(maxsize=1)
def default_profile() -> DatasetProfile:
    return load_dataset_profile(DEFAULT_PROFILE_PATH)


def resolve_config_path(
    key: str,
    profile: DatasetProfile | None = None,
    override: Path | str | None = None,
) -> Path:
    """Resolve config path precedence: explicit override, profile paths, QC default."""
    if override is not None:
        return Path(override)
    prof = profile or default_profile()
    profile_path = prof.paths.get(key)
    if profile_path is not None:
        return profile_path

    # CNV source interpretation must always be explicitly owned by the profile.
    if key == "cnv_mapping":
        raise ValueError("Dataset profile does not define paths.cnv_mapping")

    # Local import avoids making qc_config depend on this module during import.
    import qc_config

    fallback_by_key = {
        "variable_mapping": qc_config.VARIABLE_MAPPING_JSON,
        "station_coords": qc_config.STATION_COORDS_CSV,
        "location_config": qc_config.LOCATION_CONFIG_JSON,
        "station_climatology": qc_config.STATION_CLIMATOLOGY_JSON,
        "station_depth_classification": qc_config.STATION_DEPTH_CLASSIFICATION_JSON,
        "sensor_specs": qc_config.SENSOR_SPECS_JSON,
        "variable_sensor_map": qc_config.VARIABLE_SENSOR_MAP_JSON,
        "spike_thresholds": qc_config.SPIKE_THRESHOLDS_JSON,
        "rate_of_change_thresholds": qc_config.RATE_OF_CHANGE_THRESHOLDS_JSON,
        "flat_line_config": qc_config.FLAT_LINE_CONFIG_JSON,
    }
    try:
        return fallback_by_key[key]
    except KeyError as exc:
        raise KeyError(f"Unknown config path key: {key}") from exc


def resolve_sample_axis_index(dims: tuple[str, ...], sample_dimension: str) -> int:
    """Return the axis index for the configured sample dimension."""
    if not dims:
        return 0
    if sample_dimension == "last":
        return len(dims) - 1
    try:
        return dims.index(sample_dimension)
    except ValueError as exc:
        raise ValueError(
            f"Sample dimension {sample_dimension!r} is not present in variable dims {dims!r}"
        ) from exc


def align_for_profile_tests(
    array: Any,
    dims: tuple[str, ...],
    sample_dimension: str,
) -> tuple[np.ndarray, int | None]:
    """
    Move the configured sample dimension to the last axis.

    Returns the aligned array and the original sample axis if a move was needed.
    """
    arr = np.asarray(array)
    if arr.ndim <= 1:
        return arr, None
    axis = resolve_sample_axis_index(dims, sample_dimension)
    if axis == arr.ndim - 1:
        return arr, None
    return np.moveaxis(arr, axis, -1), axis


def restore_flags_shape(
    flags: Any,
    original_shape: tuple[int, ...],
    moved_axis: int | None,
) -> np.ndarray:
    """Restore flags produced on an aligned array back to the original data shape."""
    flag_arr = np.asarray(flags, dtype=int)
    if moved_axis is not None and flag_arr.ndim > 1:
        flag_arr = np.moveaxis(flag_arr, -1, moved_axis)
    return flag_arr.reshape(original_shape)
