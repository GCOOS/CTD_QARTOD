"""
Run QC pipeline for SFER_CTD NetCDF files.

Station and cruise IDs are read from the ``station`` and ``cruiseID``
variables embedded in each file — no filename parsing is required.
"""

from __future__ import annotations

import logging
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
    QC_FLAGS,
    TEST_CATEGORIES,
    get_climatology_config_for_file,
    load_flat_line_config,
    load_location_config,
    load_rate_of_change_thresholds,
    load_spike_thresholds,
)
from qc_manifest import (
    QCRunSummary,
    build_qc_run_manifest,
    utc_now,
    write_qc_run_manifest,
)
from instrument_resolver import resolve_gross_ranges
from qc_limit_generation import prepare_dataset_qc
from qc_time import decode_qc_time
from qc_data_loader import (
    get_coord_for_var,
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
    climatology_test,
    decreasing_radiance_test,
    flat_line_test,
    gross_range_test,
    location_test,
    rate_of_change_test,
    spike_test,
)
from qc_validation import REQUIRED_QC_PATHS, validate_qc_profile
from qc_writer import (
    aggregate_qc_flags,
    aggregate_qc_variable_name,
    append_qc_history,
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


def _should_run_test(test_name: str, category: str | None) -> bool:
    """Return True if *test_name* should run for the given variable category."""
    cats = TEST_CATEGORIES.get(test_name)
    return cats is not None and category in cats


def _broadcast_like(target: xr.DataArray, source: xr.DataArray | None) -> Optional[np.ndarray]:
    """
    Broadcast source to target by named dimensions, if present.
    """
    if source is None:
        return None
    extra_dims = set(source.dims) - set(target.dims)
    if extra_dims:
        if all(source.sizes[dim] == 1 for dim in extra_dims):
            source = source.squeeze(tuple(extra_dims), drop=True)
            extra_dims = set(source.dims) - set(target.dims)
    if extra_dims:
        names = ", ".join(sorted(extra_dims))
        raise ValueError(
            f"cannot broadcast source dimensions ({names}) to target {target.dims}"
        )
    ordered_dims = tuple(dim for dim in target.dims if dim in source.dims)
    ordered = source.transpose(*ordered_dims)
    shape = tuple(source.sizes.get(dim, 1) for dim in target.dims)
    for dim in ordered_dims:
        if source.sizes[dim] not in (1, target.sizes[dim]):
            raise ValueError(
                f"cannot broadcast dimension {dim!r} of size {source.sizes[dim]} "
                f"to size {target.sizes[dim]}"
            )
    return np.broadcast_to(np.asarray(ordered).reshape(shape), target.shape)


def _iter_attr_values(value: object) -> Iterable[object]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        arr = np.asarray(value)
    except Exception:
        return (value,)
    if arr.shape == ():
        return (arr.item(),)
    return tuple(arr.ravel().tolist())


def _missing_mask_for_var(data_var: xr.DataArray) -> np.ndarray:
    """Return samples that should be treated as missing before any QC test."""
    raw = np.ma.asarray(data_var.values)
    mask = np.ma.getmaskarray(raw).copy()
    arr = np.asarray(raw.data)

    if np.issubdtype(arr.dtype, np.number):
        mask |= ~np.isfinite(arr.astype(float, copy=False))

    sentinels: list[object] = []
    for attr_name in ("_FillValue", "missing_value"):
        sentinels.extend(_iter_attr_values(data_var.attrs.get(attr_name)))

    for sentinel in sentinels:
        try:
            if np.issubdtype(arr.dtype, np.number):
                sentinel_value = float(sentinel)
                if np.isfinite(sentinel_value):
                    mask |= arr == sentinel_value
            else:
                mask |= arr == sentinel
        except (TypeError, ValueError):
            continue

    return np.asarray(mask, dtype=bool)


def _data_var_with_missing_as_nan(data_var: xr.DataArray, missing_mask: np.ndarray) -> xr.DataArray:
    """Return a numeric copy of *data_var* with encoded missing samples as NaN."""
    if not np.any(missing_mask):
        return data_var
    try:
        values = np.asarray(data_var.values, dtype=float).copy()
    except (TypeError, ValueError):
        return data_var
    values[missing_mask] = np.nan
    return xr.DataArray(
        values,
        dims=data_var.dims,
        coords=data_var.coords,
        attrs=dict(data_var.attrs),
        name=data_var.name,
    )


def _apply_missing_flags(flags: np.ndarray, missing_mask: np.ndarray) -> np.ndarray:
    out = np.asarray(flags, dtype=int).copy()
    if out.shape == missing_mask.shape:
        out[missing_mask] = QC_FLAGS["MISSING"]
    return out


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
        return _broadcast_like(var, decode_qc_time(time))
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
    suspect = spike["suspect_threshold"]
    fail = spike["fail_threshold"]
    if suspect is None or fail is None:
        return None
    return (float(suspect), float(fail))


def _roc_threshold_for_var(
    rate_of_change_thresholds: Mapping[str, Mapping[str, Any]] | None,
    var_name: str,
) -> Optional[float]:
    if not rate_of_change_thresholds:
        return None
    roc = rate_of_change_thresholds.get(var_name)
    if not isinstance(roc, dict) or "threshold" not in roc:
        return None
    threshold = roc["threshold"]
    if threshold is None:
        return None
    v = float(threshold)
    if not np.isfinite(v) or v <= 0:
        return None
    return v


def _test_provenance(
    test_name: str,
    var_name: str,
    profile: DatasetProfile,
    *,
    gross_ranges: Mapping[str, Mapping[str, tuple]] | None,
    climatology_config: Mapping[str, Iterable[Mapping]] | None,
    expected_location: tuple[float, float] | None,
    location_tolerance: float | None,
    spike_thresholds: Mapping[str, Mapping[str, Any]] | None,
    rate_of_change_thresholds: Mapping[str, Mapping[str, Any]] | None,
    flat_line_config: Mapping[str, Any] | None,
) -> tuple[dict[str, object], str]:
    def sources(*keys: str) -> str:
        return ";".join(str(resolve_config_path(key, profile)) for key in keys)

    if test_name in profile.qc_test_modes:
        source = str(profile.profile_path) if profile.profile_path else "dataset profile"
        return {"mode": profile.qc_test_modes[test_name]}, source
    if test_name == "location_test":
        location = None
        if expected_location is not None:
            location = {
                "latitude": expected_location[0],
                "longitude": expected_location[1],
            }
        return {
            "expected_location": location,
            "tolerance": location_tolerance,
        }, sources("station_coords", "location_config")
    if test_name == "gross_range_test":
        return {
            "ranges": (gross_ranges or {}).get(var_name)
        }, sources("sensor_specs", "variable_sensor_map")
    if test_name == "decreasing_radiance_test":
        return {"direction": "decreases_with_increasing_depth"}, "built-in"
    if test_name == "climatology_test":
        return {
            "limits": (climatology_config or {}).get(var_name)
        }, sources("station_climatology", "station_depth_classification")
    if test_name == "flat_line_test":
        return dict(flat_line_config or {}), sources("flat_line_config")
    if test_name == "spike_test":
        return {
            "thresholds": (spike_thresholds or {}).get(var_name)
        }, sources("spike_thresholds")
    if test_name == "rate_of_change_test":
        return {
            "thresholds": (rate_of_change_thresholds or {}).get(var_name)
        }, sources("rate_of_change_thresholds")
    raise ValueError(f"Unknown test_name: {test_name}")


def _run_common_tests(
    ds: xr.Dataset,
    var_name: str,
    category: Optional[str],
    gross_ranges: Mapping[str, Mapping[str, tuple]] | None,
    climatology_config: Mapping[str, Iterable[Mapping]] | None,
    expected_location: Optional[tuple[float, float]],
    location_tolerance: float | None,
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
        applied_config, config_source = _test_provenance(
            test_name,
            var_name,
            prof,
            gross_ranges=gross_ranges,
            climatology_config=climatology_config,
            expected_location=expected_location,
            location_tolerance=location_tolerance,
            spike_thresholds=spike_thresholds,
            rate_of_change_thresholds=rate_of_change_thresholds,
            flat_line_config=flat_line_config,
        )
        ds = write_qc_results(
            ds,
            var_name,
            test_name,
            flags,
            applied_config=applied_config,
            config_source=config_source,
        )
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
    location_tolerance: float | None,
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
    missing_mask = _missing_mask_for_var(data_var)
    test_data_var = _data_var_with_missing_as_nan(data_var, missing_mask)

    if test_name in prof.qc_test_modes:
        if prof.qc_test_modes[test_name] == "run":
            raise ValueError(f"{test_name} run mode is not implemented")
        flags = np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int)
        return _apply_missing_flags(flags, missing_mask)

    if test_name == "location_test":
        lon, lat = get_lon_lat(ds, prof.metadata)
        if (
            lon is not None
            and lat is not None
            and expected_location is not None
            and location_tolerance is not None
        ):
            lon_b = _broadcast_like(test_data_var, lon)
            lat_b = _broadcast_like(test_data_var, lat)
            expected_lat, expected_lon = expected_location
            return _apply_missing_flags(
                location_test(
                    lon=lon_b,
                    lat=lat_b,
                    expected_lon=expected_lon,
                    expected_lat=expected_lat,
                    tolerance=location_tolerance,
                ),
                missing_mask,
            )
        return _apply_missing_flags(np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int), missing_mask)

    if test_name == "gross_range_test":
        ranges = gross_ranges.get(var_name) if gross_ranges else None
        if ranges:
            fail_span = tuple(ranges.get("fail_span", ())) or None
            suspect_span = tuple(ranges.get("suspect_span", ())) or None
            if fail_span:
                return _apply_missing_flags(
                    gross_range_test(test_data_var, fail_span=fail_span, suspect_span=suspect_span),
                    missing_mask,
                )
        return _apply_missing_flags(np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int), missing_mask)

    if test_name == "decreasing_radiance_test":
        depth = _get_depth_for_var(ds, test_data_var, prof)
        aligned_data, moved_axis = align_for_profile_tests(
            test_data_var,
            test_data_var.dims,
            prof.metadata.sample_dimension,
        )
        aligned_depth = None
        if depth is not None:
            aligned_depth, _ = align_for_profile_tests(
                depth,
                data_var.dims,
                prof.metadata.sample_dimension,
            )
        flags = decreasing_radiance_test(aligned_data, depth=aligned_depth)
        return _apply_missing_flags(restore_flags_shape(flags, data_var.shape, moved_axis), missing_mask)

    if test_name == "climatology_test":
        depth = _get_depth_for_var(ds, test_data_var, prof)
        config = None
        if climatology_config:
            config = climatology_config.get(var_name)
        time = _get_time_for_var(ds, test_data_var, prof) if depth is not None and config is not None else None
        if depth is None or time is None or config is None:
            return _apply_missing_flags(np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int), missing_mask)
        return _apply_missing_flags(
            climatology_test(test_data_var, time=time, depth=depth, config=config),
            missing_mask,
        )

    if test_name == "flat_line_test":
        flat_cfg = dict(flat_line_config or load_flat_line_config())
        aligned_data, moved_axis = align_for_profile_tests(
            test_data_var,
            test_data_var.dims,
            prof.metadata.sample_dimension,
        )
        flags = flat_line_test(
            aligned_data,
            rep_cnt_suspect=flat_cfg["rep_cnt_suspect"],
            rep_cnt_fail=flat_cfg["rep_cnt_fail"],
            eps=flat_cfg["eps"],
        )
        return _apply_missing_flags(restore_flags_shape(flags, data_var.shape, moved_axis), missing_mask)

    if test_name == "spike_test":
        sp = _spike_params_for_var(spike_thresholds, var_name)
        if sp is None:
            return _apply_missing_flags(np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int), missing_mask)
        s_th, f_th = sp
        aligned_data, moved_axis = align_for_profile_tests(
            test_data_var,
            test_data_var.dims,
            prof.metadata.sample_dimension,
        )
        flags = spike_test(aligned_data, suspect_threshold=s_th, fail_threshold=f_th)
        return _apply_missing_flags(restore_flags_shape(flags, data_var.shape, moved_axis), missing_mask)

    if test_name == "rate_of_change_test":
        roc_thr = _roc_threshold_for_var(rate_of_change_thresholds, var_name)
        if roc_thr is None:
            return _apply_missing_flags(np.full(data_var.shape, QC_FLAGS["NOT_EVALUATED"], dtype=int), missing_mask)
        aligned_data, moved_axis = align_for_profile_tests(
            test_data_var,
            test_data_var.dims,
            prof.metadata.sample_dimension,
        )
        flags = rate_of_change_test(aligned_data, threshold=roc_thr)
        return _apply_missing_flags(restore_flags_shape(flags, data_var.shape, moved_axis), missing_mask)

    raise ValueError(f"Unknown test_name: {test_name}")


