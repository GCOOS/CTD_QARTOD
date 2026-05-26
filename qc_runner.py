"""
Run QC pipeline for SFER_CTD NetCDF files.

Station and cruise IDs are read from the ``station`` and ``cruiseID``
variables embedded in each file — no filename parsing is required.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

from dataset_profile import (
    DatasetProfile,
    align_for_profile_tests,
    default_profile,
    resolve_config_path,
    restore_flags_shape,
)
from qc_config import (
    LOCATION_TOLERANCE,
    QC_FLAGS,
    TEST_CATEGORIES,
    get_climatology_config_for_file,
    load_flat_line_config,
    load_rate_of_change_thresholds,
    load_spike_thresholds,
)
from instrument_resolver import resolve_gross_ranges
from qc_data_loader import (
    get_coord_for_var,
    get_lon_lat,
    get_qc_variables,
    get_station_id,
    get_cruise_id,
    get_variable_category,
    get_scalar_var,
    load_mapping,
    load_nc_file,
)
from station_resolver import resolve_coords_by_station_id
from qc_result_viz import QCTestResult, _flag_summary
from qc_tests import (
    climatology_test,
    decreasing_radiance_test,
    flat_line_test,
    gap_test,
    gross_range_test,
    location_test,
    rate_of_change_test,
    spike_test,
    syntax_test,
)
from qc_writer import (
    aggregate_qc_flags,
    aggregate_qc_variable_name,
    qc_variable_name,
    resolve_output_path,
    save_dataset,
    set_ancillary_variables,
    write_aggregate_qc_results,
    write_qc_results,
)

_AVAILABLE_TESTS = list(TEST_CATEGORIES.keys())
_IMPLEMENTED_TESTS = (
    "gap_test",
    "syntax_test",
    "location_test",
    "gross_range_test",
    "decreasing_radiance_test",
    "climatology_test",
    "flat_line_test",
    "spike_test",
    "rate_of_change_test",
)


def _date_from_iso_like(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def _date_from_time_var(ds: xr.Dataset, profile: DatasetProfile) -> str | None:
    for name in profile.metadata.time:
        if name not in ds:
            continue
        value = np.asarray(ds[name].values).flat[0]
        iso_date = _date_from_iso_like(value)
        if iso_date:
            return iso_date

        units = str(ds[name].attrs.get("units") or "").strip().lower()
        if np.issubdtype(np.asarray(value).dtype, np.number) and units.startswith("seconds since 1970-01-01"):
            date = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=float(value))
            return date.date().isoformat()
    return None


def _sfer_metadata_date(ds: xr.Dataset, profile: DatasetProfile) -> str:
    for attr_name in ("time_coverage_start", "time_coverage_end"):
        date = _date_from_iso_like(ds.attrs.get(attr_name))
        if date:
            return date
    return _date_from_time_var(ds, profile) or "unknown"


def refresh_sfer_qc_metadata(ds: xr.Dataset, profile: DatasetProfile | None = None) -> xr.Dataset:
    """Refresh SFER display metadata before writing QC output."""
    prof = profile or default_profile()
    cruise_id = get_scalar_var(ds, prof.metadata.cruise_id) or "unknown"
    station = get_scalar_var(ds, prof.metadata.station) or "unknown"
    date = _sfer_metadata_date(ds, prof)

    if "profile" in ds:
        ds["profile"].attrs["long_name"] = f"{cruise_id}_{station}"
    else:
        logger.warning("Dataset has no profile variable; cannot refresh profile long_name")

    ds.attrs["title"] = f"CTD data from SFER cruise {cruise_id}, station {station}, {date}"
    return ds


def _should_run_test(test_name: str, category: str | None) -> bool:
    """Return True if *test_name* should run for the given variable category."""
    cats = TEST_CATEGORIES.get(test_name)
    return cats is not None and category in cats


def _broadcast_like(target: xr.DataArray, source: xr.DataArray | None) -> Optional[np.ndarray]:
    """
    Broadcast source to the shape of target, if present.
    """
    if source is None:
        return None
    try:
        return np.broadcast_to(np.asarray(source), target.shape)
    except ValueError:
        return np.full(target.shape, np.asarray(source).flat[0])


def _get_depth_for_var(
    ds: xr.Dataset,
    var: xr.DataArray,
    profile: DatasetProfile | None = None,
) -> Optional[np.ndarray]:
    metadata = (profile or default_profile()).metadata
    depth = get_coord_for_var(ds, var, metadata.depth)
    if depth is not None:
        return _broadcast_like(var, depth)
    return None


def _get_time_for_var(
    ds: xr.Dataset,
    var: xr.DataArray,
    profile: DatasetProfile | None = None,
) -> Optional[np.ndarray]:
    metadata = (profile or default_profile()).metadata
    time = get_coord_for_var(ds, var, metadata.time)
    if time is not None:
        return _broadcast_like(var, time)
    return None


def _spike_params_for_var(
    spike_thresholds: Mapping[str, Mapping[str, Any]] | None,
    var_name: str,
) -> Optional[Tuple[float, float]]:
    if not spike_thresholds:
        return None
    spike = spike_thresholds.get(var_name)
    if not isinstance(spike, dict):
        return None
    if "suspect_threshold" not in spike or "fail_threshold" not in spike:
        return None
    return (float(spike["suspect_threshold"]), float(spike["fail_threshold"]))


def _roc_threshold_for_var(
    rate_of_change_thresholds: Mapping[str, Mapping[str, Any]] | None,
    var_name: str,
) -> Optional[float]:
    if not rate_of_change_thresholds:
        return None
    roc = rate_of_change_thresholds.get(var_name)
    if not isinstance(roc, dict) or "threshold" not in roc:
        return None
    v = float(roc["threshold"])
    if not np.isfinite(v) or v <= 0:
        return None
    return v


def _run_common_tests(
    ds: xr.Dataset,
    var_name: str,
    category: Optional[str],
    gross_ranges: Mapping[str, Mapping[str, tuple]] | None,
    climatology_config: Mapping[str, Iterable[Mapping]] | None,
    expected_location: Optional[tuple[float, float]],
    location_tolerance: float,
    spike_thresholds: Mapping[str, Mapping[str, Any]] | None = None,
    rate_of_change_thresholds: Mapping[str, Mapping[str, Any]] | None = None,
    flat_line_config: Mapping[str, Any] | None = None,
    profile: DatasetProfile | None = None,
) -> xr.Dataset:
    data_var = ds[var_name]
    prof = profile or default_profile()
    flag_arrays: list[np.ndarray] = []
    qc_names: list[str] = []

    for test_name in _IMPLEMENTED_TESTS:
        if not _should_run_test(test_name, category):
            continue
        flags = _run_single_test_for_var(
            test_name=test_name,
            var_name=var_name,
            data_var=data_var,
            ds=ds,
            category=category,
            gross_ranges=gross_ranges,
            climatology_config=climatology_config,
            expected_location=expected_location,
            location_tolerance=location_tolerance,
            spike_thresholds=spike_thresholds,
            rate_of_change_thresholds=rate_of_change_thresholds,
            flat_line_config=flat_line_config,
            profile=prof,
        )
        ds = write_qc_results(ds, var_name, test_name, flags)
        flag_arrays.append(np.asarray(flags, dtype=np.int8))
        qc_names.append(qc_variable_name(var_name, test_name))

    if flag_arrays:
        aggregate_flags = aggregate_qc_flags(flag_arrays, data=data_var)
        ds = write_aggregate_qc_results(ds, var_name, aggregate_flags)
        ds = set_ancillary_variables(
            ds,
            var_name,
            [aggregate_qc_variable_name(var_name), *qc_names],
        )

    return ds


def _run_single_test_for_var(
    test_name: str,
    var_name: str,
    data_var: xr.DataArray,
    ds: xr.Dataset,
    category: Optional[str],
    gross_ranges: Mapping[str, Mapping[str, tuple]] | None,
    climatology_config: Mapping[str, Iterable[Mapping]] | None,
    expected_location: Optional[tuple[float, float]],
    location_tolerance: float,
    spike_thresholds: Mapping[str, Mapping[str, Any]] | None = None,
    rate_of_change_thresholds: Mapping[str, Mapping[str, Any]] | None = None,
    flat_line_config: Mapping[str, Any] | None = None,
    profile: DatasetProfile | None = None,
) -> np.ndarray:
    """
    Execute a single QC test for the given variable and return flags.
    """
    if not _should_run_test(test_name, category):
        return np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
    prof = profile or default_profile()

    if test_name == "gap_test":
        return gap_test(data_var)

    if test_name == "syntax_test":
        return syntax_test(data_var)

    if test_name == "location_test":
        lon, lat = get_lon_lat(ds, prof.metadata)
        if lon is not None and lat is not None and expected_location is not None:
            lon_b = _broadcast_like(data_var, lon)
            lat_b = _broadcast_like(data_var, lat)
            expected_lat, expected_lon = expected_location
            return location_test(
                lon=lon_b,
                lat=lat_b,
                expected_lon=expected_lon,
                expected_lat=expected_lat,
                tolerance=location_tolerance,
            )
        return np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)

    if test_name == "gross_range_test":
        ranges = gross_ranges.get(var_name) if gross_ranges else None
        if ranges:
            fail_span = tuple(ranges.get("fail_span", ())) or None
            suspect_span = tuple(ranges.get("suspect_span", ())) or None
            if fail_span:
                return gross_range_test(data_var, fail_span=fail_span, suspect_span=suspect_span)
        return np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)

    if test_name == "decreasing_radiance_test":
        depth = _get_depth_for_var(ds, data_var, prof)
        aligned_data, moved_axis = align_for_profile_tests(
            data_var,
            data_var.dims,
            prof.metadata.sample_dimension,
        )
        aligned_depth = None
        if depth is not None:
            aligned_depth, _ = align_for_profile_tests(
                depth,
                data_var.dims,
                prof.metadata.sample_dimension,
            )
        flags = decreasing_radiance_test(aligned_data, depth=aligned_depth, non_increasing=True)
        return restore_flags_shape(flags, data_var.shape, moved_axis)

    if test_name == "climatology_test":
        depth = _get_depth_for_var(ds, data_var, prof)
        time = _get_time_for_var(ds, data_var, prof)
        config = None
        if climatology_config:
            config = climatology_config.get(var_name)
        if depth is None or time is None or config is None:
            return np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
        return climatology_test(data_var, time=time, depth=depth, config=config)

    if test_name == "flat_line_test":
        flat_cfg = dict(flat_line_config or load_flat_line_config())
        aligned_data, moved_axis = align_for_profile_tests(
            data_var,
            data_var.dims,
            prof.metadata.sample_dimension,
        )
        flags = flat_line_test(
            aligned_data,
            rep_cnt_suspect=flat_cfg["rep_cnt_suspect"],
            rep_cnt_fail=flat_cfg["rep_cnt_fail"],
            eps=flat_cfg["eps"],
        )
        return restore_flags_shape(flags, data_var.shape, moved_axis)

    if test_name == "spike_test":
        sp = _spike_params_for_var(spike_thresholds, var_name)
        if sp is None:
            return np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
        s_th, f_th = sp
        aligned_data, moved_axis = align_for_profile_tests(
            data_var,
            data_var.dims,
            prof.metadata.sample_dimension,
        )
        flags = spike_test(aligned_data, suspect_threshold=s_th, fail_threshold=f_th)
        return restore_flags_shape(flags, data_var.shape, moved_axis)

    if test_name == "rate_of_change_test":
        roc_thr = _roc_threshold_for_var(rate_of_change_thresholds, var_name)
        if roc_thr is None:
            return np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
        aligned_data, moved_axis = align_for_profile_tests(
            data_var,
            data_var.dims,
            prof.metadata.sample_dimension,
        )
        flags = rate_of_change_test(aligned_data, threshold=roc_thr)
        return restore_flags_shape(flags, data_var.shape, moved_axis)

    raise ValueError(f"Unknown test_name: {test_name}")


def run_qc_for_file(
    nc_path: Path | str,
    mapping_path: Path | str | None = None,
    location_tolerance: float = LOCATION_TOLERANCE,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    station_climatology_config_path: Path | str | None = None,
    station_depth_classification_path: Path | str | None = None,
    sensor_specs_path: Path | str | None = None,
    variable_sensor_map_path: Path | str | None = None,
    station_coords_csv: Path | str | None = None,
    spike_thresholds_path: Path | str | None = None,
    rate_of_change_thresholds_path: Path | str | None = None,
    flat_line_config_path: Path | str | None = None,
    profile: DatasetProfile | None = None,
    data_root: Path | str | None = None,
) -> None:
    """
    Run QC for a single NetCDF file and write results back to the same file.

    Station and cruise IDs are obtained from the ``station`` and ``cruiseID``
    variables inside the file.
    """
    nc_path = Path(nc_path)
    prof = profile or default_profile()
    root_for_output = Path(data_root) if data_root is not None else prof.data_root
    mapping_file = resolve_config_path("variable_mapping", prof, mapping_path)
    station_coords_file = resolve_config_path("station_coords", prof, station_coords_csv)
    station_climatology_file = resolve_config_path("station_climatology", prof, station_climatology_config_path)
    station_depth_file = resolve_config_path("station_depth_classification", prof, station_depth_classification_path)
    sensor_specs_file = resolve_config_path("sensor_specs", prof, sensor_specs_path)
    variable_sensor_file = resolve_config_path("variable_sensor_map", prof, variable_sensor_map_path)
    spike_file = resolve_config_path("spike_thresholds", prof, spike_thresholds_path)
    rate_file = resolve_config_path("rate_of_change_thresholds", prof, rate_of_change_thresholds_path)
    flat_line_file = resolve_config_path("flat_line_config", prof, flat_line_config_path)
    logger.info("Processing file: %s", nc_path.name)
    
    mapping = load_mapping(mapping_file)
    ds = load_nc_file(nc_path)

    station_id = get_station_id(ds, prof.metadata)
    cruise_id = get_cruise_id(ds, prof.metadata)
    logger.debug("  Station ID: %s, Cruise ID: %s", station_id, cruise_id)
    
    station_coords = resolve_coords_by_station_id(station_id, station_csv=station_coords_file)
    if station_coords:
        logger.debug("  Station coords: lat=%.4f, lon=%.4f", station_coords[0], station_coords[1])
    else:
        logger.debug("  Station coords: not found")

    dynamic_ranges = resolve_gross_ranges(
        ds, specs_path=sensor_specs_file, variable_map_path=variable_sensor_file,
    )
    gross_ranges = {**dynamic_ranges, **(gross_range_overrides or {})}
    clim_config = get_climatology_config_for_file(
        ds,
        limits_json_path=station_climatology_file,
        classification_json_path=station_depth_file,
        metadata=prof.metadata,
    )
    if clim_config and climatology_overrides:
        clim_config = {**clim_config, **climatology_overrides}

    spike_thresholds = load_spike_thresholds(spike_file)
    rate_of_change_thresholds = load_rate_of_change_thresholds(rate_file)
    flat_line_cfg = load_flat_line_config(flat_line_file)

    qc_vars = get_qc_variables(ds, mapping)
    logger.debug("  QC variables (%d): %s", len(qc_vars), ", ".join(qc_vars))
    
    for var_name in qc_vars:
        category = get_variable_category(var_name, mapping)
        logger.debug("  Running tests on %s (category: %s)", var_name, category)
        ds = _run_common_tests(
            ds,
            var_name,
            category,
            gross_ranges,
            clim_config,
            expected_location=station_coords,
            location_tolerance=location_tolerance,
            spike_thresholds=spike_thresholds,
            rate_of_change_thresholds=rate_of_change_thresholds,
            flat_line_config=flat_line_cfg,
            profile=prof,
        )

    output_path = resolve_output_path(nc_path, prof, root_for_output)
    ds = refresh_sfer_qc_metadata(ds, prof)
    save_dataset(ds, output_path)
    logger.info("  Saved QC results to %s", output_path)


def run_qc_for_directory(
    dir_path: Path | str,
    mapping_path: Path | str | None = None,
    location_tolerance: float = LOCATION_TOLERANCE,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    station_climatology_config_path: Path | str | None = None,
    station_depth_classification_path: Path | str | None = None,
    sensor_specs_path: Path | str | None = None,
    variable_sensor_map_path: Path | str | None = None,
    station_coords_csv: Path | str | None = None,
    spike_thresholds_path: Path | str | None = None,
    rate_of_change_thresholds_path: Path | str | None = None,
    flat_line_config_path: Path | str | None = None,
    profile: DatasetProfile | None = None,
    data_root: Path | str | None = None,
) -> None:
    """
    Run QC for all NetCDF files in a directory (non-recursive).
    """
    dir_path = Path(dir_path)
    prof = profile or default_profile()
    nc_files = list(dir_path.glob("*.nc"))
    logger.info("Processing directory: %s (%d files)", dir_path.name, len(nc_files))
    
    for i, nc_file in enumerate(nc_files, 1):
        logger.debug("File %d/%d: %s", i, len(nc_files), nc_file.name)
        run_qc_for_file(
            nc_file,
            mapping_path=mapping_path,
            location_tolerance=location_tolerance,
            gross_range_overrides=gross_range_overrides,
            climatology_overrides=climatology_overrides,
            station_climatology_config_path=station_climatology_config_path,
            station_depth_classification_path=station_depth_classification_path,
            sensor_specs_path=sensor_specs_path,
            variable_sensor_map_path=variable_sensor_map_path,
            station_coords_csv=station_coords_csv,
            spike_thresholds_path=spike_thresholds_path,
            rate_of_change_thresholds_path=rate_of_change_thresholds_path,
            flat_line_config_path=flat_line_config_path,
            profile=prof,
            data_root=data_root,
        )
    
    logger.info("Completed directory: %s", dir_path.name)


def run_qc_for_all(
    base_dir: Path | str | None = None,
    mapping_path: Path | str | None = None,
    location_tolerance: float = LOCATION_TOLERANCE,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    station_climatology_config_path: Path | str | None = None,
    station_depth_classification_path: Path | str | None = None,
    sensor_specs_path: Path | str | None = None,
    variable_sensor_map_path: Path | str | None = None,
    station_coords_csv: Path | str | None = None,
    spike_thresholds_path: Path | str | None = None,
    rate_of_change_thresholds_path: Path | str | None = None,
    flat_line_config_path: Path | str | None = None,
    profile: DatasetProfile | None = None,
) -> None:
    """
    Run QC for all SFER_CTD cruise directories (one level deep).

    Each subdirectory of *base_dir* is expected to be a cruise folder (e.g.
    ``WS24139/``) containing ``.nc`` files named ``cruiseID_station.nc``.
    """
    prof = profile or default_profile()
    base_dir = Path(base_dir) if base_dir is not None else prof.data_root
    cruise_dirs = [child for child in base_dir.iterdir() if child.is_dir()]
    logger.info("Found %d cruise directories in %s", len(cruise_dirs), base_dir)
    
    for i, cruise_dir in enumerate(cruise_dirs, 1):
        logger.info("Processing cruise %d/%d: %s", i, len(cruise_dirs), cruise_dir.name)
        run_qc_for_directory(
            cruise_dir,
            mapping_path=mapping_path,
            location_tolerance=location_tolerance,
            gross_range_overrides=gross_range_overrides,
            climatology_overrides=climatology_overrides,
            station_climatology_config_path=station_climatology_config_path,
            station_depth_classification_path=station_depth_classification_path,
            sensor_specs_path=sensor_specs_path,
            variable_sensor_map_path=variable_sensor_map_path,
            station_coords_csv=station_coords_csv,
            spike_thresholds_path=spike_thresholds_path,
            rate_of_change_thresholds_path=rate_of_change_thresholds_path,
            flat_line_config_path=flat_line_config_path,
            profile=prof,
            data_root=base_dir,
        )


def list_available_tests() -> List[str]:
    """
    Return available test names.
    """
    return list(_AVAILABLE_TESTS)


def list_file_variables(
    nc_path: Path | str,
    mapping_path: Path | str | None = None,
    profile: DatasetProfile | None = None,
) -> List[str]:
    """
    List variables in the file that are eligible for QC (per mapping).
    """
    prof = profile or default_profile()
    mapping = load_mapping(resolve_config_path("variable_mapping", prof, mapping_path))
    ds = load_nc_file(nc_path)
    return get_qc_variables(ds, mapping)


def run_single_test(
    nc_path: Path | str,
    test_name: str,
    variable: str | None = None,
    mapping_path: Path | str | None = None,
    location_tolerance: float = LOCATION_TOLERANCE,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    station_climatology_config_path: Path | str | None = None,
    station_depth_classification_path: Path | str | None = None,
    sensor_specs_path: Path | str | None = None,
    variable_sensor_map_path: Path | str | None = None,
    station_coords_csv: Path | str | None = None,
    spike_thresholds_path: Path | str | None = None,
    rate_of_change_thresholds_path: Path | str | None = None,
    flat_line_config_path: Path | str | None = None,
    print_summary: bool = True,
    profile: DatasetProfile | None = None,
) -> List[QCTestResult]:
    """
    Run a single QC test on one file for a specific variable or all mapped variables.
    Returns a list of QCTestResult objects.
    """
    if test_name not in _AVAILABLE_TESTS:
        raise ValueError(f"Unsupported test_name {test_name}. Available: {_AVAILABLE_TESTS}")

    nc_path = Path(nc_path)
    prof = profile or default_profile()
    mapping = load_mapping(resolve_config_path("variable_mapping", prof, mapping_path))
    ds = load_nc_file(nc_path)

    station_id = get_station_id(ds, prof.metadata)
    station_coords = resolve_coords_by_station_id(
        station_id,
        station_csv=resolve_config_path("station_coords", prof, station_coords_csv),
    )

    dynamic_ranges = resolve_gross_ranges(
        ds,
        specs_path=resolve_config_path("sensor_specs", prof, sensor_specs_path),
        variable_map_path=resolve_config_path("variable_sensor_map", prof, variable_sensor_map_path),
    )
    gross_ranges = {**dynamic_ranges, **(gross_range_overrides or {})}
    clim_config = get_climatology_config_for_file(
        ds,
        limits_json_path=resolve_config_path("station_climatology", prof, station_climatology_config_path),
        classification_json_path=resolve_config_path(
            "station_depth_classification",
            prof,
            station_depth_classification_path,
        ),
        metadata=prof.metadata,
    )
    if clim_config and climatology_overrides:
        clim_config = {**clim_config, **climatology_overrides}

    spike_thresholds = load_spike_thresholds(resolve_config_path("spike_thresholds", prof, spike_thresholds_path))
    rate_of_change_thresholds = load_rate_of_change_thresholds(
        resolve_config_path("rate_of_change_thresholds", prof, rate_of_change_thresholds_path)
    )
    flat_line_cfg = load_flat_line_config(resolve_config_path("flat_line_config", prof, flat_line_config_path))

    vars_to_run = [variable] if variable else get_qc_variables(ds, mapping)
    results: List[QCTestResult] = []

    for var_name in vars_to_run:
        if var_name not in ds:
            logger.warning("Variable %s not in dataset %s, skipping...", var_name, nc_path.name)
            continue
        category = get_variable_category(var_name, mapping)
        data_var = ds[var_name]

        depth = _get_depth_for_var(ds, data_var, prof)
        time = _get_time_for_var(ds, data_var, prof)
        flags = _run_single_test_for_var(
            test_name=test_name,
            var_name=var_name,
            data_var=data_var,
            ds=ds,
            category=category,
            gross_ranges=gross_ranges,
            climatology_config=clim_config,
            expected_location=station_coords,
            location_tolerance=location_tolerance,
            spike_thresholds=spike_thresholds,
            rate_of_change_thresholds=rate_of_change_thresholds,
            flat_line_config=flat_line_cfg,
            profile=prof,
        )

        summary = _flag_summary(flags)
        result = QCTestResult(
            test_name=test_name,
            variable_name=var_name,
            file_path=str(nc_path),
            data=np.asarray(data_var),
            flags=np.asarray(flags),
            depth=depth,
            time=time,
            flag_summary=summary,
        )
        if print_summary:
            result.print_summary()
        results.append(result)

    return results
