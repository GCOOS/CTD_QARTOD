"""
Run QC pipeline for SFER_CTD NetCDF files.

Station and cruise IDs are read from the ``station`` and ``cruiseID``
variables embedded in each file — no filename parsing is required.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

from qc_config import (
    DATASET_DIR,
    GROSS_RANGE_CONFIG,
    LOCATION_TOLERANCE,
    QC_FLAGS,
    SENSOR_SPECS_JSON,
    STATION_COORDS_CSV,
    TEST_CATEGORIES,
    VARIABLE_MAPPING_JSON,
    VARIABLE_SENSOR_MAP_JSON,
    get_climatology_config_for_file,
)
from instrument_resolver import resolve_gross_ranges
from qc_data_loader import (
    get_lon_lat,
    get_qc_variables,
    get_station_id,
    get_cruise_id,
    get_variable_category,
    load_mapping,
    load_nc_file,
)
from station_resolver import resolve_coords_by_station_id
from qc_result_viz import QCTestResult, _flag_summary
from qc_tests import (
    climatology_qc,
    decreasing_radiance_test,
    gap_test,
    gross_range_qc,
    location_qc,
    syntax_test,
)
from qc_writer import save_dataset, write_qc_results

_AVAILABLE_TESTS = list(TEST_CATEGORIES.keys())


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


def _get_depth_for_var(ds: xr.Dataset, var: xr.DataArray) -> Optional[np.ndarray]:
    if "depth" in var.coords:
        return _broadcast_like(var, var.coords["depth"])
    if "depth" in ds:
        return _broadcast_like(var, ds["depth"])
    return None


def _get_time_for_var(ds: xr.Dataset, var: xr.DataArray) -> Optional[np.ndarray]:
    if "time" in var.coords:
        return _broadcast_like(var, var.coords["time"])
    if "time" in ds:
        return _broadcast_like(var, ds["time"])
    return None


def _run_common_tests(
    ds: xr.Dataset,
    var_name: str,
    category: Optional[str],
    gross_ranges: Mapping[str, Mapping[str, tuple]] | None,
    climatology_config: Mapping[str, Iterable[Mapping]] | None,
    expected_location: Optional[tuple[float, float]],
    location_tolerance: float,
) -> xr.Dataset:
    data_var = ds[var_name]

    if _should_run_test("gap_test", category):
        gap_flags = gap_test(data_var)
        ds = write_qc_results(ds, var_name, "gap_test", gap_flags)

    if _should_run_test("syntax_test", category):
        syntax_flags = syntax_test(data_var)
        ds = write_qc_results(ds, var_name, "syntax_test", syntax_flags)

    if _should_run_test("location_test", category):
        lon, lat = get_lon_lat(ds)
        if lon is not None and lat is not None and expected_location is not None:
            lon_b = _broadcast_like(data_var, lon)
            lat_b = _broadcast_like(data_var, lat)
            expected_lat, expected_lon = expected_location
            loc_flags = location_qc(
                lon=lon_b,
                lat=lat_b,
                expected_lon=expected_lon,
                expected_lat=expected_lat,
                tolerance=location_tolerance,
            )
        else:
            loc_flags = np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
        ds = write_qc_results(ds, var_name, "location_test", loc_flags)

    if _should_run_test("gross_range_test", category):
        ranges = gross_ranges.get(var_name) if gross_ranges else None
        if ranges:
            fail_span = tuple(ranges.get("fail_span", ())) or None
            suspect_span = tuple(ranges.get("suspect_span", ())) or None
            if fail_span:
                gr_flags = gross_range_qc(data_var, fail_span=fail_span, suspect_span=suspect_span)
            else:
                gr_flags = np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
        else:
            gr_flags = np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
        ds = write_qc_results(ds, var_name, "gross_range_test", gr_flags)

    if _should_run_test("decreasing_radiance_test", category):
        depth = _get_depth_for_var(ds, data_var)
        dec_flags = decreasing_radiance_test(data_var, depth=depth, non_increasing=True)
        ds = write_qc_results(ds, var_name, "decreasing_radiance_test", dec_flags)

    if _should_run_test("climatology_test", category):
        depth = _get_depth_for_var(ds, data_var)
        time = _get_time_for_var(ds, data_var)
        config = None
        if climatology_config:
            config = climatology_config.get(var_name)
        if depth is None or time is None or config is None:
            clim_flags = np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
        else:
            clim_flags = climatology_qc(data_var, time=time, depth=depth, config=config)
        ds = write_qc_results(ds, var_name, "climatology_test", clim_flags)

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
) -> np.ndarray:
    """
    Execute a single QC test for the given variable and return flags.
    """
    if not _should_run_test(test_name, category):
        return np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)

    if test_name == "gap_test":
        return gap_test(data_var)

    if test_name == "syntax_test":
        return syntax_test(data_var)

    if test_name == "location_test":
        lon, lat = get_lon_lat(ds)
        if lon is not None and lat is not None and expected_location is not None:
            lon_b = _broadcast_like(data_var, lon)
            lat_b = _broadcast_like(data_var, lat)
            expected_lat, expected_lon = expected_location
            return location_qc(
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
                return gross_range_qc(data_var, fail_span=fail_span, suspect_span=suspect_span)
        return np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)

    if test_name == "decreasing_radiance_test":
        depth = _get_depth_for_var(ds, data_var)
        return decreasing_radiance_test(data_var, depth=depth, non_increasing=True)

    if test_name == "climatology_test":
        depth = _get_depth_for_var(ds, data_var)
        time = _get_time_for_var(ds, data_var)
        config = None
        if climatology_config:
            config = climatology_config.get(var_name)
        if depth is None or time is None or config is None:
            return np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
        return climatology_qc(data_var, time=time, depth=depth, config=config)

    raise ValueError(f"Unknown test_name: {test_name}")


def run_qc_for_file(
    nc_path: Path | str,
    mapping_path: Path | str = VARIABLE_MAPPING_JSON,
    location_tolerance: float = LOCATION_TOLERANCE,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    station_climatology_config_path: Path | str | None = None,
    sensor_specs_path: Path | str = SENSOR_SPECS_JSON,
    variable_sensor_map_path: Path | str = VARIABLE_SENSOR_MAP_JSON,
    station_coords_csv: Path | str = STATION_COORDS_CSV,
) -> None:
    """
    Run QC for a single NetCDF file and write results back to the same file.

    Station and cruise IDs are obtained from the ``station`` and ``cruiseID``
    variables inside the file.
    """
    nc_path = Path(nc_path)
    logger.info("Processing file: %s", nc_path.name)
    
    mapping = load_mapping(mapping_path)
    ds = load_nc_file(nc_path)

    station_id = get_station_id(ds)
    cruise_id = get_cruise_id(ds)
    logger.debug("  Station ID: %s, Cruise ID: %s", station_id, cruise_id)
    
    station_coords = resolve_coords_by_station_id(station_id, station_csv=station_coords_csv)
    if station_coords:
        logger.debug("  Station coords: lat=%.4f, lon=%.4f", station_coords[0], station_coords[1])
    else:
        logger.debug("  Station coords: not found")

    dynamic_ranges = resolve_gross_ranges(
        ds, specs_path=sensor_specs_path, variable_map_path=variable_sensor_map_path,
    )
    gross_ranges = {**GROSS_RANGE_CONFIG, **dynamic_ranges, **(gross_range_overrides or {})}
    if station_climatology_config_path:
        clim_config = get_climatology_config_for_file(ds, station_climatology_config_path)
    else:
        clim_config = get_climatology_config_for_file(ds)
    if clim_config and climatology_overrides:
        clim_config = {**clim_config, **climatology_overrides}

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
        )

    save_dataset(ds, nc_path)
    logger.info("  Saved QC results to %s", nc_path.name)


def run_qc_for_directory(
    dir_path: Path | str,
    mapping_path: Path | str = VARIABLE_MAPPING_JSON,
    location_tolerance: float = LOCATION_TOLERANCE,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    station_climatology_config_path: Path | str | None = None,
    sensor_specs_path: Path | str = SENSOR_SPECS_JSON,
    variable_sensor_map_path: Path | str = VARIABLE_SENSOR_MAP_JSON,
    station_coords_csv: Path | str = STATION_COORDS_CSV,
) -> None:
    """
    Run QC for all NetCDF files in a directory (non-recursive).
    """
    dir_path = Path(dir_path)
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
            sensor_specs_path=sensor_specs_path,
            variable_sensor_map_path=variable_sensor_map_path,
            station_coords_csv=station_coords_csv,
        )
    
    logger.info("Completed directory: %s", dir_path.name)


def run_qc_for_all(
    base_dir: Path | str = DATASET_DIR,
    mapping_path: Path | str = VARIABLE_MAPPING_JSON,
    location_tolerance: float = LOCATION_TOLERANCE,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    station_climatology_config_path: Path | str | None = None,
    sensor_specs_path: Path | str = SENSOR_SPECS_JSON,
    variable_sensor_map_path: Path | str = VARIABLE_SENSOR_MAP_JSON,
    station_coords_csv: Path | str = STATION_COORDS_CSV,
) -> None:
    """
    Run QC for all SFER_CTD cruise directories (one level deep).

    Each subdirectory of *base_dir* is expected to be a cruise folder (e.g.
    ``WS24139/``) containing ``.nc`` files named ``cruiseID_station.nc``.
    """
    base_dir = Path(base_dir)
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
            sensor_specs_path=sensor_specs_path,
            variable_sensor_map_path=variable_sensor_map_path,
            station_coords_csv=station_coords_csv,
        )


def list_available_tests() -> List[str]:
    """
    Return available test names.
    """
    return list(_AVAILABLE_TESTS)


def list_file_variables(nc_path: Path | str, mapping_path: Path | str = VARIABLE_MAPPING_JSON) -> List[str]:
    """
    List variables in the file that are eligible for QC (per mapping).
    """
    mapping = load_mapping(mapping_path)
    ds = load_nc_file(nc_path)
    return get_qc_variables(ds, mapping)


def run_single_test(
    nc_path: Path | str,
    test_name: str,
    variable: str | None = None,
    mapping_path: Path | str = VARIABLE_MAPPING_JSON,
    location_tolerance: float = LOCATION_TOLERANCE,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    station_climatology_config_path: Path | str | None = None,
    sensor_specs_path: Path | str = SENSOR_SPECS_JSON,
    variable_sensor_map_path: Path | str = VARIABLE_SENSOR_MAP_JSON,
    station_coords_csv: Path | str = STATION_COORDS_CSV,
    print_summary: bool = True,
) -> List[QCTestResult]:
    """
    Run a single QC test on one file for a specific variable or all mapped variables.
    Returns a list of QCTestResult objects.
    """
    if test_name not in _AVAILABLE_TESTS:
        raise ValueError(f"Unsupported test_name {test_name}. Available: {_AVAILABLE_TESTS}")

    nc_path = Path(nc_path)
    mapping = load_mapping(mapping_path)
    ds = load_nc_file(nc_path)

    station_id = get_station_id(ds)
    station_coords = resolve_coords_by_station_id(station_id, station_csv=station_coords_csv)

    dynamic_ranges = resolve_gross_ranges(
        ds, specs_path=sensor_specs_path, variable_map_path=variable_sensor_map_path,
    )
    gross_ranges = {**GROSS_RANGE_CONFIG, **dynamic_ranges, **(gross_range_overrides or {})}
    if station_climatology_config_path:
        clim_config = get_climatology_config_for_file(ds, station_climatology_config_path)
    else:
        clim_config = get_climatology_config_for_file(ds)
    if clim_config and climatology_overrides:
        clim_config = {**clim_config, **climatology_overrides}

    vars_to_run = [variable] if variable else get_qc_variables(ds, mapping)
    results: List[QCTestResult] = []

    for var_name in vars_to_run:
        if var_name not in ds:
            logger.warning("Variable %s not in dataset %s, skipping...", var_name, nc_path.name)
            continue
        category = get_variable_category(var_name, mapping)
        data_var = ds[var_name]

        depth = _get_depth_for_var(ds, data_var)
        time = _get_time_for_var(ds, data_var)
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