def run_qc_for_file(
    nc_path: Path | str,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    profile: DatasetProfile | None = None,
    data_root: Path | str | None = None,
) -> Path:
    """
    Run QC for a single NetCDF file and write results back to the same file.

    Station and cruise IDs are obtained from the ``station`` and ``cruiseID``
    variables inside the file.
    """
    nc_path = Path(nc_path)
    prof = prepare_dataset_qc(profile or default_profile())
    root_for_output = Path(data_root) if data_root is not None else prof.data_root
    mapping_file = resolve_config_path("variable_mapping", prof)
    station_coords_file = resolve_config_path("station_coords", prof)
    location_file = resolve_config_path("location_config", prof)
    station_climatology_file = resolve_config_path("station_climatology", prof)
    station_depth_file = resolve_config_path("station_depth_classification", prof)
    sensor_specs_file = resolve_config_path("sensor_specs", prof)
    variable_sensor_file = resolve_config_path("variable_sensor_map", prof)
    spike_file = resolve_config_path("spike_thresholds", prof)
    rate_file = resolve_config_path("rate_of_change_thresholds", prof)
    flat_line_file = resolve_config_path("flat_line_config", prof)
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
    clim_config = {**(clim_config or {}), **(climatology_overrides or {})}

    spike_thresholds = load_spike_thresholds(spike_file)
    rate_of_change_thresholds = load_rate_of_change_thresholds(rate_file)
    location_cfg = load_location_config(location_file)
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
            location_tolerance=location_cfg["tolerance"],
            spike_thresholds=spike_thresholds,
            rate_of_change_thresholds=rate_of_change_thresholds,
            flat_line_config=flat_line_cfg,
            profile=prof,
        )

    output_path = resolve_output_path(nc_path, prof, root_for_output)
    ds = append_qc_history(ds)
    save_dataset(ds, output_path)
    logger.info("  Saved QC results to %s", output_path)
    return output_path


