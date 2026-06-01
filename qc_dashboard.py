"""Interactive Dash viewer for QC'd NetCDF outputs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import xarray as xr

from dataset_profile import REPO_ROOT, DatasetProfile, default_profile, resolve_config_path
from qc_config import QC_FLAGS, get_climatology_config_for_file, load_location_config
from qc_data_loader import flatten_mapping, get_coord_for_var, get_lon_lat, get_scalar_var, get_station_id, load_mapping
from station_resolver import resolve_coords_by_station_id

FLAG_ORDER = ("PASS", "NOT_EVALUATED", "SUSPECT", "FAIL", "MISSING")
FLAG_LABELS = {name: f"{name} ({QC_FLAGS[name]})" for name in FLAG_ORDER}
FLAG_COLORS = {
    "PASS": "#1f8f4d",
    "NOT_EVALUATED": "#9aa3ad",
    "SUSPECT": "#d97706",
    "FAIL": "#dc2626",
    "MISSING": "#2563eb",
}
FLAG_BY_VALUE = {value: name for name, value in QC_FLAGS.items()}

TEST_LABELS = {
    "agg": "Aggregate",
    "gap": "Gap",
    "syntax": "Syntax",
    "location": "Location",
    "gross_range": "Gross Range",
    "decreasing_radiance_test": "Decreasing Radiance",
    "climatology": "Climatology",
    "flat_line": "Flat Line",
    "spike": "Spike",
    "rate_of_change": "Rate Of Change",
}
ISSUE_FLAG_OPTIONS = ("SUSPECT", "FAIL")


@dataclass(frozen=True)
class QCTestOption:
    """A selectable QC result attached to a data variable."""

    label: str
    value: str
    qc_name: str


@dataclass(frozen=True)
class QCVariableOption:
    """A data variable with at least one selectable QC result."""

    label: str
    value: str


def default_viz_data_root(profile: DatasetProfile | None = None) -> Path:
    """Return the default NetCDF tree for visualization."""
    prof = profile or default_profile()
    if prof.output.mode == "duplicate":
        return prof.output.directory
    return prof.data_root


def discover_qc_files(data_root: Path | str) -> dict[str, list[Path]]:
    """Discover QC NetCDF files as ``{cruise: [files...]}``."""
    root = Path(data_root)
    if not root.exists() or not root.is_dir():
        return {}

    by_cruise: dict[str, list[Path]] = {}
    for path in sorted(root.glob("*/*.nc")):
        if path.is_file():
            by_cruise.setdefault(path.parent.name, []).append(path)
    return by_cruise


def _mapped_variable_names(mapping: dict[str, Iterable[str]]) -> list[str]:
    """Return mapped variable names in mapping-file order with duplicates removed."""
    names: list[str] = []
    seen: set[str] = set()
    for var_names in mapping.values():
        for name in var_names:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _test_label(var_name: str, qc_name: str, qc_var: xr.DataArray | None = None) -> str:
    prefix = f"{var_name}_qc_"
    suffix = qc_name[len(prefix) :] if qc_name.startswith(prefix) else qc_name
    label = TEST_LABELS.get(suffix, suffix.replace("_", " ").title())
    if qc_var is not None:
        long_name = str(qc_var.attrs.get("long_name") or "").strip()
        if long_name:
            marker = " Quality Flag"
            if long_name.endswith(marker):
                long_name = long_name[: -len(marker)]
            if label.lower() in long_name.lower():
                label = label
    return label


def _qc_names_from_ancillary(ds: xr.Dataset, var_name: str) -> list[str]:
    raw = str(ds[var_name].attrs.get("ancillary_variables") or "")
    names = [item for item in raw.split() if item in ds and item.startswith(f"{var_name}_qc_")]
    return names


def _qc_names_from_prefix(ds: xr.Dataset, var_name: str) -> list[str]:
    prefix = f"{var_name}_qc_"
    return sorted(name for name in ds.variables if name.startswith(prefix))


def discover_tests_for_variable(ds: xr.Dataset, var_name: str) -> list[QCTestOption]:
    """Return selectable QC tests for a variable, preferring ancillary metadata."""
    seen: set[str] = set()
    names: list[str] = []
    for name in [*_qc_names_from_ancillary(ds, var_name), *_qc_names_from_prefix(ds, var_name)]:
        if name not in seen:
            seen.add(name)
            names.append(name)

    def sort_key(name: str) -> tuple[int, str]:
        suffix = name.removeprefix(f"{var_name}_qc_")
        return (0 if suffix == "agg" else 1, TEST_LABELS.get(suffix, suffix))

    return [
        QCTestOption(label=_test_label(var_name, name, ds[name]), value=name, qc_name=name)
        for name in sorted(names, key=sort_key)
    ]


def discover_variables(
    ds: xr.Dataset,
    mapping: dict[str, Iterable[str]] | None = None,
) -> list[QCVariableOption]:
    """Return mapped numeric data variables that have at least one QC flag variable."""
    options: list[QCVariableOption] = []
    names = _mapped_variable_names(mapping) if mapping is not None else list(ds.data_vars)
    for name in names:
        if name not in ds or "_qc_" in name:
            continue
        data_var = ds[name]
        if not np.issubdtype(data_var.dtype, np.number):
            continue
        if not discover_tests_for_variable(ds, name):
            continue
        options.append(QCVariableOption(label=name, value=name))
    return options


def flag_summary(flags: Iterable[int]) -> list[dict[str, object]]:
    """Summarize QARTOD flags for display."""
    arr = np.asarray(flags).ravel()
    total = int(arr.size)
    rows: list[dict[str, object]] = []
    for name in FLAG_ORDER:
        value = QC_FLAGS[name]
        count = int(np.count_nonzero(arr == value))
        rows.append(
            {
                "flag": name,
                "value": value,
                "count": count,
                "percent": (count / total * 100.0) if total else 0.0,
                "color": FLAG_COLORS[name],
            }
        )
    return rows


def default_issue_index_path() -> Path:
    """Return the generated cache path for the dashboard issue index."""
    return REPO_ROOT / "output" / "qc_dashboard_index.json"


def _flag_count_map(flags: Iterable[int]) -> dict[str, int]:
    arr = np.asarray(flags, dtype=np.int16).ravel()
    return {
        "pass": int(np.count_nonzero(arr == QC_FLAGS["PASS"])),
        "not_evaluated": int(np.count_nonzero(arr == QC_FLAGS["NOT_EVALUATED"])),
        "suspect": int(np.count_nonzero(arr == QC_FLAGS["SUSPECT"])),
        "fail": int(np.count_nonzero(arr == QC_FLAGS["FAIL"])),
        "missing": int(np.count_nonzero(arr == QC_FLAGS["MISSING"])),
        "total": int(arr.size),
    }


def _test_value_from_qc_name(var_name: str, qc_name: str) -> str:
    return qc_name.removeprefix(f"{var_name}_qc_")


def build_qc_issue_index(data_root: Path | str, mapping: dict[str, Iterable[str]]) -> list[dict[str, object]]:
    """Scan QC NetCDF files and return per-file/variable/test flag counts."""
    rows: list[dict[str, object]] = []
    for cruise, files in discover_qc_files(data_root).items():
        for nc_path in files:
            try:
                with xr.open_dataset(nc_path, decode_cf=False, mask_and_scale=True) as ds:
                    for variable in discover_variables(ds, mapping):
                        for test in discover_tests_for_variable(ds, variable.value):
                            counts = _flag_count_map(ds[test.qc_name].values)
                            rows.append(
                                {
                                    "cruise": cruise,
                                    "file": nc_path.name,
                                    "path": str(nc_path),
                                    "variable": variable.value,
                                    "test": _test_value_from_qc_name(variable.value, test.qc_name),
                                    "test_label": test.label,
                                    "qc_name": test.qc_name,
                                    **counts,
                                }
                            )
            except Exception:
                continue
    return rows


def write_qc_issue_index(
    path: Path | str,
    rows: list[dict[str, object]],
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Write issue index JSON and return the payload."""
    out_path = Path(path)
    payload = {
        "metadata": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "row_count": len(rows),
            **(metadata or {}),
        },
        "rows": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_qc_issue_index(path: Path | str) -> dict[str, object]:
    """Load a cached issue index payload, or return an empty missing-cache payload."""
    in_path = Path(path)
    if not in_path.is_file():
        return {"metadata": {"status": "missing", "path": str(in_path)}, "rows": []}
    try:
        payload = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"metadata": {"status": "invalid", "path": str(in_path)}, "rows": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return {"metadata": {"status": "invalid", "path": str(in_path)}, "rows": []}
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.setdefault("path", str(in_path))
    return {"metadata": metadata, "rows": payload["rows"]}


