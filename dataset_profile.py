"""Dataset-level profile loading and layout helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from xml_generator.config import ErddapConfig
from cnv_metadata import PublicationSettings, validate_ownership

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_TEMPLATE_DIR = REPO_ROOT / "config_template"
PRESETS_DIR = REPO_ROOT / "presets"
TEMPLATE_PROFILE_PATH = CONFIG_TEMPLATE_DIR / "dataset_profile.json"
DEFAULT_PROFILE_PATH = TEMPLATE_PROFILE_PATH
QC_TEST_MODE_NAMES = ("gap_test", "syntax_test")
QC_TEST_MODES = {"not_evaluated", "run"}

# Relative location of each configuration file inside config_template/, a preset,
# and every config/<dataset>/ folder.
CONFIG_LAYOUT = {
    "station_identity": Path("station_identity.json"),
    "soak_config": Path("soak_config.json"),
    "source_variable_mapping": Path("variable_mapping/source_variable_mapping.json"),
    "variable_mapping": Path("qc_variable_mapping.json"),
    "station_coords": Path("location_test/Station_Mean_Coords.csv"),
    "location_config": Path("location_test/location_config.json"),
    "station_climatology": Path("climatology_test/station_climatology_config.json"),
    "station_classification": Path("climatology_test/station_classification.json"),
    "sensor_specs": Path("gross_range_test/sensor_specs.json"),
    "variable_sensor_map": Path("gross_range_test/variable_sensor_map.json"),
    "spike_thresholds": Path("spike_test/spike_thresholds.json"),
    "rate_of_change_thresholds": Path("rate_of_change_test/rate_of_change_thresholds.json"),
    "flat_line_config": Path("flat_line_test/flat_line_config.json"),
}


def available_presets() -> list[str]:
    return sorted(path.name for path in PRESETS_DIR.iterdir() if path.is_dir()) if PRESETS_DIR.is_dir() else []


def reference_file(relative: Path | str, preset: str | None = None) -> Path:
    """Return a preset file when the preset defines it, otherwise the generic template file."""
    directories = [CONFIG_TEMPLATE_DIR]
    if preset is not None:
        preset_dir = PRESETS_DIR / preset
        if not preset_dir.is_dir():
            raise ValueError(f"unknown preset {preset!r}; available: {', '.join(available_presets()) or 'none'}")
        directories.insert(0, preset_dir)
    for directory in directories:
        candidate = directory / relative
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no reference file {relative} in {', '.join(map(str, directories))}")


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


def _load_netcdf_global_attributes(data: object) -> dict[str, object]:
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise ValueError(
            "dataset profile netcdf_global_attributes must be a JSON object"
        )
    attributes: dict[str, object] = {}
    for name, value in data.items():
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError(
                "netcdf_global_attributes keys must be non-empty trimmed strings"
            )
        if value is None:
            attributes[name] = None
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (str, int, float))
            or (isinstance(value, str) and not value.strip())
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            raise ValueError(
                f"netcdf_global_attributes.{name} must be a non-empty string, "
                "a finite number, or null"
            )
        attributes[name] = value
    return attributes


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
    station_identity: Path | None = None
    soak_config: Path | None = None
    source_variable_mapping: Path | None = None
    variable_mapping: Path | None = None
    station_coords: Path | None = None
    location_config: Path | None = None
    station_climatology: Path | None = None
    station_classification: Path | None = None
    sensor_specs: Path | None = None
    variable_sensor_map: Path | None = None
    spike_thresholds: Path | None = None
    rate_of_change_thresholds: Path | None = None
    flat_line_config: Path | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, object] | None, base_dir: Path = REPO_ROOT) -> "PathsConfig":
        if not data:
            return cls()
        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"dataset profile has unknown paths: {', '.join(unknown)}")
        return cls(**{key: _resolve_path(data.get(key), base_dir) for key in cls.__dataclass_fields__})

    def get(self, key: str) -> Path | None:
        if not hasattr(self, key):
            raise KeyError(f"Unknown dataset profile config path key: {key}")
        return getattr(self, key)


def _load_qc_test_modes(data: object) -> dict[str, str]:
    if not isinstance(data, Mapping):
        raise ValueError("dataset profile must define a qc_test_modes JSON object")
    expected = set(QC_TEST_MODE_NAMES)
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if extra:
            details.append(f"unknown: {', '.join(extra)}")
        raise ValueError(f"qc_test_modes must define exactly gap_test and syntax_test ({'; '.join(details)})")

    modes: dict[str, str] = {}
    for name in QC_TEST_MODE_NAMES:
        mode = data[name]
        if not isinstance(mode, str) or mode not in QC_TEST_MODES:
            raise ValueError(
                f"qc_test_modes.{name} must be 'not_evaluated' or 'run'"
            )
        modes[name] = mode
    return modes


@dataclass(frozen=True)
class DatasetProfile:
    """Dataset-level configuration for file layout, metadata names, and output."""

    data_root: Path = REPO_ROOT / "output"
    preset: str | None = None
    display_name: str = "CTD"
    dataset_id_prefix: str = ""
    platforms: Mapping[str, str] = field(default_factory=dict)
    netcdf_global_attributes: Mapping[str, object] = field(default_factory=dict)
    netcdf_fixed_global_attributes: Mapping[str, object] = field(default_factory=dict)
    netcdf_derived_global_attributes: Mapping[str, object] = field(default_factory=dict)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    qc_test_modes: Mapping[str, str] = field(
        default_factory=lambda: {
            "gap_test": "not_evaluated",
            "syntax_test": "not_evaluated",
        }
    )
    erddap: ErddapConfig = field(
        default_factory=lambda: ErddapConfig(
            output_xml=REPO_ROOT / "output" / "erddap" / "datasets.xml",
            filedir_prefix="/data/erddap",
        )
    )
    profile_path: Path | None = None

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, object],
        base_dir: Path = REPO_ROOT,
        profile_path: Path | None = None,
    ) -> "DatasetProfile":
        data_root = _resolve_path(data.get("data_root"), base_dir)
        if data_root is None:
            raise ValueError("dataset profile must define data_root")
        human = _load_netcdf_global_attributes(data.get("netcdf_global_attributes"))
        fixed = _load_netcdf_global_attributes(data.get("netcdf_fixed_global_attributes"))
        derived = _load_netcdf_global_attributes(data.get("netcdf_derived_global_attributes"))
        validate_ownership(human, fixed, derived)
        preset = data.get("preset")
        if preset is not None and (not isinstance(preset, str) or not preset.strip()):
            raise ValueError("dataset profile preset must be a preset name or null")
        display_name = data.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("dataset profile must define a non-empty display_name")
        prefix = data.get("dataset_id_prefix")
        if not isinstance(prefix, str):
            raise ValueError("dataset profile must define dataset_id_prefix as a string")
        platforms = data.get("platforms", {})
        if not isinstance(platforms, Mapping) or not all(
            isinstance(code, str) and isinstance(name, str) and name.strip() for code, name in platforms.items()
        ):
            raise ValueError("dataset profile platforms must map platform codes to platform names")
        return cls(
            data_root=data_root,
            preset=preset,
            display_name=display_name,
            dataset_id_prefix=prefix,
            platforms={code.upper(): name for code, name in platforms.items()},
            netcdf_global_attributes=human,
            netcdf_fixed_global_attributes=fixed,
            netcdf_derived_global_attributes=derived,
            metadata=MetadataConfig.from_mapping(data.get("metadata") if isinstance(data.get("metadata"), Mapping) else None),
            output=OutputConfig.from_mapping(data.get("output") if isinstance(data.get("output"), Mapping) else None, base_dir),
            paths=PathsConfig.from_mapping(data.get("paths") if isinstance(data.get("paths"), Mapping) else None, base_dir),
            qc_test_modes=_load_qc_test_modes(data.get("qc_test_modes")),
            erddap=ErddapConfig.from_mapping(data.get("erddap"), base_dir, dataset_id_prefix=prefix),
            profile_path=profile_path,
        )

    @property
    def publication(self) -> PublicationSettings:
        return PublicationSettings(
            global_attributes=self.netcdf_global_attributes,
            fixed_attributes=self.netcdf_fixed_global_attributes,
            derived_attributes=self.netcdf_derived_global_attributes,
            platforms=self.platforms,
            dataset_id_prefix=self.dataset_id_prefix,
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
) -> Path:
    """Return a configuration path explicitly selected by the dataset profile."""
    prof = profile or default_profile()
    profile_path = prof.paths.get(key)
    if profile_path is not None:
        return profile_path
    raise ValueError(f"Dataset profile does not define paths.{key}")


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