def run_qc_for_directory(
    dir_path: Path | str,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    profile: DatasetProfile | None = None,
    data_root: Path | str | None = None,
) -> List[Path]:
    """
    Run QC for all NetCDF files in a directory (non-recursive).
    """
    dir_path = Path(dir_path)
    prof = profile or default_profile()
    nc_files = sorted(dir_path.glob("*.nc"))
    logger.info("Processing directory: %s (%d files)", dir_path.name, len(nc_files))
    
    output_paths: List[Path] = []
    for i, nc_file in enumerate(nc_files, 1):
        logger.debug("File %d/%d: %s", i, len(nc_files), nc_file.name)
        output_path = run_qc_for_file(
            nc_file,
            gross_range_overrides=gross_range_overrides,
            climatology_overrides=climatology_overrides,
            profile=prof,
            data_root=data_root,
        )
        if output_path is not None:
            output_paths.append(output_path)
    
    logger.info("Completed directory: %s", dir_path.name)
    return output_paths


def run_qc_for_all(
    base_dir: Path | str | None = None,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
    profile: DatasetProfile | None = None,
) -> QCRunSummary:
    """
    Run QC for all SFER_CTD cruise directories (one level deep).

    Each subdirectory of *base_dir* is expected to be a cruise folder (e.g.
    ``WS24139/``) containing ``.nc`` files named ``cruiseID_station.nc``.
    """
    prof = prepare_dataset_qc(profile or default_profile())
    base_dir = Path(base_dir) if base_dir is not None else prof.data_root
    nc_files = sorted(base_dir.glob("*/*.nc"))
    configured_paths = {
        key: path
        for key in REQUIRED_QC_PATHS
        if (path := prof.paths.get(key)) is not None
    }
    manifest_path, manifest = build_qc_run_manifest(
        prof, base_dir, configured_paths, len(nc_files)
    )
    write_qc_run_manifest(manifest_path, manifest)
    written_paths: list[Path] = []

    try:
        validate_qc_profile(prof)
        if not nc_files:
            raise ValueError(f"No NetCDF files found under {base_dir}")
        logger.info("Found %d NetCDF files in %s", len(nc_files), base_dir)
        for index, nc_file in enumerate(nc_files, 1):
            logger.info("Processing file %d/%d: %s", index, len(nc_files), nc_file)
            output_path = run_qc_for_file(
                nc_file,
                gross_range_overrides=gross_range_overrides,
                climatology_overrides=climatology_overrides,
                profile=prof,
                data_root=base_dir,
            )
            manifest["counts"]["processed"] += 1
            if output_path is not None:
                written_paths.append(output_path)
                manifest["counts"]["written"] += 1
            write_qc_run_manifest(manifest_path, manifest)
    except BaseException as exc:
        manifest["status"] = "failed"
        manifest["completed_at"] = utc_now()
        manifest["counts"]["failed"] += 1
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        write_qc_run_manifest(manifest_path, manifest)
        raise

    manifest["status"] = "complete"
    manifest["completed_at"] = utc_now()
    write_qc_run_manifest(manifest_path, manifest)
    return QCRunSummary(
        manifest_path=manifest_path,
        discovered=len(nc_files),
        written_paths=tuple(written_paths),
    )