def filter_issue_rows(
    rows: Iterable[dict[str, object]],
    test: str | None = "ALL",
    variable: str | None = "ALL",
    flags: Iterable[str] | None = None,
) -> list[dict[str, object]]:
    """Filter cached issue-index rows for display."""
    selected_flags = {str(flag).lower() for flag in (flags or ISSUE_FLAG_OPTIONS)}
    out: list[dict[str, object]] = []
    for row in rows:
        if test and test != "ALL" and row.get("test") != test:
            continue
        if variable and variable != "ALL" and row.get("variable") != variable:
            continue
        if selected_flags and not any(int(row.get(flag, 0) or 0) > 0 for flag in selected_flags):
            continue
        out.append(row)
    return out


def issue_row_selection(row: Mapping[str, object]) -> dict[str, str]:
    """Return the main-plot selection fields encoded by an issue-index row."""
    return {
        "path": str(row.get("path") or ""),
        "variable": str(row.get("variable") or ""),
        "qc_name": str(row.get("qc_name") or ""),
    }


def issue_row_key(row: Mapping[str, object]) -> str:
    """Return a stable key for one issue row in the sidebar navigator."""
    selection = issue_row_selection(row)
    return json.dumps(
        {
            "path": selection["path"],
            "variable": selection["variable"],
            "qc_name": selection["qc_name"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def issue_row_from_key(rows: Iterable[dict[str, object]] | None, key: str | None) -> dict[str, object] | None:
    """Find an issue row by the sidebar navigator key."""
    if not rows or not key:
        return None
    for row in rows:
        if isinstance(row, dict) and issue_row_key(row) == key:
            return row
    return None


def adjacent_issue_key(rows: Iterable[dict[str, object]], current_key: str | None, direction: int) -> str | None:
    """Return the previous/next issue key within a filtered issue list."""
    keys = [issue_row_key(row) for row in rows]
    if not keys:
        return None
    if current_key not in keys:
        return keys[0]
    idx = keys.index(str(current_key))
    next_idx = idx + direction
    if 0 <= next_idx < len(keys):
        return keys[next_idx]
    return keys[idx]


def _issue_dropdown_options(rows: Iterable[dict[str, object]], key: str, label_key: str | None = None) -> list[dict[str, str]]:
    seen: set[str] = set()
    options = [{"label": "All", "value": "ALL"}]
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in seen:
            continue
        seen.add(value)
        label = str(row.get(label_key or key) or value)
        options.append({"label": label, "value": value})
    return options


def _issue_filter_dropdown_state(
    rows: list[dict[str, object]],
    current_test: str | None,
    current_variable: str | None,
    triggered_id: str = "",
) -> tuple[list[dict[str, str]], str, list[dict[str, str]], str]:
    """Return mutually constrained issue test/variable dropdown state."""
    all_test_options = _issue_dropdown_options(rows, "test", "test_label")
    all_variable_options = _issue_dropdown_options(rows, "variable")
    all_test_values = {item["value"] for item in all_test_options}
    all_variable_values = {item["value"] for item in all_variable_options}
    test_value = current_test if current_test in all_test_values else "ALL"
    variable_value = current_variable if current_variable in all_variable_values else "ALL"

    if triggered_id == "issue-variable-dropdown":
        test_rows = rows if variable_value == "ALL" else [row for row in rows if row.get("variable") == variable_value]
        test_options = _issue_dropdown_options(test_rows, "test", "test_label")
        test_values = {item["value"] for item in test_options}
        test_value = test_value if test_value in test_values else "ALL"

        variable_rows = rows if test_value == "ALL" else [row for row in rows if row.get("test") == test_value]
        variable_options = _issue_dropdown_options(variable_rows, "variable")
        variable_values = {item["value"] for item in variable_options}
        variable_value = variable_value if variable_value in variable_values else "ALL"
        return test_options, test_value, variable_options, variable_value

    variable_rows = rows if test_value == "ALL" else [row for row in rows if row.get("test") == test_value]
    variable_options = _issue_dropdown_options(variable_rows, "variable")
    variable_values = {item["value"] for item in variable_options}
    variable_value = variable_value if variable_value in variable_values else "ALL"

    test_rows = rows if variable_value == "ALL" else [row for row in rows if row.get("variable") == variable_value]
    test_options = _issue_dropdown_options(test_rows, "test", "test_label")
    test_values = {item["value"] for item in test_options}
    test_value = test_value if test_value in test_values else "ALL"
    return test_options, test_value, variable_options, variable_value


def _issue_cruise_options(rows: Iterable[dict[str, object]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    options: list[dict[str, str]] = []
    for row in rows:
        cruise = str(row.get("cruise") or "")
        if not cruise or cruise in seen:
            continue
        seen.add(cruise)
        options.append({"label": cruise, "value": cruise})
    return options


def _issue_file_options(rows: Iterable[dict[str, object]], include_cruise: bool = True) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for row in rows:
        suspect = int(row.get("suspect", 0) or 0)
        fail = int(row.get("fail", 0) or 0)
        total = int(row.get("total", 0) or 0)
        label = f"{row.get('file', '')} - S:{suspect} F:{fail} / {total}"
        cruise = str(row.get("cruise") or "")
        if include_cruise and cruise:
            label = f"{cruise} / {label}"
        options.append({"label": label, "value": issue_row_key(row)})
    return options


def _issue_status(payload: dict[str, object], displayed_count: int | None = None) -> str:
    metadata = payload.get("metadata") if isinstance(payload, dict) else {}
    rows = payload.get("rows") if isinstance(payload, dict) else []
    if not isinstance(metadata, dict):
        metadata = {}
    if not rows:
        status = metadata.get("status")
        if status == "missing":
            return "No index cache found. Click Re-index."
        if status == "invalid":
            return "Index cache is invalid. Click Re-index."
    total = len(rows) if isinstance(rows, list) else 0
    created = metadata.get("created_at") or "unknown time"
    file_count = metadata.get("file_count")
    shown = f"{displayed_count} shown / " if displayed_count is not None else ""
    file_part = f", {file_count} files" if file_count is not None else ""
    return f"{shown}{total} indexed rows{file_part}. Cache: {created}."


def _broadcast_optional(source: xr.DataArray | None, target: xr.DataArray) -> np.ndarray | None:
    if source is None:
        return None
    src = np.asarray(source)
    if src.shape == target.shape:
        return src
    try:
        return np.broadcast_to(src, target.shape)
    except ValueError:
        pass
    if src.ndim == 1:
        for axis, size in enumerate(target.shape):
            if src.size == size:
                shape = [1] * len(target.shape)
                shape[axis] = src.size
                try:
                    return np.broadcast_to(src.reshape(shape), target.shape)
                except ValueError:
                    return None
    return None


def _depth_for_variable(ds: xr.Dataset, data_var: xr.DataArray, profile: DatasetProfile) -> np.ndarray | None:
    depth = get_coord_for_var(ds, data_var, profile.metadata.depth)
    return _broadcast_optional(depth, data_var)


def _time_for_variable(ds: xr.Dataset, data_var: xr.DataArray, profile: DatasetProfile) -> xr.DataArray | None:
    return get_coord_for_var(ds, data_var, profile.metadata.time)


def _is_climatology_qc(qc_name: str) -> bool:
    return qc_name.endswith("_qc_climatology")


def _is_location_qc(qc_name: str) -> bool:
    return qc_name.endswith("_qc_location")


def _first_finite(values: np.ndarray | None) -> float | None:
    if values is None:
        return None
    arr = np.asarray(values, dtype=float).ravel()
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return None
    return float(finite[0])


def _location_info_for_file(
    ds: xr.Dataset,
    data_var: xr.DataArray,
    profile: DatasetProfile,
) -> dict[str, object] | None:
    lon, lat = get_lon_lat(ds, profile.metadata)
    lon_values = _broadcast_optional(lon, data_var) if lon is not None else None
    lat_values = _broadcast_optional(lat, data_var) if lat is not None else None
    actual_lon = _first_finite(lon_values if lon_values is not None else (np.asarray(lon) if lon is not None else None))
    actual_lat = _first_finite(lat_values if lat_values is not None else (np.asarray(lat) if lat is not None else None))

    station_id = get_station_id(ds, profile.metadata)
    expected = resolve_coords_by_station_id(
        station_id,
        station_csv=resolve_config_path("station_coords", profile),
    )
    if actual_lon is None and actual_lat is None and expected is None:
        return None

    expected_lat = float(expected[0]) if expected is not None else None
    expected_lon = float(expected[1]) if expected is not None else None
    tolerance = None
    try:
        tolerance = float(load_location_config(resolve_config_path("location_config", profile)).get("tolerance"))
    except Exception:
        tolerance = None

    delta_lat = actual_lat - expected_lat if actual_lat is not None and expected_lat is not None else None
    delta_lon = actual_lon - expected_lon if actual_lon is not None and expected_lon is not None else None
    return {
        "station": station_id or "unknown",
        "actual_lat": actual_lat,
        "actual_lon": actual_lon,
        "expected_lat": expected_lat,
        "expected_lon": expected_lon,
        "delta_lat": delta_lat,
        "delta_lon": delta_lon,
        "tolerance": tolerance,
    }


def _month_from_datetime_like(value: object) -> int | None:
    try:
        if isinstance(value, np.datetime64):
            if np.isnat(value):
                return None
            text = np.datetime_as_string(value, unit="D")
            return int(text[5:7])
        if isinstance(value, datetime):
            return int(value.month)
    except (TypeError, ValueError):
        return None

    text = str(value).strip()
    if len(text) >= 7 and text[4] == "-" and text[5:7].isdigit():
        return int(text[5:7])
    return None


def _base_datetime_from_units(units: str) -> datetime | None:
    text = units.strip()
    if " since " not in text:
        return None
    _, base = text.split(" since ", 1)
    base = base.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(base)
    except ValueError:
        try:
            return datetime.fromisoformat(base.split()[0])
        except ValueError:
            return None


def _months_for_time(time: xr.DataArray | np.ndarray | None, target: xr.DataArray) -> np.ndarray | None:
    if time is None:
        return None
    raw = np.asarray(time)
    values = _broadcast_optional(time if isinstance(time, xr.DataArray) else xr.DataArray(raw), target)
    if values is None:
        return None

    months = np.full(target.shape, np.nan, dtype=float)
    units = str(getattr(time, "attrs", {}).get("units") or "") if isinstance(time, xr.DataArray) else ""
    base = _base_datetime_from_units(units)
    unit_name = units.split(" since ", 1)[0].strip().lower() if " since " in units else ""

    for idx, value in np.ndenumerate(values):
        month = _month_from_datetime_like(value)
        if month is None and base is not None:
            try:
                offset = float(value)
            except (TypeError, ValueError):
                offset = np.nan
            if np.isfinite(offset):
                if unit_name.startswith("day"):
                    dt = base + timedelta(days=offset)
                elif unit_name.startswith("hour"):
                    dt = base + timedelta(hours=offset)
                elif unit_name.startswith("minute"):
                    dt = base + timedelta(minutes=offset)
                elif unit_name.startswith("millisecond"):
                    dt = base + timedelta(milliseconds=offset)
                else:
                    dt = base + timedelta(seconds=offset)
                month = int(dt.astimezone(timezone.utc).month) if dt.tzinfo else int(dt.month)
        if month is not None:
            months[idx] = month
    return months


def _tspan_contains(tspan: Iterable[object] | None, month: float) -> bool:
    if tspan is None or not np.isfinite(month):
        return False
    parts = list(tspan)
    if len(parts) != 2:
        return False
    start, end = int(parts[0]), int(parts[1])
    m = int(month)
    if start <= end:
        return start <= m <= end
    return m >= start or m <= end


def _zspan_contains(zspan: Iterable[object] | None, depth: float) -> bool:
    if zspan is None or not np.isfinite(depth):
        return False
    parts = list(zspan)
    if len(parts) != 2:
        return False
    low, high = float(parts[0]), float(parts[1])
    return low <= float(depth) <= high


def _climatology_limits_for_samples(
    config: Iterable[Mapping[str, object]] | None,
    depth: np.ndarray | None,
    months: np.ndarray | None,
) -> dict[str, np.ndarray] | None:
    if config is None or depth is None or months is None:
        return None

    depth_arr = np.asarray(depth, dtype=float).ravel()
    month_arr = np.asarray(months, dtype=float).ravel()
    lower = np.full(depth_arr.shape, np.nan, dtype=float)
    upper = np.full(depth_arr.shape, np.nan, dtype=float)

    rows = list(config)
    for i, (sample_depth, sample_month) in enumerate(zip(depth_arr, month_arr, strict=False)):
        for row in rows:
            vspan = row.get("vspan")
            try:
                vparts = list(vspan) if vspan is not None else []
            except TypeError:
                continue
            if len(vparts) != 2:
                continue
            if _zspan_contains(row.get("zspan"), sample_depth) and _tspan_contains(row.get("tspan"), sample_month):
                lower[i] = float(vparts[0])
                upper[i] = float(vparts[1])
                break

    if np.all(np.isnan(lower)) and np.all(np.isnan(upper)):
        return None
    return {"lower": lower, "upper": upper}


def _resolve_climatology_limits(
    ds: xr.Dataset,
    data_var: xr.DataArray,
    var_name: str,
    depth_values: np.ndarray | None,
    profile: DatasetProfile,
) -> dict[str, np.ndarray] | None:
    clim_config = get_climatology_config_for_file(
        ds,
        limits_json_path=resolve_config_path("station_climatology", profile),
        classification_json_path=resolve_config_path("station_depth_classification", profile),
        metadata=profile.metadata,
    )
    if not clim_config:
        return None
    var_config = clim_config.get(var_name)
    if not var_config:
        return None

    time = _time_for_variable(ds, data_var, profile)
    months = _months_for_time(time, data_var)
    return _climatology_limits_for_samples(var_config, depth_values, months)


def load_plot_data(
    nc_path: Path | str,
    variable: str,
    qc_name: str,
    profile: DatasetProfile | None = None,
) -> dict[str, object]:
    """Load flattened arrays and metadata for one variable/test selection."""
    prof = profile or default_profile()
    path = Path(nc_path)
    with xr.open_dataset(path, decode_cf=False, mask_and_scale=True) as ds:
        if variable not in ds:
            raise KeyError(f"Variable not found: {variable}")
        if qc_name not in ds:
            raise KeyError(f"QC variable not found: {qc_name}")

        data_var = ds[variable]
        values = np.asarray(data_var.values).ravel()
        flags = np.asarray(ds[qc_name].values, dtype=np.int16).ravel()
        if values.size != flags.size:
            raise ValueError(f"{variable} and {qc_name} do not have matching shapes")

        depth = _depth_for_variable(ds, data_var, prof)
        depth_values = np.asarray(depth).ravel() if depth is not None else None
        if depth_values is not None and depth_values.size != values.size:
            depth_values = None
        climatology_limits = None
        climatology_limit_status = ""
        if _is_climatology_qc(qc_name):
            climatology_limits = _resolve_climatology_limits(ds, data_var, variable, depth_values, prof)
            if climatology_limits is None:
                climatology_limit_status = "Climatology limits are unavailable for this file, variable, station, depth, or time."
        location_info = _location_info_for_file(ds, data_var, prof) if _is_location_qc(qc_name) else None

        return {
            "path": str(path),
            "file_name": path.name,
            "cruise": path.parent.name,
            "title": ds.attrs.get("title") or path.name,
            "station": get_scalar_var(ds, prof.metadata.station) or "unknown",
            "cruise_id": get_scalar_var(ds, prof.metadata.cruise_id) or path.parent.name,
            "variable": variable,
            "variable_label": variable,
            "variable_units": str(data_var.attrs.get("units") or "").strip(),
            "qc_name": qc_name,
            "test_label": _test_label(variable, qc_name, ds[qc_name]),
            "index": np.arange(values.size),
            "values": values,
            "depth": depth_values,
            "flags": flags,
            "climatology_limits": climatology_limits,
            "climatology_limit_status": climatology_limit_status,
            "location_info": location_info,
            "summary": flag_summary(flags),
        }


def _dropdown_options(items: Iterable[object]) -> list[dict[str, str]]:
    return [{"label": item.label, "value": item.value} for item in items]


def _file_options(files: Iterable[Path]) -> list[dict[str, str]]:
    return [{"label": path.name, "value": str(path)} for path in files]


def _format_coord(lat: object, lon: object) -> str:
    if lat is None or lon is None:
        return "unavailable"
    try:
        return f"{float(lat):.6f}, {float(lon):.6f}"
    except (TypeError, ValueError):
        return "unavailable"


def adjacent_file(files: Iterable[Path], current: Path | str | None, direction: int) -> str | None:
    """Return the previous/next file path string within a cruise file list."""
    paths = [str(path) for path in files]
    if not paths:
        return None
    if current not in paths:
        return paths[0]
    idx = paths.index(str(current))
    next_idx = idx + direction
    if 0 <= next_idx < len(paths):
        return paths[next_idx]
    return paths[idx]


def _empty_figure(message: str):
    import plotly.graph_objects as go

    fig = go.Figure()
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    fig.update_layout(
        template="plotly_white",
        autosize=True,
        height=850,
        margin={"l": 56, "r": 24, "t": 48, "b": 48},
    )
    return fig


def _marker_style(flag_name: str) -> dict[str, object]:
    if flag_name == "FAIL":
        return {
            "size": 12,
            "symbol": "x",
            "color": FLAG_COLORS[flag_name],
            "opacity": 0.98,
            "line": {"width": 3, "color": FLAG_COLORS[flag_name]},
        }
    if flag_name == "SUSPECT":
        return {
            "size": 11,
            "symbol": "x",
            "color": FLAG_COLORS[flag_name],
            "opacity": 0.95,
            "line": {"width": 2, "color": FLAG_COLORS[flag_name]},
        }
    return {
        "size": 6,
        "symbol": "circle",
        "color": FLAG_COLORS[flag_name],
        "opacity": 0.78,
    }


def _make_figure(payload: dict[str, object], visible_flags: list[str] | None):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    visible = set(visible_flags or FLAG_ORDER)
    idx = np.asarray(payload["index"])
    values = np.asarray(payload["values"])
    depth = payload["depth"]
    flags = np.asarray(payload["flags"])
    climatology_limits = payload.get("climatology_limits")
    units = payload["variable_units"]
    variable_label = payload["variable"]
    value_title = f"{variable_label} ({units})" if units else str(variable_label)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        subplot_titles=("Depth by sample index", "Variable value by sample index"),
    )

    if depth is not None:
        fig.add_trace(
            go.Scatter(
                x=idx,
                y=np.asarray(depth),
                mode="lines",
                line={"color": "#8aa0b2", "width": 1.2},
                name="Depth path",
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    fig.add_trace(
        go.Scattergl(
            x=idx,
            y=values,
            mode="lines",
            line={"color": "#5f6f5b", "width": 1.2},
            name="Value path",
            hoverinfo="skip",
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    if climatology_limits:
        lower = np.asarray(climatology_limits["lower"], dtype=float)
        upper = np.asarray(climatology_limits["upper"], dtype=float)
        fig.add_trace(
            go.Scatter(
                x=idx,
                y=lower,
                mode="lines",
                line={"color": "#7c3aed", "width": 1.6, "dash": "dash"},
                name="Climatology lower",
                hovertemplate="index=%{x}<br>lower limit=%{y}<extra>Climatology lower</extra>",
                connectgaps=False,
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=idx,
                y=upper,
                mode="lines",
                line={"color": "#7c3aed", "width": 1.6, "dash": "dot"},
                name="Climatology upper",
                hovertemplate="index=%{x}<br>upper limit=%{y}<extra>Climatology upper</extra>",
                connectgaps=False,
            ),
            row=2,
            col=1,
        )

    for flag_name in FLAG_ORDER:
        if flag_name not in visible:
            continue
        flag_value = QC_FLAGS[flag_name]
        mask = flags == flag_value
        if not np.any(mask):
            continue
        label = FLAG_LABELS[flag_name]
        if depth is not None:
            depth_values = np.asarray(depth)
            fig.add_trace(
                go.Scatter(
                    x=idx[mask],
                    y=depth_values[mask],
                    mode="markers",
                    marker=_marker_style(flag_name),
                    name=label,
                    legendgroup=flag_name,
                    showlegend=False,
                    hovertemplate="index=%{x}<br>depth=%{y}<extra>" + label + "</extra>",
                ),
                row=1,
                col=1,
            )
        fig.add_trace(
            go.Scatter(
                x=idx[mask],
                y=values[mask],
                mode="markers",
                marker=_marker_style(flag_name),
                name=label,
                legendgroup=flag_name,
                hovertemplate="index=%{x}<br>value=%{y}<extra>" + label + "</extra>",
            ),
            row=2,
            col=1,
        )

    if depth is None:
        fig.add_annotation(
            text="Depth unavailable for this variable",
            x=0.5,
            y=0.78,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"color": "#64748b", "size": 14},
        )
    else:
        fig.update_yaxes(title_text="depth", autorange="reversed", row=1, col=1)

    fig.update_yaxes(title_text=value_title, row=2, col=1)
    fig.update_xaxes(title_text="sample index", row=2, col=1)
    fig.update_layout(
        template="plotly_white",
        autosize=True,
        height=900,
        margin={"l": 70, "r": 28, "t": 70, "b": 56},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.04, "xanchor": "left", "x": 0},
        paper_bgcolor="#f7f8f5",
        plot_bgcolor="#ffffff",
    )
    return fig


def create_app(data_root: Path | str | None = None, profile: DatasetProfile | None = None):
    """Create the Dash QC viewer app."""
    from dash import Dash, Input, Output, State, callback_context, dash_table, dcc, html, no_update

    prof = profile or default_profile()
    root = Path(data_root) if data_root is not None else default_viz_data_root(prof)
    mapping = load_mapping(resolve_config_path("variable_mapping", prof))
    mapped_names = flatten_mapping(mapping)
    issue_index_path = default_issue_index_path()
    issue_index_payload = load_qc_issue_index(issue_index_path)
    files_by_cruise = discover_qc_files(root)
    cruise_options = [{"label": cruise, "value": cruise} for cruise in sorted(files_by_cruise)]
    initial_cruise = cruise_options[0]["value"] if cruise_options else None
    initial_file = str(files_by_cruise[initial_cruise][0]) if initial_cruise else None

    app = Dash(__name__, title="CTD QC Viewer")

    app.layout = html.Div(
        className="app-shell",
        children=[
            dcc.Store(id="data-root", data=str(root)),
            dcc.Store(id="issue-index-store", data=issue_index_payload),
            dcc.Store(id="filtered-issue-store", data=[]),
            html.Header(
                className="topbar",
                children=[
                    html.Div(
                        [
                            html.P("SFER CTD", className="eyebrow"),
                            html.H1("QC result viewer"),
                        ]
                    ),
                    html.Div(
                        [
                            html.Span("Data root", className="meta-label"),
                            html.Code(str(root), className="root-path"),
                        ],
                        className="root-chip",
                    ),
                ],
            ),
            html.Main(
                className="workspace",
                children=[
                    html.Aside(
                        className="controls",
                        children=[
                            dcc.Tabs(
                                id="view-mode",
                                value="file",
                                className="mode-tabs",
                                children=[
                                    dcc.Tab(label="File by file", value="file", className="mode-tab", selected_className="mode-tab-selected"),
                                    dcc.Tab(label="Issue by issue", value="issue", className="mode-tab", selected_className="mode-tab-selected"),
                                ],
                            ),
                            html.Div(
                                id="file-browser-panel",
                                className="mode-panel",
                                children=[
                                    html.Label("Cruise", htmlFor="cruise-dropdown"),
                                    dcc.Dropdown(id="cruise-dropdown", options=cruise_options, value=initial_cruise, clearable=False),
                                    html.Label("Files in cruise", htmlFor="file-radio"),
                                    html.Div(
                                        dcc.RadioItems(
                                            id="file-radio",
                                            options=[],
                                            value=None,
                                            labelStyle={
                                                "display": "block",
                                                "margin": "4px 0",
                                                "fontSize": "12px",
                                                "wordBreak": "break-all",
                                            },
                                            inputStyle={"marginRight": "6px"},
                                        ),
                                        className="file-list",
                                    ),
                                    html.Div(
                                        [
                                            html.Button("Prev", id="btn-prev-file", n_clicks=0, className="nav-button"),
                                            html.Button("Next", id="btn-next-file", n_clicks=0, className="nav-button"),
                                        ],
                                        className="file-nav",
                                    ),
                                ],
                            ),
                            html.Div(
                                id="issue-browser-panel",
                                className="mode-panel",
                                style={"display": "none"},
                                children=[
                                    html.H3("Issue Finder", className="sidebar-heading"),
                                    html.Label("Issue test", htmlFor="issue-test-dropdown"),
                                    dcc.Dropdown(id="issue-test-dropdown", clearable=False),
                                    html.Label("Issue variable", htmlFor="issue-variable-dropdown"),
                                    dcc.Dropdown(id="issue-variable-dropdown", clearable=False),
                                    html.Label("Issue flags", htmlFor="issue-flag-checklist"),
                                    dcc.Checklist(
                                        id="issue-flag-checklist",
                                        options=[{"label": FLAG_LABELS[name], "value": name} for name in ISSUE_FLAG_OPTIONS],
                                        value=list(ISSUE_FLAG_OPTIONS),
                                        className="flag-list",
                                    ),
                                    html.Button("Re-index", id="btn-reindex", n_clicks=0, className="nav-button reindex-button"),
                                    html.Div(id="issue-cache-status", className="status-message"),
                                    html.Label("Issue cruise", htmlFor="issue-cruise-dropdown"),
                                    dcc.Dropdown(id="issue-cruise-dropdown", clearable=False),
                                    html.Label("Issue files", htmlFor="issue-file-radio"),
                                    html.Div(
                                        dcc.RadioItems(
                                            id="issue-file-radio",
                                            options=[],
                                            value=None,
                                            labelStyle={
                                                "display": "block",
                                                "margin": "5px 0",
                                                "fontSize": "12px",
                                                "wordBreak": "break-word",
                                                "lineHeight": "1.35",
                                            },
                                            inputStyle={"marginRight": "6px"},
                                        ),
                                        className="file-list issue-file-list",
                                    ),
                                    html.Div(
                                        [
                                            html.Button("Prev issue", id="btn-prev-issue", n_clicks=0, className="nav-button"),
                                            html.Button("Next issue", id="btn-next-issue", n_clicks=0, className="nav-button"),
                                        ],
                                        className="file-nav",
                                    ),
                                ],
                            ),
                            html.Div(
                                id="graph-controls-panel",
                                className="mode-panel",
                                children=[
                                    html.Label("Variable", htmlFor="variable-dropdown"),
                                    dcc.Dropdown(id="variable-dropdown", clearable=False),
                                    html.Label("QC test", htmlFor="test-dropdown"),
                                    dcc.Dropdown(id="test-dropdown", clearable=False),
                                    html.Label("Visible flags", htmlFor="flag-checklist"),
                                    dcc.Checklist(
                                        id="flag-checklist",
                                        options=[{"label": FLAG_LABELS[name], "value": name} for name in FLAG_ORDER],
                                        value=list(FLAG_ORDER),
                                        className="flag-list",
                                    ),
                                ],
                            ),
                            html.Div(id="status-message", className="status-message"),
                        ],
                    ),
                    html.Section(
                        className="results",
                        children=[
                            html.Div(id="file-metadata", className="metadata-strip"),
                            dcc.Graph(id="qc-graph", config={"displaylogo": False, "responsive": True}),
                            dash_table.DataTable(
                                id="summary-table",
                                columns=[
                                    {"name": "Flag", "id": "flag"},
                                    {"name": "Value", "id": "value"},
                                    {"name": "Count", "id": "count"},
                                    {"name": "Percent", "id": "percent", "type": "numeric", "format": {"specifier": ".2f"}},
                                ],
                                data=[],
                                style_as_list_view=True,
                                style_cell={"padding": "10px 12px", "fontFamily": "system-ui", "fontSize": "14px"},
                                style_header={"backgroundColor": "#edf1ea", "fontWeight": "700"},
                                style_data_conditional=[
                                    {
                                        "if": {"filter_query": f"{{flag}} = '{name}'"},
                                        "borderLeft": f"5px solid {FLAG_COLORS[name]}",
                                    }
                                    for name in FLAG_ORDER
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    app.index_string = """
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            :root {
                --bg: #f7f8f5;
                --ink: #182016;
                --muted: #667263;
                --panel: #ffffff;
                --line: #d9dfd3;
                --accent: #315c47;
            }
            * { box-sizing: border-box; }
            body {
                margin: 0;
                background: var(--bg);
                color: var(--ink);
                font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            }
            .app-shell { min-height: 100vh; }
            .topbar {
                display: flex;
                align-items: flex-end;
                justify-content: space-between;
                gap: 24px;
                padding: 28px 34px 20px;
                border-bottom: 1px solid var(--line);
            }
            .eyebrow {
                margin: 0 0 6px;
                color: var(--accent);
                font-size: 12px;
                font-weight: 800;
                letter-spacing: .08em;
                text-transform: uppercase;
            }
            h1 { margin: 0; font-size: 34px; line-height: 1.08; letter-spacing: 0; }
            .root-chip {
                max-width: min(58vw, 780px);
                display: grid;
                gap: 4px;
                justify-items: end;
                color: var(--muted);
                font-size: 13px;
            }
            .meta-label { font-weight: 700; }
            .root-path {
                color: #263322;
                background: #e7ece2;
                border: 1px solid #d5ddcf;
                border-radius: 6px;
                padding: 7px 9px;
                white-space: normal;
                overflow-wrap: anywhere;
            }
            .workspace {
                display: grid;
                grid-template-columns: minmax(260px, 330px) minmax(720px, 1fr);
                gap: 24px;
                padding: 24px 34px 36px;
                width: 100%;
            }
            .controls {
                align-self: start;
                display: grid;
                gap: 10px;
                padding: 18px;
                background: var(--panel);
                border: 1px solid var(--line);
                border-radius: 8px;
            }
            .controls label {
                margin-top: 8px;
                color: #33402f;
                font-size: 13px;
                font-weight: 750;
            }
            .mode-tabs {
                border: 1px solid var(--line);
                border-radius: 8px;
                overflow: hidden;
                margin-bottom: 4px;
            }
            .mode-tab {
                padding: 10px 8px !important;
                background: #f3f6ef !important;
                border: 0 !important;
                border-right: 1px solid var(--line) !important;
                color: #455240 !important;
                font-size: 13px;
                font-weight: 750;
            }
            .mode-tab-selected {
                background: #dfe9d9 !important;
                color: #182016 !important;
                border-top: 3px solid var(--accent) !important;
            }
            .mode-panel {
                display: grid;
                gap: 10px;
            }
            .sidebar-rule {
                width: 100%;
                border: 0;
                border-top: 1px solid var(--line);
                margin: 8px 0 4px;
            }
            .sidebar-heading,
            .results-heading {
                margin: 4px 0 0;
                color: #253520;
                font-size: 16px;
                line-height: 1.2;
            }
            .flag-list label {
                display: block;
                margin: 8px 0;
                color: #33402f;
                font-size: 14px;
            }
            .file-list {
                max-height: 34vh;
                overflow-y: auto;
                margin-top: -2px;
                padding: 7px 8px;
                border: 1px solid var(--line);
                border-radius: 6px;
                background: #fbfcfa;
            }
            .issue-file-list {
                max-height: 28vh;
            }
            .file-nav {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
            }
            .nav-button {
                min-height: 34px;
                border: 1px solid #cbd6c5;
                border-radius: 6px;
                background: #edf3e9;
                color: #253520;
                font-weight: 750;
                cursor: pointer;
            }
            .nav-button:hover { background: #e1ebdc; }
            .reindex-button {
                width: 100%;
                margin-top: 4px;
            }
            .status-message {
                min-height: 20px;
                color: #8a3b12;
                font-size: 13px;
                line-height: 1.45;
            }
            .results {
                min-width: 0;
                display: grid;
                gap: 16px;
                width: 100%;
            }
            .metadata-strip {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 10px;
            }
            .metadata-item {
                background: #ffffff;
                border: 1px solid var(--line);
                border-radius: 8px;
                padding: 12px 14px;
                min-width: 0;
            }
            .metadata-item span {
                display: block;
                color: var(--muted);
                font-size: 12px;
                font-weight: 750;
            }
            .metadata-item strong {
                display: block;
                margin-top: 4px;
                overflow-wrap: anywhere;
                font-size: 14px;
                line-height: 1.35;
            }
            #qc-graph {
                overflow: hidden;
                border: 1px solid var(--line);
                border-radius: 8px;
                background: #ffffff;
                width: 100%;
                min-height: 850px;
            }
            #qc-graph .js-plotly-plot,
            #qc-graph .plot-container,
            #qc-graph .svg-container {
                width: 100% !important;
            }
            @media (max-width: 980px) {
                .topbar { display: grid; align-items: start; }
                .root-chip { max-width: 100%; justify-items: start; }
                .workspace { grid-template-columns: 1fr; padding: 18px; }
                .metadata-strip { grid-template-columns: 1fr 1fr; }
            }
            @media (max-width: 620px) {
                h1 { font-size: 28px; }
                .metadata-strip { grid-template-columns: 1fr; }
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""

    @app.callback(
        Output("issue-index-store", "data"),
        Input("btn-reindex", "n_clicks"),
        prevent_initial_call=True,
    )
    def rebuild_issue_index(_n_clicks: int):
        rows = build_qc_issue_index(root, mapping)
        file_count = sum(len(files) for files in files_by_cruise.values())
        return write_qc_issue_index(
            issue_index_path,
            rows,
            {
                "data_root": str(root),
                "index_path": str(issue_index_path),
                "file_count": file_count,
            },
        )

    @app.callback(
        Output("issue-test-dropdown", "options"),
        Output("issue-test-dropdown", "value"),
        Output("issue-variable-dropdown", "options"),
        Output("issue-variable-dropdown", "value"),
        Input("issue-index-store", "data"),
        Input("issue-test-dropdown", "value"),
        Input("issue-variable-dropdown", "value"),
    )
    def update_issue_filter_options(payload: dict | None, current_test: str | None, current_variable: str | None):
        rows = (payload or {}).get("rows") or []
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""
        return _issue_filter_dropdown_state(rows, current_test, current_variable, triggered)

    @app.callback(
        Output("file-browser-panel", "style"),
        Output("issue-browser-panel", "style"),
        Output("graph-controls-panel", "style"),
        Input("view-mode", "value"),
    )
    def toggle_browser_mode(mode: str | None):
        hidden = {"display": "none"}
        shown = {}
        if mode == "issue":
            return hidden, shown, hidden
        return shown, hidden, shown

    @app.callback(
        Output("filtered-issue-store", "data"),
        Output("issue-cruise-dropdown", "options"),
        Output("issue-cruise-dropdown", "value"),
        Output("issue-cache-status", "children"),
        Input("issue-index-store", "data"),
        Input("issue-test-dropdown", "value"),
        Input("issue-variable-dropdown", "value"),
        Input("issue-flag-checklist", "value"),
        State("issue-cruise-dropdown", "value"),
    )
    def update_issue_cruises(
        payload: dict | None,
        issue_test: str | None,
        issue_variable: str | None,
        issue_flags: list[str] | None,
        current_cruise: str | None,
    ):
        payload = payload or {"metadata": {"status": "missing", "path": str(issue_index_path)}, "rows": []}
        rows = payload.get("rows") or []
        filtered = filter_issue_rows(rows, issue_test or "ALL", issue_variable or "ALL", issue_flags or [])
        cruise_options = _issue_cruise_options(filtered)
        cruise_values = {item["value"] for item in cruise_options}
        cruise_value = current_cruise if current_cruise in cruise_values else (cruise_options[0]["value"] if cruise_options else None)
        return filtered, cruise_options, cruise_value, _issue_status(payload, len(filtered))

    @app.callback(
        Output("issue-file-radio", "options"),
        Output("issue-file-radio", "value"),
        Input("filtered-issue-store", "data"),
        Input("issue-cruise-dropdown", "value"),
        Input("btn-prev-issue", "n_clicks"),
        Input("btn-next-issue", "n_clicks"),
        State("issue-file-radio", "value"),
    )
    def update_issue_files(
        issue_rows: list[dict] | None,
        issue_cruise: str | None,
        _prev_clicks: int,
        _next_clicks: int,
        current_issue_key: str | None,
    ):
        rows = issue_rows or []
        cruise_rows = [row for row in rows if not issue_cruise or row.get("cruise") == issue_cruise]
        options = _issue_file_options(cruise_rows, include_cruise=False)
        values = {item["value"] for item in options}
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""

        if triggered == "btn-prev-issue":
            value = adjacent_issue_key(cruise_rows, current_issue_key, -1)
        elif triggered == "btn-next-issue":
            value = adjacent_issue_key(cruise_rows, current_issue_key, 1)
        else:
            value = current_issue_key if current_issue_key in values else (options[0]["value"] if options else None)
        return options, value

    @app.callback(
        Output("cruise-dropdown", "value"),
        Input("view-mode", "value"),
        Input("issue-file-radio", "value"),
        State("filtered-issue-store", "data"),
        State("cruise-dropdown", "value"),
    )
    def set_cruise_from_issue(
        mode: str | None,
        issue_key: str | None,
        issue_rows: list[dict] | None,
        current: str | None,
    ):
        if mode != "issue":
            return no_update
        row = issue_row_from_key(issue_rows, issue_key)
        if not row:
            return no_update
        cruise = str(row.get("cruise") or "")
        return cruise if cruise in files_by_cruise and cruise != current else no_update

    @app.callback(
        Output("file-radio", "options"),
        Output("file-radio", "value"),
        Input("cruise-dropdown", "value"),
        Input("btn-prev-file", "n_clicks"),
        Input("btn-next-file", "n_clicks"),
        Input("view-mode", "value"),
        Input("issue-file-radio", "value"),
        State("file-radio", "value"),
        State("filtered-issue-store", "data"),
    )
    def sync_file_controls(
        cruise: str | None,
        _prev_clicks: int,
        _next_clicks: int,
        mode: str | None,
        issue_key: str | None,
        current_file: str | None,
        issue_rows: list[dict] | None,
    ):
        issue_row = issue_row_from_key(issue_rows, issue_key)
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""
        if mode == "issue" and triggered in {"issue-file-radio", "view-mode"} and issue_row:
            cruise = str(issue_row.get("cruise") or cruise or "")
        if not cruise or cruise not in files_by_cruise:
            return [], None
        files = files_by_cruise[cruise]
        options = _file_options(files)
        values = {item["value"] for item in options}

        if mode == "issue" and triggered in {"issue-file-radio", "view-mode"} and issue_row and issue_row.get("path") in values:
            value = issue_row_selection(issue_row)["path"]
        elif triggered == "btn-prev-file":
            value = adjacent_file(files, current_file, -1)
        elif triggered == "btn-next-file":
            value = adjacent_file(files, current_file, 1)
        else:
            value = current_file if current_file in values else (options[0]["value"] if options else None)
        return options, value

    @app.callback(
        Output("variable-dropdown", "options"),
        Output("variable-dropdown", "value"),
        Input("file-radio", "value"),
        Input("view-mode", "value"),
        Input("issue-file-radio", "value"),
        State("variable-dropdown", "value"),
        State("filtered-issue-store", "data"),
    )
    def update_variables(
        file_path: str | None,
        mode: str | None,
        issue_key: str | None,
        current: str | None,
        issue_rows: list[dict] | None,
    ):
        issue_row = issue_row_from_key(issue_rows, issue_key)
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""
        if mode == "issue" and triggered in {"issue-file-radio", "view-mode"} and issue_row:
            selection = issue_row_selection(issue_row)
            file_path = selection["path"] or file_path
            current = selection["variable"] or current
        if not file_path:
            return [], None
        try:
            with xr.open_dataset(file_path, decode_cf=False, mask_and_scale=True) as ds:
                options = _dropdown_options(discover_variables(ds, mapping))
        except Exception:
            return [], None
        values = {item["value"] for item in options}
        value = current if current in values else (options[0]["value"] if options else None)
        return options, value

    @app.callback(
        Output("test-dropdown", "options"),
        Output("test-dropdown", "value"),
        Input("file-radio", "value"),
        Input("variable-dropdown", "value"),
        Input("view-mode", "value"),
        Input("issue-file-radio", "value"),
        State("test-dropdown", "value"),
        State("filtered-issue-store", "data"),
    )
    def update_tests(
        file_path: str | None,
        variable: str | None,
        mode: str | None,
        issue_key: str | None,
        current: str | None,
        issue_rows: list[dict] | None,
    ):
        issue_row = issue_row_from_key(issue_rows, issue_key)
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""
        if mode == "issue" and triggered in {"issue-file-radio", "view-mode"} and issue_row:
            selection = issue_row_selection(issue_row)
            file_path = selection["path"] or file_path
            variable = selection["variable"] or variable
            current = selection["qc_name"] or current
        if not file_path or not variable:
            return [], None
        try:
            with xr.open_dataset(file_path, decode_cf=False, mask_and_scale=True) as ds:
                options = _dropdown_options(discover_tests_for_variable(ds, variable))
        except Exception:
            return [], None
        values = {item["value"] for item in options}
        value = current if current in values else (options[0]["value"] if options else None)
        return options, value

    @app.callback(
        Output("qc-graph", "figure"),
        Output("summary-table", "data"),
        Output("file-metadata", "children"),
        Output("status-message", "children"),
        Input("file-radio", "value"),
        Input("variable-dropdown", "value"),
        Input("test-dropdown", "value"),
        Input("flag-checklist", "value"),
    )
    def update_view(file_path: str | None, variable: str | None, qc_name: str | None, visible_flags: list[str] | None):
        if not root.exists():
            return _empty_figure("QC data root does not exist"), [], [], f"Missing data root: {root}"
        if not files_by_cruise:
            return _empty_figure("No QC NetCDF files found"), [], [], f"No .nc files found under {root}"
        if not file_path or not variable or not qc_name:
            return _empty_figure("Choose a file, variable, and QC test"), [], [], ""

        try:
            payload = load_plot_data(file_path, variable, qc_name, prof)
        except Exception as exc:
            return _empty_figure(str(exc)), [], [], str(exc)

        if variable not in mapped_names:
            return _empty_figure(f"{variable} is not present in the variable mapping"), [], [], (
                f"{variable} is not present in {resolve_config_path('variable_mapping', prof)}"
            )

        metadata = [
            html.Div([html.Span("Cruise"), html.Strong(str(payload["cruise_id"]))], className="metadata-item"),
            html.Div([html.Span("Station"), html.Strong(str(payload["station"]))], className="metadata-item"),
            html.Div([html.Span("File"), html.Strong(str(payload["file_name"]))], className="metadata-item"),
            html.Div([html.Span("Selection"), html.Strong(f"{payload['variable']} / {payload['test_label']}")], className="metadata-item"),
        ]
        location_info = payload.get("location_info")
        if isinstance(location_info, dict):
            metadata.extend(
                [
                    html.Div(
                        [
                            html.Span("Actual location"),
                            html.Strong(_format_coord(location_info.get("actual_lat"), location_info.get("actual_lon"))),
                        ],
                        className="metadata-item",
                    ),
                    html.Div(
                        [
                            html.Span("Expected location"),
                            html.Strong(_format_coord(location_info.get("expected_lat"), location_info.get("expected_lon"))),
                        ],
                        className="metadata-item",
                    ),
                    html.Div(
                        [
                            html.Span("Location delta"),
                            html.Strong(_format_coord(location_info.get("delta_lat"), location_info.get("delta_lon"))),
                        ],
                        className="metadata-item",
                    ),
                    html.Div(
                        [
                            html.Span("Tolerance"),
                            html.Strong(
                                "unavailable"
                                if location_info.get("tolerance") is None
                                else f"{float(location_info['tolerance']):.6f} degrees"
                            ),
                        ],
                        className="metadata-item",
                    ),
                ]
            )
        return _make_figure(payload, visible_flags), payload["summary"], metadata, payload.get("climatology_limit_status", "")

    return app


def run_dashboard(
    data_root: Path | str | None = None,
    profile: DatasetProfile | None = None,
    host: str = "127.0.0.1",
    port: int = 8050,
    debug: bool = False,
) -> None:
    """Run the Dash development server."""
    app = create_app(data_root=data_root, profile=profile)
    app.run(host=host, port=port, debug=debug)
