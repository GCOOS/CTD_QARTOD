"""
Interactive Dash app for reviewing and adjusting CTD surface soak removal.

Usage:
    python soak_removal/review_app.py --input-root /path/to/netcdf --output-root /path/to/out --port 8051
    python soak_removal/review_app.py --apply-batch /any/path/batch.json --input-root ... --output-root ... --progress-json ...
"""

from __future__ import annotations

import argparse
import base64
import json
from datetime import datetime
from pathlib import Path

import dash
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import xarray as xr
from dash import Input, Output, State, dcc, html
from dash.exceptions import PreventUpdate

from nc_util import guess_scan_dim, safe_depth_1d, sanitize_encodings_for_netcdf
from soak_detection_util import (
    PRE_SOAK_DEPTH_THRESHOLD_M,
    first_index_depth_at_least,
    get_soak_removal_index,
)

MIN_PROFILE_SCANS = 50
PROFILE_TOO_SHORT_MSG = f"Profile too short (< {MIN_PROFILE_SCANS} scans)"

PROBLEMATIC_SEP = "|||"


def _is_problematic_queue_status(status: str | None) -> bool:
    return status in ("reported", "problematic")


def iter_problematic_queue(
    progress: ProgressTracker, navigator: FileNavigator
) -> list[tuple[str, str]]:
    """(folder, filename) in navigator order for reported/problematic entries."""
    out: list[tuple[str, str]] = []
    for folder in navigator.get_folders():
        for fn in navigator.get_files(folder):
            if _is_problematic_queue_status(progress.get_status(folder, fn)):
                out.append((folder, fn))
    return out


def first_remaining_problematic(
    progress: ProgressTracker, navigator: FileNavigator,
) -> tuple[str, str] | None:
    q = iter_problematic_queue(progress, navigator)
    return q[0] if q else None


def export_suspicious_jsonl_from_progress(
    progress: ProgressTracker, jsonl_path: Path,
) -> int:
    """
    Overwrite jsonl with one JSON object per line for each reported/problematic
    entry, using report_detail when present (legacy JSONL shape).
    """
    lines: list[str] = []
    for folder in sorted(progress.data.keys()):
        files = progress.data.get(folder) or {}
        if not isinstance(files, dict):
            continue
        for fn in sorted(files.keys()):
            entry = files.get(fn)
            if not isinstance(entry, dict):
                continue
            st = entry.get("status")
            if st not in ("reported", "problematic"):
                continue
            detail = entry.get("report_detail")
            if isinstance(detail, dict):
                rec = {**detail, "folder": folder, "filename": fn}
            else:
                dp = int(entry.get("data_points", 0) or 0)
                rec = {
                    "folder": folder,
                    "filename": fn,
                    "timestamp": entry.get("reviewed_at", datetime.now().isoformat()),
                    "scans": dp,
                    "data_points": dp,
                    "load_error": None,
                    "report_source": entry.get("report_source", "unknown"),
                    "depth_range": None,
                    "temp_range": None,
                    "conductivity_range": None,
                    "sal_range": None,
                    "do_range": None,
                    "selected_range": [0, max(0, dp - 1)],
                }
            lines.append(json.dumps(rec, ensure_ascii=False))
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


def validate_batch_progress_shape(data: object) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "Top level must be a JSON object (cruise folder -> files map)."
    for cruise, files in data.items():
        if not isinstance(cruise, str):
            return False, f"Cruise key must be a string, got {type(cruise).__name__}"
        if not isinstance(files, dict):
            return False, f"Cruise {cruise!r} must map filename strings to entry objects."
        for fname, entry in files.items():
            if not isinstance(fname, str):
                return False, f"Filename key under {cruise!r} must be a string."
            if not isinstance(entry, dict):
                return False, f"Entry for {cruise}/{fname} must be an object."
    return True, ""


def apply_batch_from_progress_data(
    data: dict,
    input_root: Path,
    output_root: Path,
    progress: ProgressTracker,
) -> dict:
    """
    Apply accepted (trim + mark_reviewed) and problematic (delete output + mark_deleted).
    end_idx in entries is exclusive (same as review_progress / save_trimmed_netcdf).
    """
    ok, err = validate_batch_progress_shape(data)
    if not ok:
        return {"ok": False, "error": err, "counts": {}}
    counts = {
        "accepted_ok": 0,
        "accepted_fail": 0,
        "deleted_output": 0,
        "skipped_status": 0,
        "errors": [],
    }
    for folder, files in data.items():
        for filename, entry in files.items():
            status = entry.get("status")
            if status == "accepted":
                start_idx = int(entry.get("start_idx", 0))
                end_idx = int(entry.get("end_idx", 0))
                auto_s = int(entry.get("auto_start_idx", start_idx))
                auto_e = int(entry.get("auto_end_idx", end_idx))
                method = str(entry.get("method", "unknown"))
                res = save_trimmed_netcdf(
                    input_root, output_root, folder, filename, start_idx, end_idx
                )
                if res.get("ok"):
                    progress.mark_reviewed(
                        folder, filename, "accepted",
                        start_idx, end_idx, auto_s, auto_e, method,
                    )
                    counts["accepted_ok"] += 1
                else:
                    counts["accepted_fail"] += 1
                    counts["errors"].append(
                        f"{folder}/{filename}: {res.get('error', 'save failed')}"
                    )
            elif status == "problematic":
                outp = output_root / folder / filename
                if outp.is_file():
                    outp.unlink()
                    counts["deleted_output"] += 1
                progress.mark_deleted(folder, filename, reason="batch_problematic")
            else:
                counts["skipped_status"] += 1
    return {"ok": True, "counts": counts}


# ---------------------------------------------------------------------------
# Suspicious-cast JSONL tracker
# ---------------------------------------------------------------------------

class SuspiciousCastTracker:
    """Manage suspicious_casts.jsonl: append, remove, and query reported casts."""

    def __init__(self, jsonl_path: Path):
        self.jsonl_path = jsonl_path
        self._entries: list[dict] = []
        self._keys: set[tuple[str, str]] = set()
        self.load()

    def load(self):
        self._entries = []
        self._keys = set()
        if not self.jsonl_path.exists():
            return
        try:
            with open(self.jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._entries.append(obj)
                    self._keys.add((obj.get("folder", ""), obj.get("filename", "")))
        except OSError:
            pass

    def is_reported(self, folder: str, filename: str) -> bool:
        return (folder, filename) in self._keys

    def append(self, record: dict):
        key = (record.get("folder", ""), record.get("filename", ""))
        if key in self._keys:
            return
        self._entries.append(record)
        self._keys.add(key)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def remove(self, folder: str, filename: str):
        key = (folder, filename)
        if key not in self._keys:
            return
        self._entries = [
            e for e in self._entries
            if (e.get("folder"), e.get("filename")) != key
        ]
        self._keys.discard(key)
        self._rewrite()

    def _rewrite(self):
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.jsonl_path, "w", encoding="utf-8") as f:
            for entry in self._entries:
                f.write(json.dumps(entry) + "\n")

    def all_entries(self) -> list[dict]:
        return list(self._entries)

    @staticmethod
    def build_too_short_record(folder: str, filename: str, data_points: int) -> dict:
        return {
            "folder": folder,
            "filename": filename,
            "timestamp": datetime.now().isoformat(),
            "scans": data_points,
            "data_points": data_points,
            "load_error": PROFILE_TOO_SHORT_MSG,
            "report_source": "auto_too_short",
            "depth_range": None,
            "temp_range": None,
            "conductivity_range": None,
            "sal_range": None,
            "do_range": None,
            "selected_range": [0, max(data_points, 0)],
        }


def _collect_too_short_profiles(input_root: Path, navigator) -> list[dict]:
    """All .nc files under navigator where load_and_analyze_file hits PROFILE_TOO_SHORT_MSG."""
    rows: list[dict] = []
    for folder in navigator.get_folders():
        for filename in navigator.get_files(folder):
            res = load_and_analyze_file(input_root, folder, filename)
            if not res or res.get("error") != PROFILE_TOO_SHORT_MSG:
                continue
            dp = int(res.get("data_points", res.get("total_scans", 0)))
            rows.append({"folder": folder, "filename": filename, "data_points": dp})
    return rows