def list_available_tests() -> List[str]:
    """
    Return available test names.
    """
    return list(_AVAILABLE_TESTS)


def list_file_variables(
    nc_path: Path | str,
    profile: DatasetProfile | None = None,
) -> List[str]:
    """
    List variables in the file that are eligible for QC (per mapping).
    """
    prof = profile or default_profile()
    mapping = load_mapping(resolve_config_path("variable_mapping", prof))
    ds = load_nc_file(nc_path)
    return get_qc_variables(ds, mapping)


def run_single_test(
    nc_path: Path | str,
    test_name: str,
    variable: str | None = None,
    gross_range_overrides: Mapping[str, Mapping[str, tuple]] | None = None,
    climatology_overrides: Mapping[str, Iterable[Mapping]] | None = None,
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
    prof = prepare_dataset_qc(profile or default_profile())
    mapping = load_mapping(resolve_config_path("variable_mapping", prof))
    ds = load_nc_file(nc_path)

    station_id = get_station_id(ds, prof.metadata)
    station_coords = resolve_coords_by_station_id(
        station_id,
        station_csv=resolve_config_path("station_coords", prof),
    )

    dynamic_ranges = resolve_gross_ranges(
        ds,
        specs_path=resolve_config_path("sensor_specs", prof),
        variable_map_path=resolve_config_path("variable_sensor_map", prof),
    )
    gross_ranges = {**dynamic_ranges, **(gross_range_overrides or {})}
    clim_config = get_climatology_config_for_file(
        ds,
        limits_json_path=resolve_config_path("station_climatology", prof),
        classification_json_path=resolve_config_path("station_depth_classification", prof),
        metadata=prof.metadata,
    )
    clim_config = {**(clim_config or {}), **(climatology_overrides or {})}

    spike_thresholds = load_spike_thresholds(resolve_config_path("spike_thresholds", prof))
    rate_of_change_thresholds = load_rate_of_change_thresholds(
        resolve_config_path("rate_of_change_thresholds", prof)
    )
    location_cfg = load_location_config(resolve_config_path("location_config", prof))
    flat_line_cfg = load_flat_line_config(resolve_config_path("flat_line_config", prof))

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
            location_tolerance=location_cfg["tolerance"],
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