class ProgressTracker:
    """Manage review_progress.json read/write."""

    def __init__(self, json_path: Path):
        self.json_path = json_path
        self.data: dict = {}
        self.load()

    def load(self):
        if self.json_path.exists():
            with open(self.json_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {}

    def save(self):
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get_status(self, folder: str, filename: str) -> str | None:
        """Return accepted, skipped, reported, problematic, deleted, or None."""
        return self.data.get(folder, {}).get(filename, {}).get("status")

    def mark_reviewed(
        self,
        folder: str,
        filename: str,
        status: str,
        start_idx: int,
        end_idx: int,
        auto_start_idx: int,
        auto_end_idx: int,
        method: str,
    ):
        """Record review action (accepted / skipped)."""
        if folder not in self.data:
            self.data[folder] = {}
        self.data[folder][filename] = {
            "status": status,
            "start_idx": start_idx,
            "end_idx": end_idx,
            "auto_start_idx": auto_start_idx,
            "auto_end_idx": auto_end_idx,
            "method": method,
            "reviewed_at": datetime.utcnow().isoformat(),
        }
        self.save()

    def mark_reported(
        self,
        folder: str,
        filename: str,
        data_points: int,
        report_source: str,
        report_detail: dict | None = None,
    ):
        """Flag a file as reported (lighter record than mark_reviewed)."""
        if folder not in self.data:
            self.data[folder] = {}
        entry: dict = {
            "status": "reported",
            "data_points": data_points,
            "report_source": report_source,
            "reviewed_at": datetime.utcnow().isoformat(),
        }
        if report_detail:
            entry["report_detail"] = report_detail
        self.data[folder][filename] = entry
        self.save()

    def mark_deleted(
        self,
        folder: str,
        filename: str,
        *,
        reason: str | None = None,
    ):
        """Terminal state: removed from problematic queue; optional output already deleted."""
        if folder not in self.data:
            self.data[folder] = {}
        prev = dict(self.data[folder].get(filename, {}))
        prev["status"] = "deleted"
        prev["reviewed_at"] = datetime.utcnow().isoformat()
        if reason:
            prev["delete_reason"] = reason
        self.data[folder][filename] = prev
        self.save()

    def mark_delete(
        self,
        folder: str,
        filename: str,
        *,
        reason: str | None = None,
    ):
        """Mark problematic review: should delete (stays in queue until accepted/skip/deleted)."""
        if folder not in self.data:
            self.data[folder] = {}
        entry = dict(self.data[folder].get(filename, {}))
        entry["status"] = "problematic"
        entry["should_delete"] = True
        if reason:
            entry["delete_reason"] = reason
        entry["marked_at"] = datetime.utcnow().isoformat()
        self.data[folder][filename] = entry
        self.save()


class FileNavigator:
    """Manage folder/file tree navigation."""

    def __init__(self, input_root: Path):
        self.input_root = input_root
        self.folders: list[str] = []
        self.files_by_folder: dict[str, list[str]] = {}
        self._scan_folders()

    def _scan_folders(self):
        """Index files directly in input_root and in its cruise subfolders."""
        root_files = sorted(
            f.name for f in self.input_root.iterdir()
            if f.is_file() and f.suffix.lower() == ".nc"
        )
        if root_files:
            self.folders.append(".")
            self.files_by_folder["."] = root_files
        for item in sorted(self.input_root.iterdir()):
            if not item.is_dir() or item.name.startswith("."):
                continue
            nc_files = sorted(f.name for f in item.iterdir() if f.is_file() and f.suffix.lower() == ".nc")
            if nc_files:
                self.folders.append(item.name)
                self.files_by_folder[item.name] = nc_files

    def get_folders(self) -> list[str]:
        return self.folders

    def get_files(self, folder: str) -> list[str]:
        return self.files_by_folder.get(folder, [])

    def find_next_unreviewed(
        self, current_folder: str, current_file: str, progress: ProgressTracker
    ) -> tuple[str, str] | None:
        """Find next unreviewed file starting from current position."""
        folders = self.get_folders()
        if current_folder not in folders:
            return None

        folder_idx = folders.index(current_folder)
        files = self.get_files(current_folder)
        if current_file in files:
            file_idx = files.index(current_file)
        else:
            file_idx = -1

        # Check remaining files in current folder
        for i in range(file_idx + 1, len(files)):
            if progress.get_status(current_folder, files[i]) is None:
                return (current_folder, files[i])

        # Check subsequent folders
        for i in range(folder_idx + 1, len(folders)):
            folder = folders[i]
            for filename in self.get_files(folder):
                if progress.get_status(folder, filename) is None:
                    return (folder, filename)

        return None

    def find_next_skipped(
        self, current_folder: str, current_file: str, progress: ProgressTracker
    ) -> tuple[str, str] | None:
        """Find next file with status 'skipped' starting after current position."""
        folders = self.get_folders()
        if current_folder not in folders:
            return None

        folder_idx = folders.index(current_folder)
        files = self.get_files(current_folder)
        if current_file in files:
            file_idx = files.index(current_file)
        else:
            file_idx = -1

        for i in range(file_idx + 1, len(files)):
            if progress.get_status(current_folder, files[i]) == "skipped":
                return (current_folder, files[i])

        for i in range(folder_idx + 1, len(folders)):
            folder = folders[i]
            for filename in self.get_files(folder):
                if progress.get_status(folder, filename) == "skipped":
                    return (folder, filename)

        return None

    def get_prev_file(self, folder: str, filename: str) -> tuple[str, str] | None:
        """Get previous file in current folder, or None if at start."""
        files = self.get_files(folder)
        if filename not in files:
            return None
        idx = files.index(filename)
        if idx > 0:
            return (folder, files[idx - 1])
        return None

    def get_next_file(self, folder: str, filename: str) -> tuple[str, str] | None:
        """Get next file in current folder, or None if at end."""
        files = self.get_files(folder)
        if filename not in files:
            return None
        idx = files.index(filename)
        if idx < len(files) - 1:
            return (folder, files[idx + 1])
        return None


def _dataset_progress_panel(
    progress: ProgressTracker, navigator: FileNavigator
) -> html.Div:
    """Whole input-root progress: all cruise folders and .nc files vs review_progress.json."""
    total = 0
    reviewed = 0
    flagged = 0
    deleted_n = 0
    for folder in navigator.get_folders():
        for fn in navigator.get_files(folder):
            total += 1
            st = progress.get_status(folder, fn)
            if st in ("accepted", "skipped"):
                reviewed += 1
            elif st in ("reported", "problematic"):
                flagged += 1
            elif st == "deleted":
                deleted_n += 1
    left = max(0, total - reviewed - flagged - deleted_n)
    pct = (100.0 * reviewed / total) if total else 0.0
    if total == 0:
        return html.Div(
            [
                html.Div(
                    "Dataset progress (all cruise folders)",
                    style={"fontWeight": "bold", "fontSize": "12px", "marginBottom": "6px"},
                ),
                html.Div(
                    "No .nc files found under input root.",
                    style={"fontSize": "11px", "color": "#666"},
                ),
            ],
            style={
                "margin": "10px",
                "padding": "8px",
                "backgroundColor": "#fff",
                "border": "1px solid #ddd",
                "borderRadius": "4px",
            },
        )
    return html.Div(
        [
            html.Div(
                "Dataset progress (all cruise folders)",
                style={"fontWeight": "bold", "fontSize": "12px", "marginBottom": "6px"},
            ),
            html.Div(
                f"Reviewed {reviewed} of {total} · {left} remaining"
                + (f" · {flagged} flagged" if flagged else "")
                + (f" · {deleted_n} deleted" if deleted_n else ""),
                style={"fontSize": "11px", "color": "#333", "marginBottom": "6px"},
            ),
            html.Div(
                html.Div(
                    style={
                        "width": f"{pct:.1f}%",
                        "height": "100%",
                        "backgroundColor": "#28a745",
                        "borderRadius": "3px",
                        "transition": "width 0.3s ease",
                    }
                ),
                style={
                    "width": "100%",
                    "height": "10px",
                    "backgroundColor": "#dee2e6",
                    "borderRadius": "4px",
                    "overflow": "hidden",
                },
            ),
        ],
        style={
            "margin": "10px",
            "padding": "8px",
            "backgroundColor": "#fff",
            "border": "1px solid #ddd",
            "borderRadius": "4px",
        },
    )


def _float64_ravel(arr: np.ndarray | list | None) -> np.ndarray | None:
    """1D float64 for stats; NetCDF vars may be int/uint (isnan unsupported)."""
    if arr is None:
        return None
    try:
        return np.asarray(arr, dtype=np.float64).ravel()
    except (TypeError, ValueError):
        return None


def _variable_y_range(data_arr) -> list[float] | None:
    """Tight y-axis bounds with padding so small variations (e.g. ~27 °C) are visible."""
    f = _float64_ravel(data_arr)
    if f is None or f.size == 0:
        return None
    valid = f[np.isfinite(f)]
    if valid.size == 0:
        return None
    lo, hi = float(valid.min()), float(valid.max())
    if lo == hi:
        pad = 1.0 if lo == 0 else max(abs(lo) * 0.02, 1e-6)
        return [lo - pad, hi + pad]
    span = hi - lo
    pad = max(span * 0.06, 1e-9)
    return [lo - pad, hi + pad]


def _last_scan_rounded_minimum(
    scan_indices: np.ndarray, depths: np.ndarray
) -> int | None:
    """Last scan index where depth rounds to 2 d.p. to the profile min (ties on rounded values)."""
    valid = np.isfinite(depths)
    if not np.any(valid):
        return None
    idx = scan_indices[valid]
    y = depths[valid]
    r_min = float(np.round(float(np.min(y)), 2))
    mask = np.round(y, 2) == r_min
    hits = idx[mask]
    return int(hits[-1]) if hits.size else None


def _first_scan_rounded_maximum(scan_indices: np.ndarray, depths: np.ndarray) -> int | None:
    """First scan index where depth rounds to 2 d.p. to the profile max (ties on rounded values)."""
    valid = np.isfinite(depths)
    if not np.any(valid):
        return None
    idx = scan_indices[valid]
    y = depths[valid]
    r_max = float(np.round(float(np.max(y)), 2))
    mask = np.round(y, 2) == r_max
    hits = idx[mask]
    return int(hits[0]) if hits.size else None


def load_and_analyze_file(
    input_root: Path, folder: str, filename: str, *, force_load: bool = False
) -> dict | None:
    """
    Load a NetCDF, extract depth and other variables, run soak detection.

    When *force_load* is True the MIN_PROFILE_SCANS gate and soak detection
    are skipped so that even very short / problematic profiles can be plotted.
    """
    file_path = input_root / folder / filename
    if not file_path.exists():
        return None

    try:
        ds = xr.open_dataset(file_path)
        depth_data = safe_depth_1d(ds)

        def extract_var(var_name: str) -> np.ndarray | None:
            if var_name in ds.data_vars:
                return np.asarray(ds[var_name].values).astype("float64", copy=False).ravel()
            return None

        temp_data = extract_var("sea_water_temperature")
        conductivity_data = extract_var("sea_water_electrical_conductivity")
        salinity_data = extract_var("sea_water_salinity")
        do_data = extract_var("dissolved_oxygen")

        ds.close()
    except Exception as e:
        return {"error": str(e), "total_scans": 0, "data_points": 0}

    n = int(depth_data.size)

    if not force_load and n < MIN_PROFILE_SCANS:
        return {
            "error": PROFILE_TOO_SHORT_MSG,
            "total_scans": n,
            "data_points": n,
        }

    base = {
        "depth_data": depth_data,
        "temp_data": temp_data,
        "conductivity_data": conductivity_data,
        "salinity_data": salinity_data,
        "do_data": do_data,
        "total_scans": n,
    }

    if force_load or n < MIN_PROFILE_SCANS:
        base.update({
            "auto_keep_from_idx": 0,
            "auto_keep_until_idx": n,
            "pre_depth_trim_idx": 0,
            "station_type": "unknown",
            "method": "none",
            "detected": False,
        })
        return base

    soak_result = get_soak_removal_index(depth_data, verbose=False)
    k0 = int(soak_result["keep_from_idx"])
    auto_until = int(soak_result.get("keep_until_idx", n))
    auto_until = max(k0 + 1, min(auto_until, n))

    base.update({
        "auto_keep_from_idx": k0,
        "auto_keep_until_idx": auto_until,
        "pre_depth_trim_idx": int(soak_result.get("pre_depth_trim_idx", 0)),
        "station_type": soak_result.get("station_type", "unknown"),
        "method": soak_result.get("method", "unknown"),
        "detected": bool(soak_result.get("detected", False)),
    })
    return base


def save_trimmed_netcdf(
    input_root: Path,
    output_root: Path,
    folder: str,
    filename: str,
    start_idx: int,
    end_idx: int,
) -> dict:
    """
    Slice original NetCDF at keep_from_idx and write to output_root.

    Returns dict with 'ok' (bool) and optional 'error' (str).
    """
    input_path = input_root / folder / filename
    output_path = output_root / folder / filename

    try:
        ds = xr.open_dataset(input_path)
        try:
            scan_dim = guess_scan_dim(ds)
            n = int(ds.dims[scan_dim])
            start_idx = max(0, min(int(start_idx), n))
            end_idx = max(0, min(int(end_idx), n))
            if end_idx < start_idx:
                start_idx, end_idx = end_idx, start_idx

            trimmed = ds.isel({scan_dim: slice(start_idx, end_idx)})
            trimmed = sanitize_encodings_for_netcdf(trimmed)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            trimmed.to_netcdf(output_path, engine="netcdf4")
            return {"ok": True}
        finally:
            ds.close()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def create_app(
    input_root: Path,
    output_root: Path,
    progress_json: Path,
    *,
    suspicious_export_jsonl: Path | None = None,
) -> dash.Dash:
    """Build and return the Dash app."""

    app = dash.Dash(__name__, suppress_callback_exceptions=True)

    navigator = FileNavigator(input_root)
    progress = ProgressTracker(progress_json)
    _export_jsonl = suspicious_export_jsonl or output_root / "suspicious_casts.jsonl"

    folders = navigator.get_folders()
    if not folders:
        app.layout = html.Div(
            [
                html.H3("No NetCDF files found"),
                html.P(f"Input root: {input_root}"),
                html.P("Choose a folder containing NetCDF files or cruise subfolders."),
            ],
            style={"padding": "20px"},
        )
        return app

    # ========================================================================
    # Layout
    # ========================================================================

    app.layout = html.Div(
        [
            # Hidden stores for state
            dcc.Store(id="current-folder", data=folders[0]),
            dcc.Store(id="current-file", data=navigator.get_files(folders[0])[0] if navigator.get_files(folders[0]) else None),
            dcc.Store(id="file-data", data=None),
            dcc.Store(id="click-target", data="start"),
            # Sidebar
            html.Div(
                [
                    html.H4("Soak Review", style={"margin": "10px"}),
                    html.Label(
                        "View Mode:",
                        style={"margin": "10px", "fontWeight": "bold"},
                    ),
                    dcc.RadioItems(
                        id="view-mode",
                        options=[
                            {"label": "Review (original)", "value": "review"},
                            {"label": "After soak removal", "value": "after"},
                            {"label": "Problematic datasets", "value": "problematic"},
                        ],
                        value="review",
                        labelStyle={"display": "block", "fontSize": "12px", "margin": "2px 0"},
                        inputStyle={"marginRight": "6px"},
                        style={"margin": "10px"},
                    ),
                    html.Div(id="dataset-progress"),
                    # -- Normal navigation (review / after modes) --
                    html.Div(
                        id="normal-nav-panel",
                        children=[
                            html.Label("Cruise Folder:", style={"margin": "10px"}),
                            dcc.Dropdown(
                                id="folder-dropdown",
                                options=[{"label": input_root.name if f == "." else f, "value": f} for f in folders],
                                value=folders[0],
                                clearable=False,
                                style={"margin": "10px"},
                            ),
                            html.Label("NetCDF files:", style={"margin": "10px"}),
                            html.Div(
                                dcc.RadioItems(
                                    id="file-radio",
                                    options=[],
                                    value=None,
                                    labelStyle={
                                        "display": "block",
                                        "margin": "2px 0",
                                        "fontSize": "11px",
                                        "wordBreak": "break-all",
                                    },
                                    inputStyle={"marginRight": "6px"},
                                ),
                                style={
                                    "maxHeight": "52vh",
                                    "overflowY": "auto",
                                    "margin": "10px",
                                    "border": "1px solid #ddd",
                                    "borderRadius": "4px",
                                    "padding": "6px",
                                    "backgroundColor": "#fff",
                                },
                            ),
                            html.Div(
                                [
                                    html.Button(
                                        "⏮ Prev",
                                        id="btn-prev",
                                        n_clicks=0,
                                        style={"width": "48%", "marginRight": "4%"},
                                    ),
                                    html.Button(
                                        "Next ⏭",
                                        id="btn-next",
                                        n_clicks=0,
                                        style={"width": "48%"},
                                    ),
                                ],
                                style={"margin": "10px"},
                            ),
                            html.Button(
                                "Jump to Next Unreviewed",
                                id="btn-jump-unreviewed",
                                n_clicks=0,
                                style={"width": "95%", "margin": "10px"},
                            ),
                            html.Button(
                                "Jump to Next Skipped",
                                id="btn-jump-skipped",
                                n_clicks=0,
                                style={"width": "95%", "margin": "10px"},
                            ),
                        ],
                    ),
                    # -- Problematic navigation (problematic mode) --
                    html.Div(
                        id="problematic-nav-panel",
                        children=[
                            html.Label(
                                "Problematic datasets:",
                                style={"margin": "10px", "fontWeight": "bold"},
                            ),
                            html.Div(
                                dcc.RadioItems(
                                    id="problematic-file-radio",
                                    options=[],
                                    value=None,
                                    labelStyle={
                                        "display": "block",
                                        "margin": "2px 0",
                                        "fontSize": "11px",
                                        "wordBreak": "break-all",
                                    },
                                    inputStyle={"marginRight": "6px"},
                                ),
                                style={
                                    "maxHeight": "60vh",
                                    "overflowY": "auto",
                                    "margin": "10px",
                                    "border": "1px solid #ddd",
                                    "borderRadius": "4px",
                                    "padding": "6px",
                                    "backgroundColor": "#fff",
                                },
                            ),
                            html.Div(
                                [
                                    html.Button(
                                        "⏮ Prev",
                                        id="btn-problematic-prev",
                                        n_clicks=0,
                                        style={"width": "48%", "marginRight": "4%"},
                                    ),
                                    html.Button(
                                        "Next ⏭",
                                        id="btn-problematic-next",
                                        n_clicks=0,
                                        style={"width": "48%"},
                                    ),
                                ],
                                style={"margin": "10px"},
                            ),
                        ],
                        style={"display": "none"},
                    ),
                    html.Hr(style={"margin": "12px 10px", "borderColor": "#ddd"}),
                    html.Div(
                        f"Too-short profiles (<{MIN_PROFILE_SCANS} depth points)",
                        style={"margin": "6px 10px", "fontWeight": "bold", "fontSize": "12px"},
                    ),
                    html.Button(
                        "Detect & Report Too-Short Profiles",
                        id="btn-detect-too-short",
                        n_clicks=0,
                        style={"width": "95%", "margin": "4px 10px"},
                    ),
                    dcc.Loading(
                        id="too-short-loading",
                        type="default",
                        children=html.Div(
                            id="too-short-feedback",
                            style={"margin": "4px 10px", "fontSize": "11px"},
                        ),
                    ),
                    html.Hr(style={"margin": "12px 10px", "borderColor": "#ddd"}),
                    html.Div(
                        "Problematic export & batch",
                        style={"margin": "6px 10px", "fontWeight": "bold", "fontSize": "12px"},
                    ),
                    html.Button(
                        "Export problematic → JSONL",
                        id="btn-export-suspicious-jsonl",
                        n_clicks=0,
                        style={"width": "95%", "margin": "4px 10px"},
                    ),
                    html.Div(
                        id="export-jsonl-feedback",
                        style={"margin": "4px 10px", "fontSize": "10px", "color": "#555"},
                    ),
                    dcc.Upload(
                        id="batch-json-upload",
                        children=html.Div(
                            ["Drop batch JSON or click to select"],
                            style={"fontSize": "11px", "textAlign": "center"},
                        ),
                        style={
                            "width": "95%",
                            "margin": "6px 10px",
                            "height": "50px",
                            "lineHeight": "50px",
                            "borderWidth": "1px",
                            "borderStyle": "dashed",
                            "borderRadius": "4px",
                            "borderColor": "#aaa",
                        },
                        multiple=False,
                    ),
                    html.Button(
                        "Apply uploaded batch JSON",
                        id="btn-apply-batch-upload",
                        n_clicks=0,
                        style={"width": "95%", "margin": "4px 10px"},
                    ),
                    html.Div(
                        id="batch-apply-feedback",
                        style={"margin": "4px 10px", "fontSize": "10px", "color": "#555"},
                    ),
                    html.Div(id="status-summary", style={"margin": "10px", "fontSize": "12px"}),
                ],
                style={
                    "width": "280px",
                    "position": "fixed",
                    "left": "0",
                    "top": "0",
                    "bottom": "0",
                    "backgroundColor": "#f8f9fa",
                    "borderRight": "1px solid #ddd",
                    "overflowY": "auto",
                },
            ),
            # Main content
            html.Div(
                [
                    html.H3(id="file-title", style={"margin": "20px"}),
                    html.Div(id="file-info", style={"margin": "20px", "fontSize": "14px"}),
                    dcc.Graph(
                        id="depth-graph",
                        style={"height": "900px", "margin": "20px"},
                        config={"displayModeBar": True},
                    ),
                    html.Div(id="stats-panel", style={"margin": "20px"}),
                    html.Div(
                        [
                            html.Label(
                                "Keep Range [start, end) (scan indices):",
                                style={"fontWeight": "bold"},
                            ),
                            html.Div(
                                "Tip: click the graph to set start/end (alternates each click).",
                                style={"fontSize": "12px", "color": "#666", "marginBottom": "6px"},
                            ),
                            dcc.RangeSlider(
                                id="cut-slider",
                                min=0,
                                max=1000,
                                step=1,
                                value=[0, 0],
                                marks={},
                                tooltip={"placement": "bottom", "always_visible": True},
                            ),
                        ],
                        style={"margin": "20px"},
                    ),
                    html.Div(
                        [
                            html.Button(
                                "✓ Accept & Save",
                                id="btn-accept",
                                n_clicks=0,
                                style={
                                    "backgroundColor": "#28a745",
                                    "color": "white",
                                    "padding": "10px 20px",
                                    "marginRight": "10px",
                                    "border": "none",
                                    "borderRadius": "4px",
                                    "cursor": "pointer",
                                },
                            ),
                            html.Button(
                                "⏭ Skip (no change)",
                                id="btn-skip",
                                n_clicks=0,
                                style={
                                    "backgroundColor": "#6c757d",
                                    "color": "white",
                                    "padding": "10px 20px",
                                    "marginRight": "10px",
                                    "border": "none",
                                    "borderRadius": "4px",
                                    "cursor": "pointer",
                                },
                            ),
                            html.Button(
                                "↺ Reset to Auto",
                                id="btn-reset",
                                n_clicks=0,
                                style={
                                    "backgroundColor": "#007bff",
                                    "color": "white",
                                    "padding": "10px 20px",
                                    "border": "none",
                                    "borderRadius": "4px",
                                    "cursor": "pointer",
                                },
                            ),
                            html.Button(
                                "⚠ Report This Station",
                                id="btn-report",
                                n_clicks=0,
                                style={
                                    "backgroundColor": "#dc3545",
                                    "color": "white",
                                    "padding": "10px 20px",
                                    "marginLeft": "10px",
                                    "border": "none",
                                    "borderRadius": "4px",
                                    "cursor": "pointer",
                                },
                            ),
                            html.Button(
                                "🗑 Mark Should Delete",
                                id="btn-mark-delete",
                                n_clicks=0,
                                style={
                                    "backgroundColor": "#ffc107",
                                    "color": "#212529",
                                    "padding": "10px 20px",
                                    "marginLeft": "10px",
                                    "border": "none",
                                    "borderRadius": "4px",
                                    "cursor": "pointer",
                                },
                            ),
                        ],
                        style={"margin": "20px"},
                    ),
                    html.Div(id="action-feedback", style={"margin": "20px", "color": "green"}),
                ],
                style={"marginLeft": "300px", "padding": "20px"},
            ),
        ]
    )

    # ========================================================================
    # Callbacks -- file navigation
    # ========================================================================

    @app.callback(
        Output("file-radio", "options"),
        Output("file-radio", "value"),
        Output("status-summary", "children"),
        Output("dataset-progress", "children"),
        Input("folder-dropdown", "value"),
        Input("current-file", "data"),
        Input("btn-accept", "n_clicks"),
        Input("btn-skip", "n_clicks"),
        Input("btn-report", "n_clicks"),
        Input("btn-mark-delete", "n_clicks"),
        Input("btn-apply-batch-upload", "n_clicks"),
    )
    def sync_file_radio_list(
        selected_folder, current_file, _a, _s, _r, _md, _batch,
    ):
        """Scrollable radio list of all files in the cruise + summary counts."""
        overall = _dataset_progress_panel(progress, navigator)
        if not selected_folder:
            return [], None, "", overall

        files = navigator.get_files(selected_folder)
        if not files:
            return [], None, html.Div("No .nc files in this folder"), overall

        accepted_count = 0
        skipped_count = 0
        flagged_count = 0
        deleted_count = 0
        options = []
        for fn in files:
            status = progress.get_status(selected_folder, fn)
            if status == "accepted":
                prefix = "✓ "
                accepted_count += 1
            elif status == "skipped":
                prefix = "⏭ "
                skipped_count += 1
            elif status == "reported":
                prefix = "⚠ "
                flagged_count += 1
            elif status == "problematic":
                prefix = "🗑 "
                flagged_count += 1
            elif status == "deleted":
                prefix = "✕ "
                deleted_count += 1
            else:
                prefix = "○ "
            options.append({"label": f"{prefix}{fn}", "value": fn})

        reviewed_count = accepted_count + skipped_count
        unreviewed_count = len(files) - reviewed_count - flagged_count - deleted_count

        if current_file in files:
            selected_value = current_file
        else:
            selected_value = files[0]

        summary_items = [
            html.Div(f"Total: {len(files)}", style={"fontWeight": "bold"}),
            html.Div(f"✓ Reviewed: {reviewed_count}", style={"color": "green"}),
            html.Div(f"○ Unreviewed: {unreviewed_count}", style={"color": "orange"}),
        ]
        if flagged_count:
            summary_items.append(
                html.Div(f"⚠ Flagged: {flagged_count}", style={"color": "#dc3545"})
            )
        if deleted_count:
            summary_items.append(
                html.Div(f"✕ Deleted: {deleted_count}", style={"color": "#6c757d"})
            )
        summary = html.Div(summary_items)

        return options, selected_value, summary, overall

    @app.callback(
        Output("current-file", "data", allow_duplicate=True),
        Input("file-radio", "value"),
        State("current-file", "data"),
        prevent_initial_call=True,
    )
    def pick_file_from_radio(selected_file, current_file):
        if selected_file == current_file:
            raise PreventUpdate
        return selected_file

    @app.callback(
        Output("folder-dropdown", "value", allow_duplicate=True),
        Input("current-folder", "data"),
        State("folder-dropdown", "value"),
        prevent_initial_call=True,
    )
    def sync_folder_dropdown_from_store(store_folder, dropdown_folder):
        """After Accept/Skip advances to another cruise folder, update the dropdown."""
        if not store_folder or store_folder == dropdown_folder:
            raise PreventUpdate
        return store_folder

    @app.callback(
        Output("current-folder", "data"),
        Output("current-file", "data"),
        Input("folder-dropdown", "value"),
        Input("btn-prev", "n_clicks"),
        Input("btn-next", "n_clicks"),
        Input("btn-jump-unreviewed", "n_clicks"),
        Input("btn-jump-skipped", "n_clicks"),
        State("current-folder", "data"),
        State("current-file", "data"),
        prevent_initial_call=True,
    )
    def navigate_files(
        folder_dropdown_value,
        btn_prev_clicks,
        btn_next_clicks,
        btn_jump_unreviewed_clicks,
        btn_jump_skipped_clicks,
        current_folder,
        current_file,
    ):
        """Folder change + Prev/Next/Jump. File selection uses file-radio (separate callback)."""
        ctx = dash.callback_context
        if not ctx.triggered:
            raise PreventUpdate

        trigger_id = ctx.triggered[0]["prop_id"]

        # Folder dropdown changed (user picked a different cruise folder)
        if trigger_id == "folder-dropdown.value":
            new_folder = folder_dropdown_value
            if new_folder == current_folder:
                # Programmatic sync: stores already set (e.g. after Accept/Save); do not reset file.
                raise PreventUpdate
            files = navigator.get_files(new_folder)
            for fn in files:
                if progress.get_status(new_folder, fn) is None:
                    return new_folder, fn
            new_file = files[0] if files else None
            return new_folder, new_file

        # Prev button
        if trigger_id == "btn-prev.n_clicks":
            prev = navigator.get_prev_file(current_folder, current_file)
            if prev:
                return prev
            raise PreventUpdate

        # Next button
        if trigger_id == "btn-next.n_clicks":
            nxt = navigator.get_next_file(current_folder, current_file)
            if nxt:
                return nxt
            raise PreventUpdate

        # Jump to unreviewed
        if trigger_id == "btn-jump-unreviewed.n_clicks":
            unreviewed = navigator.find_next_unreviewed(
                current_folder, current_file, progress
            )
            if unreviewed:
                return unreviewed
            raise PreventUpdate

        # Jump to next skipped (reviewed as skip)
        if trigger_id == "btn-jump-skipped.n_clicks":
            skipped = navigator.find_next_skipped(
                current_folder, current_file, progress
            )
            if skipped:
                return skipped
            raise PreventUpdate

        raise PreventUpdate

    # ====================================================================
    # Callbacks -- button state and graph interaction
    # ====================================================================

    @app.callback(
        Output("btn-accept", "disabled"),
        Output("btn-skip", "disabled"),
        Output("btn-reset", "disabled"),
        Output("btn-report", "disabled"),
        Output("btn-detect-too-short", "disabled"),
        Input("view-mode", "value"),
    )
    def set_button_disabled(view_mode):
        """Disable review actions when viewing the already-trimmed dataset."""
        disabled = view_mode == "after"
        return disabled, disabled, disabled, disabled, disabled

    @app.callback(
        Output("cut-slider", "value", allow_duplicate=True),
        Output("click-target", "data"),
        Input("depth-graph", "clickData"),
        State("cut-slider", "value"),
        State("file-data", "data"),
        State("click-target", "data"),
        prevent_initial_call=True,
    )
    def set_cut_by_click(click_data, current_range, file_data, click_target):
        """Set start/end by clicking on the graph (alternates start/end)."""
        if not click_data or not file_data or "error" in file_data:
            raise PreventUpdate

        pts = click_data.get("points") or []
        if not pts:
            raise PreventUpdate

        x = pts[0].get("x")
        if x is None:
            raise PreventUpdate

        total_scans = int(file_data.get("total_scans", 0))
        max_scan = max(0, total_scans - 1)
        xi = int(round(float(x)))
        xi = max(0, min(xi, max_scan))

        if not isinstance(current_range, (list, tuple)) or len(current_range) != 2:
            current_range = [0, max_scan]

        start, end = int(current_range[0]), int(current_range[1])
        click_target = click_target or "start"

        if click_target == "start":
            start = min(xi, end)
            next_target = "end"
        else:
            end = max(xi, start)
            next_target = "start"

        return [start, end], next_target

    # ====================================================================
    # Callbacks -- data loading and visualization
    # ====================================================================

    @app.callback(
        Output("file-data", "data"),
        Input("current-folder", "data"),
        Input("current-file", "data"),
        Input("view-mode", "value"),
    )
    def load_file_data(folder, filename, view_mode):
        """Load and analyze the current file (original, soak-removed, or problematic)."""
        if not folder or not filename:
            raise PreventUpdate

        if view_mode == "after":
            file_path = output_root / folder / filename
            if not file_path.exists():
                return {"error": f"Soak-removed file not found: {file_path}"}
            result = load_and_analyze_file(output_root, folder, filename)
            if result is None:
                return {"error": "Soak-removed file not found"}
            total_scans = int(result.get("total_scans", 0))
            init_start = 0
            init_end_excl = total_scans
            result["auto_keep_from_idx"] = 0
            result["auto_keep_until_idx"] = total_scans
            result["pre_depth_trim_idx"] = 0
        elif view_mode == "problematic":
            result = load_and_analyze_file(
                input_root, folder, filename, force_load=True
            )
            if result is None:
                return {"error": "File not found"}
            if "error" in result:
                return result
            total_scans = int(result.get("total_scans", 0))
            init_start = 0
            init_end_excl = total_scans
        else:
            result = load_and_analyze_file(input_root, folder, filename)
            if result is None:
                return {"error": "File not found"}

            reviewed = progress.data.get(folder, {}).get(filename, {})
            total_scans = int(result.get("total_scans", 0))
            auto_start = int(result.get("auto_keep_from_idx", 0))
            auto_end = int(result.get("auto_keep_until_idx", total_scans))

            if "start_idx" in reviewed and "end_idx" in reviewed:
                init_start = int(reviewed.get("start_idx", auto_start))
                init_end_excl = int(reviewed.get("end_idx", auto_end))
            elif "keep_from_idx" in reviewed:
                init_start = int(reviewed.get("keep_from_idx", auto_start))
                init_end_excl = total_scans
            else:
                init_start = auto_start
                init_end_excl = auto_end

        # Slider uses inclusive [start, end] scan indices.
        # Internal auto/progress values use exclusive end -- convert here.
        max_scan = max(0, total_scans - 1)
        init_start = max(0, min(init_start, max_scan))
        init_end = max(0, min(init_end_excl - 1, max_scan))
        if init_end < init_start:
            init_start, init_end = init_end, init_start

        result["initial_start_idx"] = init_start
        result["initial_end_idx"] = init_end

        # Store as JSON-serializable (convert numpy arrays to lists)
        if "depth_data" in result:
            result["depth_data"] = result["depth_data"].tolist()
        if "temp_data" in result and result["temp_data"] is not None:
            result["temp_data"] = result["temp_data"].tolist()
        if "conductivity_data" in result and result["conductivity_data"] is not None:
            result["conductivity_data"] = result["conductivity_data"].tolist()
        if "salinity_data" in result and result["salinity_data"] is not None:
            result["salinity_data"] = result["salinity_data"].tolist()
        if "do_data" in result and result["do_data"] is not None:
            result["do_data"] = result["do_data"].tolist()

        result["folder"] = folder
        result["filename"] = filename
        return result

    @app.callback(
        Output("file-title", "children"),
        Output("file-info", "children"),
        Output("depth-graph", "figure"),
        Output("stats-panel", "children"),
        Output("cut-slider", "min"),
        Output("cut-slider", "max"),
        Output("cut-slider", "value"),
        Output("cut-slider", "marks"),
        Input("file-data", "data"),
        Input("cut-slider", "value"),
        State("current-folder", "data"),
        State("current-file", "data"),
        State("view-mode", "value"),
    )
    def update_visualization(file_data, cut_value, current_folder, current_file, view_mode):
        """Update graph, stats, and slider when file changes or slider moves."""
        if not file_data or "error" in file_data:
            error_msg = file_data.get("error", "Unknown error") if file_data else "No file loaded"
            empty_fig = go.Figure()
            empty_fig.add_annotation(
                text=error_msg, xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False
            )
            return (
                f"Error: {current_folder}/{current_file}",
                "",
                empty_fig,
                "",
                0,
                0,
                [0, 0],
                {},
            )

        depth_data = np.array(file_data["depth_data"])
        temp_data = np.array(file_data["temp_data"]) if file_data.get("temp_data") else None
        conductivity_data = np.array(file_data["conductivity_data"]) if file_data.get("conductivity_data") else None
        salinity_data = np.array(file_data["salinity_data"]) if file_data.get("salinity_data") else None
        do_data = np.array(file_data["do_data"]) if file_data.get("do_data") else None

        total_scans = file_data["total_scans"]
        max_scan = max(0, total_scans - 1)
        auto_start = file_data["auto_keep_from_idx"]
        auto_end_excl = int(file_data.get("auto_keep_until_idx", total_scans))
        auto_end = max(0, auto_end_excl - 1)
        pre_trim_idx = file_data.get("pre_depth_trim_idx", 0)
        station_type = file_data.get("station_type", "unknown")
        method = file_data.get("method", "unknown")
        detected = file_data.get("detected", False)

        # Slider uses inclusive [start, end] scan indices.
        ctx = dash.callback_context
        if ctx.triggered and ctx.triggered[0]["prop_id"] == "file-data.data":
            current_start = int(file_data.get("initial_start_idx", auto_start))
            current_end = int(file_data.get("initial_end_idx", auto_end))
        else:
            if isinstance(cut_value, (list, tuple)) and len(cut_value) == 2:
                current_start, current_end = int(cut_value[0]), int(cut_value[1])
            else:
                current_start, current_end = auto_start, auto_end

        current_start = max(0, min(int(current_start), max_scan))
        current_end = max(0, min(int(current_end), max_scan))
        if current_end < current_start:
            current_start, current_end = current_end, current_start

        # Rounded global high/low (for markers + for snapping keep-from when tied to algorithm cut).
        idx_global_shallow: int | None = None
        idx_global_deep: int | None = None
        idx_real_global_shallow: int | None = None
        y_global_shallow = float("nan")
        y_global_deep = float("nan")
        y_real_global_shallow = float("nan")
        if depth_data.size > 0:
            scans_all = np.arange(total_scans)
            valid_all = np.isfinite(depth_data)
            if np.any(valid_all):
                idx_all = scans_all[valid_all]
                y_all = depth_data[valid_all]
                pos_last_true_min = y_all.size - 1 - int(np.argmin(y_all[::-1]))
                idx_real_global_shallow = int(idx_all[pos_last_true_min])
                y_real_global_shallow = float(depth_data[idx_real_global_shallow])
                i_sh = _last_scan_rounded_minimum(idx_all, y_all)
                i_dp = _first_scan_rounded_maximum(idx_all, y_all)
                if i_sh is None or i_dp is None:
                    pos_min = y_all.size - 1 - int(np.argmin(y_all[::-1]))
                    idx_global_shallow = int(idx_all[pos_min])
                    idx_global_deep = int(idx_all[int(np.argmax(y_all))])
                else:
                    idx_global_shallow = i_sh
                    idx_global_deep = i_dp
                y_global_shallow = float(depth_data[idx_global_shallow])
                y_global_deep = float(depth_data[idx_global_deep])

        if (
            idx_global_shallow is not None
            and int(current_start) == int(auto_start)
        ):
            d_auto = (
                float(depth_data[auto_start])
                if 0 <= int(auto_start) < depth_data.size
                and np.isfinite(depth_data[auto_start])
                else float("nan")
            )
            if np.isfinite(d_auto) and np.isfinite(y_global_shallow) and abs(d_auto - y_global_shallow) > 0.1:
                current_start = int(idx_global_shallow)

        # Right cut: first scan (in count order) where depth rounds to 2 d.p. to the profile max — when end still matches auto.
        if idx_global_deep is not None and int(current_end) == int(auto_end):
            current_end = int(idx_global_deep)

        current_start = max(0, min(int(current_start), max_scan))
        current_end = max(0, min(int(current_end), max_scan))
        if current_start > current_end:
            current_end = int(current_start)

        scans = np.arange(total_scans)

        # Build 5-row subplot figure
        fig = make_subplots(
            rows=5, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.035,
            row_heights=[0.36, 0.16, 0.16, 0.16, 0.16],
            subplot_titles=("Depth", "Temperature", "Conductivity", "Salinity", "Dissolved Oxygen")
        )

        # Row 1: Depth -- fills overlap by one point at boundaries to avoid gaps.
        fig.add_trace(
            go.Scatter(x=scans, y=depth_data, mode="lines", name="Depth",
                       line=dict(color="blue", width=1.5), showlegend=False),
            row=1, col=1
        )
        if current_start > 0:
            fill_end = min(current_start + 1, total_scans)
            fig.add_trace(
                go.Scatter(x=scans[:fill_end], y=depth_data[:fill_end],
                           fill="tozeroy", mode="none", fillcolor="rgba(255, 255, 0, 0.25)",
                           name="Removed", showlegend=False),
                row=1, col=1
            )
        if current_end < max_scan:
            fill_start = max(0, current_end)
            fig.add_trace(
                go.Scatter(x=scans[fill_start:], y=depth_data[fill_start:],
                           fill="tozeroy", mode="none", fillcolor="rgba(255, 255, 0, 0.25)",
                           showlegend=False),
                row=1, col=1
            )
        if current_end >= current_start:
            fig.add_trace(
                go.Scatter(x=scans[current_start:current_end + 1],
                           y=depth_data[current_start:current_end + 1],
                           fill="tozeroy", mode="none", fillcolor="rgba(40, 167, 69, 0.15)",
                           name="Kept", showlegend=False),
                row=1, col=1
            )

        # Mark global and local shallowest/deepest points on the depth profile.
        # Global: over the entire valid depth series.
        # Local: restricted to the currently kept interval (falls back to global if no kept data).
        if depth_data.size > 0 and idx_global_shallow is not None and idx_global_deep is not None:
            # Local extrema within the kept window, if any valid points; otherwise reuse global.
            if current_end >= current_start:
                idx_local_slice = np.arange(current_start, current_end + 1)
                y_local_slice = depth_data[idx_local_slice]
                valid_local = np.isfinite(y_local_slice)
            else:
                valid_local = np.zeros(0, dtype=bool)

            if np.any(valid_local):
                idx_local_valid = idx_local_slice[valid_local]
                y_local_valid = depth_data[idx_local_valid]
                idx_local_shallow = int(idx_local_valid[np.argmin(y_local_valid)])
                idx_local_deep = int(idx_local_valid[np.argmax(y_local_valid)])
                y_local_shallow = float(depth_data[idx_local_shallow])
                y_local_deep = float(depth_data[idx_local_deep])
            else:
                idx_local_shallow = idx_global_shallow
                idx_local_deep = idx_global_deep
                y_local_shallow = y_global_shallow
                y_local_deep = y_global_deep

            # Global markers
            fig.add_trace(
                go.Scatter(
                    x=[idx_global_shallow],
                    y=[y_global_shallow],
                    mode="markers+text",
                    text=[f"Global high: {y_global_shallow:.2f} m"],
                    textposition="top center",
                    marker=dict(color="darkgreen", size=11, symbol="diamond"),
                    name="Global shallowest",
                    showlegend=False,
                ),
                row=1,
                col=1,
            )
            if (
                idx_real_global_shallow is not None
                and np.isfinite(y_real_global_shallow)
                and int(idx_real_global_shallow) != int(idx_global_shallow)
            ):
                ytxt = f"{y_real_global_shallow:.3f}"
                fig.add_trace(
                    go.Scatter(
                        x=[idx_real_global_shallow],
                        y=[y_real_global_shallow],
                        mode="markers+text",
                        text=[f"Real global high: {ytxt} m"],
                        textposition="top left",
                        marker=dict(
                            color="darkcyan",
                            size=10,
                            symbol="circle",
                            line=dict(width=1.5, color="white"),
                        ),
                        name="Real global shallowest",
                        showlegend=False,
                    ),
                    row=1,
                    col=1,
                )
            fig.add_trace(
                go.Scatter(
                    x=[idx_global_deep],
                    y=[y_global_deep],
                    mode="markers+text",
                    text=[f"Global low: {y_global_deep:.2f} m"],
                    textposition="bottom center",
                    marker=dict(color="darkred", size=11, symbol="diamond-open"),
                    name="Global deepest",
                    showlegend=False,
                ),
                row=1,
                col=1,
            )

            # Local markers (within kept window)
            fig.add_trace(
                go.Scatter(
                    x=[idx_local_shallow],
                    y=[y_local_shallow],
                    mode="markers+text",
                    text=[f"Local high: {y_local_shallow:.2f} m"],
                    textposition="top right",
                    marker=dict(color="green", size=9, symbol="triangle-up"),
                    name="Local shallowest",
                    showlegend=False,
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=[idx_local_deep],
                    y=[y_local_deep],
                    mode="markers+text",
                    text=[f"Local low: {y_local_deep:.2f} m"],
                    textposition="bottom right",
                    marker=dict(color="red", size=9, symbol="triangle-down"),
                    name="Local deepest",
                    showlegend=False,
                ),
                row=1,
                col=1,
            )

        if (
            depth_data.size > 0
            and 0 <= int(auto_start) < depth_data.size
            and np.isfinite(depth_data[auto_start])
        ):
            y_at_auto = float(depth_data[auto_start])
            fig.add_trace(
                go.Scatter(
                    x=[int(auto_start)],
                    y=[y_at_auto],
                    mode="markers+text",
                    text=[f"Algo cut pt: {y_at_auto:.2f} m"],
                    textposition="middle right",
                    marker=dict(
                        color="darkviolet",
                        size=11,
                        symbol="star",
                        line=dict(width=1, color="white"),
                    ),
                    name="Algorithm cut",
                    showlegend=False,
                ),
                row=1,
                col=1,
            )

        # Row 2: Temperature (no tozeroy fill — it forces y=0 and hides small changes)
        if temp_data is not None:
            fig.add_trace(
                go.Scatter(x=scans, y=temp_data, mode="lines", name="Temp",
                           line=dict(color="orange", width=1.5), showlegend=False),
                row=2, col=1
            )
        else:
            fig.add_annotation(text="No data", xref="x2", yref="y2", x=0.5, y=0.5, 
                               showarrow=False, row=2, col=1)

        # Row 3: Conductivity
        if conductivity_data is not None:
            fig.add_trace(
                go.Scatter(x=scans, y=conductivity_data, mode="lines", name="Conductivity",
                           line=dict(color="purple", width=1.5), showlegend=False),
                row=3, col=1
            )
        else:
            fig.add_annotation(text="No data", xref="x3", yref="y3", x=0.5, y=0.5,
                               showarrow=False, row=3, col=1)

        # Row 4: Salinity
        if salinity_data is not None:
            fig.add_trace(
                go.Scatter(x=scans, y=salinity_data, mode="lines", name="Salinity",
                           line=dict(color="green", width=1.5), showlegend=False),
                row=4, col=1
            )
        else:
            fig.add_annotation(text="No data", xref="x4", yref="y4", x=0.5, y=0.5,
                               showarrow=False, row=4, col=1)

        # Row 5: Dissolved Oxygen
        if do_data is not None:
            fig.add_trace(
                go.Scatter(x=scans, y=do_data, mode="lines", name="DO",
                           line=dict(color="red", width=1.5), showlegend=False),
                row=5, col=1
            )
        else:
            fig.add_annotation(text="No data", xref="x5", yref="y5", x=0.5, y=0.5,
                               showarrow=False, row=5, col=1)

        # Red dashes: start = keep-from (algo vs rounded global-high snap); end = rounded global-low first scan when end was auto.
        for row in range(1, 6):
            fig.add_vline(x=current_start, line=dict(color="red", dash="dash", width=2), row=row, col=1)
            fig.add_vline(x=current_end, line=dict(color="red", dash="dash", width=2), row=row, col=1)
            if pre_trim_idx > 0:
                fig.add_vline(x=pre_trim_idx, line=dict(color="green", dash="dot", width=1.5), row=row, col=1)

        # Update axes -- x-axis: integer ticks, range = [0, N-1]
        x_dtick = 1 if total_scans <= 20 else (max(1, total_scans // 10) if total_scans <= 200 else None)
        for row in range(1, 6):
            xkw: dict = {"range": [-0.5, max_scan + 0.5], "tick0": 0}
            if x_dtick is not None:
                xkw["dtick"] = x_dtick
            fig.update_xaxes(**xkw, row=row, col=1)
        fig.update_xaxes(title_text="Scan Number", row=5, col=1)

        fig.update_yaxes(title_text="Depth (m)", autorange="reversed", row=1, col=1)
        fig.update_yaxes(title_text="Temp (°C)", row=2, col=1)
        fig.update_yaxes(title_text="Conductivity (S/m)", row=3, col=1)
        fig.update_yaxes(title_text="Salinity (PSU)", row=4, col=1)
        fig.update_yaxes(title_text="DO (mg/L)", row=5, col=1)
        for row, series in (
            (2, temp_data),
            (3, conductivity_data),
            (4, salinity_data),
            (5, do_data),
        ):
            yr = _variable_y_range(series)
            if yr is not None:
                fig.update_yaxes(range=yr, rangemode="normal", row=row, col=1)

        fig.update_layout(
            title_text=f"Station: {station_type} | Method: {method} | Detected: {detected}",
            hovermode="x unified",
            showlegend=False,
            height=900
        )

        # Title uses file_data keys so plot always matches the loaded profile (avoids store/dropdown lag).
        disp_folder = file_data.get("folder") or current_folder
        disp_file = file_data.get("filename") or current_file

        status = progress.get_status(disp_folder, disp_file)
        status_badge = ""
        if status == "accepted":
            status_badge = " ✓ [Reviewed: Accepted]"
        elif status == "skipped":
            status_badge = " ⏭ [Reviewed: Skipped]"
        elif status == "reported":
            status_badge = " ⚠ [Reported]"
        elif status == "problematic":
            status_badge = " 🗑 [Problematic / should delete]"
        elif status == "deleted":
            status_badge = " ✕ [Deleted]"

        view_labels = {"after": " [After soak removal]", "problematic": " [Problematic]"}
        view_badge = view_labels.get(view_mode, " [Review]")
        folder_label = input_root.name if disp_folder == "." else disp_folder
        title = f"{folder_label} / {disp_file}{status_badge}{view_badge}"

        kept_count = max(0, current_end - current_start + 1)
        info_lines = [
            f"Total scans: {total_scans}",
            f"Auto range: [{auto_start}, {auto_end}]",
            f"Current range: [{current_start}, {current_end}]",
            f"Kept scans: {kept_count}",
            f"Station type: {station_type}",
            f"Method: {method}",
        ]
        info = html.Div([html.Div(line) for line in info_lines])

        # Compute stats (coerce to float64; integer dtypes break np.isnan)
        # Slice uses inclusive end → Python slice needs end+1.
        def compute_stats(data_arr, start, end_incl):
            f = _float64_ravel(data_arr)
            if f is None or f.size == 0:
                return "N/A", "N/A"
            valid = f[np.isfinite(f)]
            if valid.size == 0:
                return "N/A", "N/A"
            slice_data = f[start:end_incl + 1]
            valid_slice = slice_data[np.isfinite(slice_data)]
            if valid_slice.size == 0:
                return (f"{valid.min():.2f} / {valid.max():.2f}", "N/A")
            return (
                f"{valid.min():.2f} / {valid.max():.2f}",
                f"{valid_slice.min():.2f} / {valid_slice.max():.2f}",
            )

        depth_full, depth_sel = compute_stats(depth_data, current_start, current_end)
        temp_full, temp_sel = compute_stats(temp_data, current_start, current_end)
        cond_full, cond_sel = compute_stats(conductivity_data, current_start, current_end)
        sal_full, sal_sel = compute_stats(salinity_data, current_start, current_end)
        do_full, do_sel = compute_stats(do_data, current_start, current_end)

        stats_panel = html.Div([
            html.H5("Statistics", style={"marginBottom": "10px"}),
            html.Table([
                html.Thead(html.Tr([
                    html.Th("Metric", style={"textAlign": "left", "padding": "8px", "borderBottom": "2px solid #ddd"}),
                    html.Th("Full File", style={"textAlign": "left", "padding": "8px", "borderBottom": "2px solid #ddd"}),
                    html.Th("Selected Range", style={"textAlign": "left", "padding": "8px", "borderBottom": "2px solid #ddd"}),
                ])),
                html.Tbody([
                    html.Tr([
                        html.Td("Scans", style={"padding": "8px"}),
                        html.Td(str(total_scans), style={"padding": "8px"}),
                        html.Td(str(kept_count), style={"padding": "8px"}),
                    ]),
                    html.Tr([
                        html.Td("Depth (min/max)", style={"padding": "8px"}),
                        html.Td(depth_full, style={"padding": "8px"}),
                        html.Td(depth_sel, style={"padding": "8px"}),
                    ]),
                    html.Tr([
                        html.Td("Temperature (min/max)", style={"padding": "8px"}),
                        html.Td(temp_full, style={"padding": "8px"}),
                        html.Td(temp_sel, style={"padding": "8px"}),
                    ]),
                    html.Tr([
                        html.Td("Conductivity (min/max)", style={"padding": "8px"}),
                        html.Td(cond_full, style={"padding": "8px"}),
                        html.Td(cond_sel, style={"padding": "8px"}),
                    ]),
                    html.Tr([
                        html.Td("Salinity (min/max)", style={"padding": "8px"}),
                        html.Td(sal_full, style={"padding": "8px"}),
                        html.Td(sal_sel, style={"padding": "8px"}),
                    ]),
                    html.Tr([
                        html.Td("Dissolved Oxygen (min/max)", style={"padding": "8px"}),
                        html.Td(do_full, style={"padding": "8px"}),
                        html.Td(do_sel, style={"padding": "8px"}),
                    ]),
                ]),
            ], style={"borderCollapse": "collapse", "width": "100%"}),
        ])

        # Slider config -- uses inclusive scan indices, max = max_scan
        slider_marks = {
            0: "0",
            auto_start: {"label": f"Auto start: {auto_start}", "style": {"color": "red"}},
            max_scan: str(max_scan),
        }
        if pre_trim_idx > 0 and pre_trim_idx != auto_start:
            slider_marks[pre_trim_idx] = {
                "label": f"2m: {pre_trim_idx}",
                "style": {"color": "green"},
            }
        if auto_end < max_scan and auto_end not in slider_marks:
            slider_marks[auto_end] = {
                "label": f"Auto end: {auto_end}",
                "style": {"color": "#6f42c1"},
            }

        return title, info, fig, stats_panel, 0, max_scan, [current_start, current_end], slider_marks

    # ====================================================================
    # Callbacks -- review actions (accept, skip, report, reset)
    # ====================================================================

    @app.callback(
        Output("cut-slider", "value", allow_duplicate=True),
        Input("btn-reset", "n_clicks"),
        State("file-data", "data"),
        State("view-mode", "value"),
        prevent_initial_call=True,
    )
    def reset_to_auto(n_clicks, file_data, view_mode):
        """Reset slider to auto-detected cut point."""
        if not n_clicks or not file_data:
            raise PreventUpdate
        if view_mode == "after":
            raise PreventUpdate
        total_scans = int(file_data.get("total_scans", 0))
        auto_start = int(file_data.get("auto_keep_from_idx", 0))
        auto_end_excl = int(file_data.get("auto_keep_until_idx", total_scans))
        return [auto_start, max(0, auto_end_excl - 1)]

    @app.callback(
        Output("action-feedback", "children"),
        Output("current-folder", "data", allow_duplicate=True),
        Output("current-file", "data", allow_duplicate=True),
        Input("btn-accept", "n_clicks"),
        Input("btn-skip", "n_clicks"),
        State("current-folder", "data"),
        State("current-file", "data"),
        State("view-mode", "value"),
        State("cut-slider", "value"),
        State("file-data", "data"),
        prevent_initial_call=True,
    )
    def handle_save_skip(
        accept_clicks, skip_clicks, folder, filename, view_mode, cut_value, file_data
    ):
        """Handle Accept & Save or Skip actions."""
        ctx = dash.callback_context
        if not ctx.triggered:
            raise PreventUpdate
        if view_mode == "after":
            raise PreventUpdate

        trigger_id = ctx.triggered[0]["prop_id"]

        if not folder or not filename or not file_data:
            raise PreventUpdate

        total_scans = int(file_data.get("total_scans", 0))
        auto_start = int(file_data.get("auto_keep_from_idx", 0))
        auto_end = int(file_data.get("auto_keep_until_idx", total_scans))
        method = file_data.get("method", "unknown")
        reviewed_ok = False

        if trigger_id == "btn-accept.n_clicks":
            if not (isinstance(cut_value, (list, tuple)) and len(cut_value) == 2):
                feedback = html.Div(
                    "✗ Invalid range selection",
                    style={"color": "red", "fontWeight": "bold"},
                )
                return feedback, dash.no_update, dash.no_update

            start_idx, end_idx_incl = int(cut_value[0]), int(cut_value[1])
            end_idx_excl = end_idx_incl + 1
            result = save_trimmed_netcdf(
                input_root, output_root, folder, filename, start_idx, end_idx_excl
            )
            if result["ok"]:
                progress.mark_reviewed(
                    folder,
                    filename,
                    "accepted",
                    start_idx,
                    end_idx_excl,
                    auto_start,
                    auto_end,
                    method,
                )
                reviewed_ok = True
                feedback = html.Div(
                    f"✓ Saved {filename} with range [{start_idx}, {end_idx_incl}]",
                    style={"color": "green", "fontWeight": "bold"},
                )
            else:
                feedback = html.Div(
                    f"✗ Error saving: {result.get('error', 'Unknown')}",
                    style={"color": "red", "fontWeight": "bold"},
                )

        elif trigger_id == "btn-skip.n_clicks":
            progress.mark_reviewed(
                folder,
                filename,
                "skipped",
                auto_start,
                auto_end,
                auto_start,
                auto_end,
                method,
            )
            reviewed_ok = True
            feedback = html.Div(
                f"⏭ Skipped {filename} (no changes written)",
                style={"color": "gray", "fontWeight": "bold"},
            )
        else:
            raise PreventUpdate

        if not reviewed_ok:
            return feedback, dash.no_update, dash.no_update

        if view_mode == "problematic":
            nxt = first_remaining_problematic(progress, navigator)
            if nxt:
                return feedback, nxt[0], nxt[1]
            return (
                html.Div([feedback, html.Div(
                    "No more problematic datasets.",
                    style={"fontSize": "12px", "marginTop": "6px"},
                )]),
                folder, filename,
            )

        nxt = navigator.find_next_unreviewed(folder, filename, progress)
        if nxt:
            return feedback, nxt[0], nxt[1]
        extra = html.Div(
            "No more unreviewed files after this one in the dataset.",
            style={"fontSize": "12px", "marginTop": "6px"},
        )
        return html.Div([feedback, extra]), folder, filename

    @app.callback(
        Output("action-feedback", "children", allow_duplicate=True),
        Input("btn-report", "n_clicks"),
        State("current-folder", "data"),
        State("current-file", "data"),
        State("view-mode", "value"),
        State("file-data", "data"),
        State("cut-slider", "value"),
        prevent_initial_call=True,
    )
    def handle_report(n_clicks, folder, filename, view_mode, file_data, cut_value):
        """Report this station as suspicious."""
        if not n_clicks or not folder or not filename or not file_data:
            raise PreventUpdate
        if view_mode == "after":
            raise PreventUpdate

        load_error = file_data.get("error")

        def _arr(key: str):
            v = file_data.get(key)
            if v is None:
                return None
            return np.array(v)

        depth_data = _arr("depth_data")
        temp_data = _arr("temp_data")
        conductivity_data = _arr("conductivity_data")
        salinity_data = _arr("salinity_data")
        do_data = _arr("do_data")

        total_scans = int(file_data.get("total_scans", 0) or 0)

        if isinstance(cut_value, (list, tuple)) and len(cut_value) == 2:
            start_idx, end_idx_incl = int(cut_value[0]), int(cut_value[1])
        else:
            start_idx, end_idx_incl = 0, max(0, total_scans - 1)

        def get_range(data_arr):
            f = _float64_ravel(data_arr)
            if f is None or f.size == 0:
                return None
            valid = f[np.isfinite(f)]
            if valid.size == 0:
                return None
            return [float(valid.min()), float(valid.max())]

        if progress.get_status(folder, filename) in ("reported", "problematic"):
            return html.Div(
                f"Already flagged as problematic: {folder}/{filename}",
                style={"color": "#856404", "fontWeight": "bold"},
            )

        record = {
            "folder": folder,
            "filename": filename,
            "timestamp": datetime.now().isoformat(),
            "scans": total_scans,
            "data_points": total_scans,
            "load_error": load_error,
            "report_source": "manual",
            "depth_range": get_range(depth_data),
            "temp_range": get_range(temp_data),
            "conductivity_range": get_range(conductivity_data),
            "sal_range": get_range(salinity_data),
            "do_range": get_range(do_data),
            "selected_range": [start_idx, end_idx_incl],
        }

        try:
            progress.mark_reported(
                folder, filename, total_scans, "manual", report_detail=record
            )
            feedback = html.Div(
                f"⚠ Reported {folder}/{filename} as suspicious",
                style={"color": "#dc3545", "fontWeight": "bold"},
            )
        except Exception as e:
            feedback = html.Div(
                f"✗ Error writing report: {e}",
                style={"color": "red", "fontWeight": "bold"},
            )

        return feedback

    # ====================================================================
    # Panel toggle and problematic-view callbacks
    # ====================================================================

    @app.callback(
        Output("normal-nav-panel", "style"),
        Output("problematic-nav-panel", "style"),
        Input("view-mode", "value"),
    )
    def toggle_nav_panels(view_mode):
        """Show the correct sidebar panel for the active view mode."""
        if view_mode == "problematic":
            return {"display": "none"}, {"display": "block"}
        return {"display": "block"}, {"display": "none"}

    @app.callback(
        Output("problematic-file-radio", "options"),
        Output("problematic-file-radio", "value"),
        Input("view-mode", "value"),
        Input("btn-accept", "n_clicks"),
        Input("btn-skip", "n_clicks"),
        Input("btn-report", "n_clicks"),
        Input("btn-detect-too-short", "n_clicks"),
        Input("btn-mark-delete", "n_clicks"),
        Input("btn-apply-batch-upload", "n_clicks"),
    )
    def populate_problematic_list(
        view_mode, _a, _s, _r, _d, _md, _batch,
    ):
        """Rebuild problematic queue from review_progress (reported / problematic)."""
        pairs = iter_problematic_queue(progress, navigator)
        if not pairs:
            return [], None
        options = []
        for folder, fn in pairs:
            entry = progress.data.get(folder, {}).get(fn, {})
            dp = entry.get("data_points", "?")
            fl = "🗑 " if entry.get("should_delete") else "⚠ "
            label = f"{fl}{folder}/{fn} ({dp} pts)"
            options.append({"label": label, "value": f"{folder}{PROBLEMATIC_SEP}{fn}"})
        return options, options[0]["value"] if options else None

    @app.callback(
        Output("current-folder", "data", allow_duplicate=True),
        Output("current-file", "data", allow_duplicate=True),
        Input("problematic-file-radio", "value"),
        State("view-mode", "value"),
        prevent_initial_call=True,
    )
    def pick_problematic_file(encoded_value, view_mode):
        """Navigate to the selected problematic dataset."""
        if view_mode != "problematic" or not encoded_value:
            raise PreventUpdate
        if PROBLEMATIC_SEP not in encoded_value:
            raise PreventUpdate
        folder, filename = encoded_value.split(PROBLEMATIC_SEP, 1)
        return folder, filename

    @app.callback(
        Output("problematic-file-radio", "value", allow_duplicate=True),
        Input("btn-problematic-prev", "n_clicks"),
        Input("btn-problematic-next", "n_clicks"),
        State("problematic-file-radio", "options"),
        State("problematic-file-radio", "value"),
        State("view-mode", "value"),
        prevent_initial_call=True,
    )
    def navigate_problematic_list(_prev, _next, options, current_value, view_mode):
        """Prev/Next navigation within the problematic datasets list."""
        if view_mode != "problematic":
            raise PreventUpdate
        if not options:
            raise PreventUpdate

        values = [o.get("value") for o in options if o.get("value") is not None]
        if not values:
            raise PreventUpdate

        ctx = dash.callback_context
        if not ctx.triggered:
            raise PreventUpdate
        trigger_id = ctx.triggered[0]["prop_id"]

        try:
            idx = values.index(current_value)
        except ValueError:
            idx = 0

        if trigger_id == "btn-problematic-prev.n_clicks":
            idx = max(0, idx - 1)
        elif trigger_id == "btn-problematic-next.n_clicks":
            idx = min(len(values) - 1, idx + 1)
        else:
            raise PreventUpdate

        if values[idx] == current_value:
            raise PreventUpdate
        return values[idx]

    @app.callback(
        Output("action-feedback", "children", allow_duplicate=True),
        Input("btn-mark-delete", "n_clicks"),
        State("current-folder", "data"),
        State("current-file", "data"),
        State("view-mode", "value"),
        prevent_initial_call=True,
    )
    def mark_should_delete(n_clicks, folder, filename, view_mode):
        """Mark current problematic dataset as should-delete in review_progress."""
        if not n_clicks or not folder or not filename:
            raise PreventUpdate
        if view_mode != "problematic":
            raise PreventUpdate

        progress.mark_delete(
            folder,
            filename,
            reason="user_marked_should_delete",
        )
        return html.Div(
            f"🗑 Marked {folder}/{filename} as 'should delete' (review_progress only).",
            style={"color": "#856404", "fontWeight": "bold"},
        )

    # ====================================================================
    # Detect & report too-short profiles (merged button)
    # ====================================================================

    @app.callback(
        Output("too-short-feedback", "children"),
        Input("btn-detect-too-short", "n_clicks"),
        prevent_initial_call=True,
    )
    def detect_and_report_too_short(n_clicks):
        """Scan all cruise folders; flag too-short profiles in review_progress."""
        if not n_clicks:
            raise PreventUpdate

        rows = _collect_too_short_profiles(input_root, navigator)
        newly_added = 0
        for row in rows:
            folder, fn, dp = row["folder"], row["filename"], row["data_points"]
            if progress.get_status(folder, fn) in ("reported", "problematic"):
                continue
            record = SuspiciousCastTracker.build_too_short_record(folder, fn, dp)
            progress.mark_reported(
                folder, fn, dp, "auto_too_short", report_detail=record
            )
            newly_added += 1

        if not rows:
            return html.Div(
                "No too-short profiles found.",
                style={"color": "green", "fontWeight": "bold"},
            )

        summary_items = [
            html.Div(
                f"Found {len(rows)} too-short profile(s); {newly_added} newly reported.",
                style={"fontWeight": "bold", "marginBottom": "4px"},
            ),
        ]
        for row in rows:
            summary_items.append(
                html.Div(
                    f"  {row['folder']}/{row['filename']} ({row['data_points']} pts)",
                    style={"fontSize": "10px", "color": "#555"},
                )
            )
        return html.Div(summary_items)

    @app.callback(
        Output("export-jsonl-feedback", "children"),
        Input("btn-export-suspicious-jsonl", "n_clicks"),
        prevent_initial_call=True,
    )
    def export_problematic_jsonl(n_clicks):
        if not n_clicks:
            raise PreventUpdate
        n_lines = export_suspicious_jsonl_from_progress(progress, _export_jsonl)
        return f"Wrote {n_lines} line(s) to {_export_jsonl}"

    @app.callback(
        Output("batch-apply-feedback", "children"),
        Input("btn-apply-batch-upload", "n_clicks"),
        State("batch-json-upload", "contents"),
        State("batch-json-upload", "filename"),
        prevent_initial_call=True,
    )
    def apply_batch_from_upload(n_clicks, contents, upload_filename):
        if not n_clicks or not contents:
            raise PreventUpdate
        try:
            _meta, b64 = contents.split(",", 1)
            raw = base64.b64decode(b64)
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError, OSError) as e:
            return html.Div(
                f"✗ Could not parse uploaded JSON: {e}",
                style={"color": "red", "fontWeight": "bold"},
            )
        result = apply_batch_from_progress_data(
            data, input_root, output_root, progress
        )
        if not result.get("ok"):
            return html.Div(
                f"✗ {result.get('error', 'Invalid batch structure')}",
                style={"color": "red", "fontWeight": "bold"},
            )
        c = result["counts"]
        parts = [
            f"accepted_ok={c['accepted_ok']}",
            f"accepted_fail={c['accepted_fail']}",
            f"deleted_output={c['deleted_output']}",
            f"skipped_status={c['skipped_status']}",
        ]
        msg = f"✓ Batch from {upload_filename or 'upload'}: " + ", ".join(parts)
        if c.get("errors"):
            msg += " | " + "; ".join(c["errors"][:8])
        return html.Div(msg, style={"color": "#198754", "fontWeight": "bold"})

    return app


def run_apply_batch_cli(
    batch_json: Path,
    input_root: Path,
    output_root: Path,
    progress_json: Path,
) -> int:
    """Load batch JSON from path, apply, print summary. Returns exit code."""
    with open(batch_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    progress = ProgressTracker(progress_json)
    result = apply_batch_from_progress_data(
        data, input_root, output_root, progress
    )
    if not result.get("ok"):
        print(result.get("error", "Invalid batch"))
        return 1
    c = result["counts"]
    print(
        f"accepted_ok={c['accepted_ok']} accepted_fail={c['accepted_fail']} "
        f"deleted_output={c['deleted_output']} skipped_status={c['skipped_status']}"
    )
    for err in c.get("errors", []):
        print(err)
    return 0 if c["accepted_fail"] == 0 else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Interactive Dash app for reviewing CTD soak removal."
    )
    ap.add_argument(
        "--input-root",
        type=str,
        required=True,
        help="Input root (original NetCDFs)",
    )
    ap.add_argument(
        "--output-root",
        type=str,
        required=True,
        help="Output root (soak-removed NetCDFs)",
    )
    ap.add_argument(
        "--progress-json",
        type=str,
        help="Progress tracking JSON (default: OUTPUT_ROOT/review_progress.json)",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=8050,
        help="Port to run Dash server (default: 8050)",
    )
    ap.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1)",
    )
    ap.add_argument(
        "--apply-batch",
        type=str,
        default=None,
        metavar="BATCH_JSON",
        help="Apply trim/delete from this JSON file and exit (no web server).",
    )
    ap.add_argument(
        "--suspicious-export-jsonl",
        type=str,
        default=None,
        help=(
            "Path for 'Export problematic → JSONL' in the app "
            "(default: OUTPUT_ROOT/suspicious_casts.jsonl)"
        ),
    )
    args = ap.parse_args(argv)

    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    progress_json = (
        Path(args.progress_json).resolve()
        if args.progress_json
        else output_root / "review_progress.json"
    )
    suspicious_export = (
        Path(args.suspicious_export_jsonl).resolve()
        if args.suspicious_export_jsonl
        else output_root / "suspicious_casts.jsonl"
    )

    if not input_root.is_dir():
        print(f"Input root not found: {input_root}")
        return 1
    if output_root.is_relative_to(input_root):
        print("Soak output root must be outside the input root")
        return 1

    if args.apply_batch:
        batch_path = Path(args.apply_batch).resolve()
        if not batch_path.is_file():
            print(f"Batch JSON not found: {batch_path}")
            return 1
        return run_apply_batch_cli(
            batch_path, input_root, output_root, progress_json
        )

    print("=" * 80)
    print("CTD Soak Removal Interactive Review")
    print("=" * 80)
    print(f"Input root:    {input_root}")
    print(f"Output root:   {output_root}")
    print(f"Progress JSON: {progress_json}")
    print(f"Server:        http://{args.host}:{args.port}")
    print("=" * 80)

    app = create_app(
        input_root,
        output_root,
        progress_json,
        suspicious_export_jsonl=suspicious_export,
    )
    app.run(host=args.host, port=args.port, debug=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
