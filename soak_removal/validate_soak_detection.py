"""
Validation Tool for Surface Soak Detection

Randomly selects files from specified cast types, runs the detection algorithm,
and visualizes the results in a grid; or plot explicit paths via ``--file``.

Usage:
    python validate_soak_detection.py --cast_types shallow_stable shallow_cast deep_cast --n_files 6
    python validate_soak_detection.py --file datasets/SFER_CTD/WS0603/WS0603_1.nc
    python validate_soak_detection.py --file a.nc b.nc
"""

import os
import random
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import argparse
import warnings
from pathlib import Path
warnings.filterwarnings('ignore')

# Import V4 detection + classification
import sys
_SOAK_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SOAK_DIR.parent
sys.path.insert(0, str(_SOAK_DIR))
from soak_detection_util import (
    PRE_SOAK_DEPTH_THRESHOLD_M,
    classify_station,
    detect_descent_start_v4,
    first_index_depth_at_least,
    get_soak_removal_index,
)

BASE_PATH = str(_REPO_ROOT / "datasets" / "SFER_CTD")
ANALYSIS_PATH = str(_SOAK_DIR)


def iter_nc_files(base_path: str):
    """Yield (cruise_folder_name, filename, full_path) for all NetCDF files."""
    for cruise in sorted(os.listdir(base_path)):
        cruise_path = os.path.join(base_path, cruise)
        if not os.path.isdir(cruise_path) or cruise.startswith("."):
            continue
        for filename in sorted(os.listdir(cruise_path)):
            if filename.endswith(".nc"):
                yield cruise, filename, os.path.join(cruise_path, filename)


def analyze_nc_file_for_validation(cruise: str, filename: str, file_path: str) -> dict | None:
    """
    Load depth, classify station (V4), run V4 descent detection.
    Returns a file_info dict compatible with visualize_file(), or None if invalid.
    """
    try:
        ds = xr.open_dataset(file_path)
        depth_data = ds.coords["depth"].values.flatten()
        ds.close()
    except Exception:
        return None

    depth_data = depth_data[~np.isnan(depth_data)]
    if len(depth_data) < 200:
        return None

    pre_idx = first_index_depth_at_least(depth_data, PRE_SOAK_DEPTH_THRESHOLD_M)
    trimmed = depth_data[pre_idx:]
    if len(trimmed) < 50:
        return None

    depth_range = float(np.max(trimmed) - np.min(trimmed))
    station_type = classify_station(trimmed)
    detection = detect_descent_start_v4(trimmed)

    return {
        "cruise": cruise,
        "file": filename,
        "cast_type": station_type,
        "total_scans": int(len(depth_data)),
        "depth_range": depth_range,
        "descent_detected": bool(detection.get("detected", False)),
        "descent_start_idx": detection.get("descent_start_idx"),
        "no_detection_reason": detection.get("reason"),
        "detection_method": detection.get("method"),
    }


def _resolve_user_nc_path(path: str) -> Path | None:
    """
    Resolve a user-supplied path to an existing file.

    Relative paths are tried against the current working directory first, then
    against the repository root (parent of ``soak_removal/``). That way
    ``datasets/SFER_CTD/...`` works when you run the script from inside
    ``soak_removal/`` as well as from the repo root.
    """
    raw = Path(path).expanduser()
    if raw.is_absolute():
        cand = raw.resolve()
        return cand if cand.is_file() else None
    for base in (Path.cwd(), _REPO_ROOT):
        cand = (base / raw).resolve()
        if cand.is_file():
            return cand
    return None


def build_file_info_from_disk_path(path: str) -> dict | None:
    """
    Build a ``file_info`` dict for an arbitrary ``.nc`` path (for ``--file``).
    Adds ``full_path`` so ``visualize_file`` does not require ``BASE_PATH``.
    """
    p = _resolve_user_nc_path(path)
    if p is None:
        return None
    if p.suffix.lower() != ".nc":
        return None
    cruise = p.parent.name
    info = analyze_nc_file_for_validation(cruise, p.name, str(p))
    if info is None:
        return None
    info["full_path"] = str(p)
    return info


def sample_files_by_type_dynamic(cast_types, n_files_total=6, seed=None):
    """
    Sample files by cast type, computing cast type dynamically using V4.
    This supports the 1.5–3m transition zone (classification depends on detection).
    
    Args:
        cast_types: List of cast types to sample from (e.g., ['shallow_stable', 'deep_cast'])
        n_files_total: Total number of files to return
        seed: Random seed for reproducibility
    
    Returns:
        List of selected file dictionaries
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    cast_types = list(cast_types)
    if not cast_types:
        return []

    # Distribute counts across requested cast types
    n_types = len(cast_types)
    n_per_type = n_files_total // n_types
    remainder = n_files_total % n_types
    target_per_type = {t: n_per_type for t in cast_types}
    for t in cast_types[:remainder]:
        target_per_type[t] += 1

    selected_by_type: dict[str, list[dict]] = {t: [] for t in cast_types}

    # Shuffle file order so sampling is random but bounded
    all_files = list(iter_nc_files(BASE_PATH))
    random.shuffle(all_files)

    for cruise, filename, path in all_files:
        # Stop early if all targets met
        if all(len(selected_by_type[t]) >= target_per_type[t] for t in cast_types):
            break

        info = analyze_nc_file_for_validation(cruise, filename, path)
        if not info:
            continue

        # Filter to files that have enough scans for meaningful visualization
        if info.get("total_scans", 0) <= 500:
            continue

        t = info.get("cast_type")
        if t in target_per_type and len(selected_by_type[t]) < target_per_type[t]:
            selected_by_type[t].append(info)

    # Flatten in requested cast_types order
    selected = []
    for t in cast_types:
        selected.extend(selected_by_type[t])

    # If we couldn't fill all buckets, top-up with any valid files of requested types
    if len(selected) < n_files_total:
        for cruise, filename, path in all_files:
            if len(selected) >= n_files_total:
                break
            info = analyze_nc_file_for_validation(cruise, filename, path)
            if not info:
                continue
            if info.get("total_scans", 0) <= 500:
                continue
            if info.get("cast_type") in cast_types and info not in selected:
                selected.append(info)

    return selected[:n_files_total]


def visualize_file(file_info, ax, show_details=True):
    """
    Visualize a single file's depth profile with detection results.
    
    Args:
        file_info: Dictionary with file information
        ax: Matplotlib axis to plot on
        show_details: Whether to show detailed annotations
    """
    file_path = file_info.get("full_path") or os.path.join(
        BASE_PATH, file_info["cruise"], file_info["file"]
    )

    if not os.path.exists(file_path):
        ax.text(0.5, 0.5, f"File not found:\n{file_info['file']}", 
                ha='center', va='center', transform=ax.transAxes)
        ax.set_title(f"{file_info['cast_type']}\n{file_info['file']}")
        return
    
    try:
        # Load the NetCDF file
        ds = xr.open_dataset(file_path)
        depth_data = ds.coords['depth'].values.flatten()
        depth_data = depth_data[~np.isnan(depth_data)]
        ds.close()
        
        scans = np.arange(len(depth_data))
        
        # Plot depth profile
        ax.plot(scans, depth_data, 'b-', linewidth=0.8, alpha=0.7, label='Depth')
        ax.invert_yaxis()  # Depth increases downward
        
        # Get detection results
        cast_type = file_info.get("cast_type", "unknown")
        descent_start_idx = file_info.get("descent_start_idx")
        descent_detected = file_info.get("descent_detected", False)
        total_scans = file_info.get("total_scans", len(depth_data))
        depth_range = file_info.get("depth_range", 0)
        
        # Unified soak removal (2 m pre-trim + detection on remainder)
        soak_result = get_soak_removal_index(depth_data)
        pre_trim_idx = int(soak_result.get("pre_depth_trim_idx", 0))
        keep_from_idx = soak_result["keep_from_idx"]
        keep_until_idx = int(soak_result.get("keep_until_idx", len(depth_data)))
        detection_method = soak_result["method"]
        was_detected = soak_result["detected"]

        if pre_trim_idx > 0:
            ax.axvline(
                x=pre_trim_idx,
                color="g",
                linestyle=":",
                linewidth=1.5,
                alpha=0.9,
                label=f"First ≥{PRE_SOAK_DEPTH_THRESHOLD_M:g} m (scan {pre_trim_idx})",
            )

        if was_detected:
            # Show detected descent start
            ax.axvline(x=keep_from_idx, color='r', linestyle='--', 
                      linewidth=2, label=f'Descent: {keep_from_idx} ({detection_method})')
            ax.fill_between(scans[:keep_from_idx], 
                           depth_data[:keep_from_idx], 
                           alpha=0.2, color='yellow', label='Surface soak')
        else:
            # Show fallback (keep last 500)
            ax.axvline(x=keep_from_idx, color='orange', linestyle='--',
                      linewidth=2, label=f'Fallback: keep last 500 (from {keep_from_idx})')
            ax.fill_between(scans[:keep_from_idx],
                           depth_data[:keep_from_idx],
                           alpha=0.2, color='yellow', label='Remove (surface soak)')
            fallback_reason = soak_result.get("fallback_reason", "unknown")
            ax.text(0.5, 0.95, f'Fallback: {fallback_reason}', 
                   transform=ax.transAxes, ha='center', va='top',
                   bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        if keep_until_idx < len(depth_data):
            ax.axvline(
                x=keep_until_idx,
                color="purple",
                linestyle="-",
                linewidth=1.5,
                alpha=0.85,
                label=f"Auto end (first global max depth): {keep_until_idx}",
            )
        
        # Formatting
        ax.set_xlabel('Scan Number', fontsize=9)
        ax.set_ylabel('Depth (m)', fontsize=9)
        
        # Title with key info
        title_parts = [
            f"{cast_type.replace('_', ' ').title()}",
            f"{os.path.basename(file_info['file'])}",
            f"Range: {depth_range:.1f}m, Scans: {total_scans}"
        ]
        if descent_detected and descent_start_idx is not None:
            title_parts.append(f"Soak: {descent_start_idx} scans")
        
        ax.set_title('\n'.join(title_parts), fontsize=8)
        ax.legend(fontsize=7, loc='best')
        ax.grid(True, alpha=0.3)
        
    except Exception as e:
        ax.text(0.5, 0.5, f"Error loading file:\n{str(e)}", 
                ha='center', va='center', transform=ax.transAxes)
        ax.set_title(f"{file_info['cast_type']}\n{file_info['file']}")


def create_validation_plot(selected_files, output_path=None, seed=None, *, explicit_paths: bool = False):
    """
    Grid visualization of one or more file dicts (``file_info``).

    Args:
        selected_files: List of file dictionaries to visualize
        output_path: Path to save the plot (if None, displays)
        seed: Random seed used (for title / filename hint)
        explicit_paths: If True, title notes explicit ``--file`` mode
    """
    n = len(selected_files)
    if n == 0:
        print("No files to plot.")
        return

    if n == 1:
        fig, ax = plt.subplots(1, 1, figsize=(10, 7))
        axes_flat = [ax]
    else:
        ncol = min(3, n)
        nrow = (n + ncol - 1) // ncol
        fig, axes = plt.subplots(nrow, ncol, figsize=(6 * ncol, 4 * nrow))
        axes_flat = np.asarray(axes).ravel()

    for idx, ax in enumerate(axes_flat):
        if idx < n:
            visualize_file(selected_files[idx], ax)
        else:
            ax.set_visible(False)

    cast_types_str = ", ".join(sorted(set(f.get("cast_type", "unknown") for f in selected_files)))
    seed_str = f" (seed={seed})" if seed is not None else ""
    mode = "Explicit files" if explicit_paths else "Cast types"
    fig.suptitle(
        f"Surface Soak Detection Validation — {mode}\n{cast_types_str}{seed_str}",
        fontsize=14,
        fontweight="bold",
    )
    
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved validation plot to: {output_path}")
    else:
        plt.show()
    
    plt.close()


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Validate surface soak detection on sampled or explicit NetCDF files."
    )
    parser.add_argument(
        "--file",
        "--files",
        dest="explicit_files",
        nargs="+",
        default=None,
        metavar="PATH",
        help="One or more .nc paths to plot (skips random sampling). Example: --file a.nc b.nc",
    )
    parser.add_argument(
        '--cast_types', 
        nargs='+',
        choices=['shallow_stable', 'shallow_cast', 'deep_cast'],
        default=['shallow_stable', 'shallow_cast', 'deep_cast'],
        help='Cast types to sample from (default: all; ignored if --file is used)'
    )
    parser.add_argument(
        '--n_files',
        type=int,
        default=6,
        help='Total number of files to visualize (default: 6)'
    )
    parser.add_argument(
        '--seed',
        type=int,
        default=None,
        help='Random seed for reproducibility'
    )
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output file path (default: auto-generated)'
    )
    
    args = parser.parse_args()

    explicit_paths = bool(args.explicit_files)
    if explicit_paths:
        selected = []
        for raw in args.explicit_files:
            info = build_file_info_from_disk_path(raw)
            if info is None:
                p = _resolve_user_nc_path(raw)
                if p is None:
                    print(
                        f"  Skip (not found under cwd={Path.cwd()} or repo={_REPO_ROOT}): {raw}"
                    )
                elif p.suffix.lower() != ".nc":
                    print(f"  Skip (not .nc): {raw}")
                else:
                    print(
                        f"  Skip (load/classify failed or profile too short after filters): {p}"
                    )
                continue
            selected.append(info)
        if not selected:
            print("No valid NetCDF files to plot.")
            raise SystemExit(1)
    else:
        print("Sampling files (dynamic cast types via V4)...")
        selected = sample_files_by_type_dynamic(
            cast_types=args.cast_types,
            n_files_total=args.n_files,
            seed=args.seed,
        )

    print(f"\nSelected {len(selected)} files:")
    for f in selected:
        label = f.get("full_path") or os.path.join(BASE_PATH, f["cruise"], f["file"])
        print(f"  {f['cast_type']}: {label}")

    # Generate output path if not provided
    if args.output is None:
        seed_str = f"_seed{args.seed}" if args.seed is not None else ""
        if explicit_paths:
            if len(selected) == 1:
                stem = Path(selected[0].get("full_path", selected[0]["file"])).stem
                out_name = f"validation_file_{stem}{seed_str}.png"
            else:
                out_name = f"validation_files_{len(selected)}{seed_str}.png"
            args.output = os.path.join(ANALYSIS_PATH, out_name)
        else:
            cast_str = "_".join(args.cast_types)
            args.output = os.path.join(ANALYSIS_PATH, f"validation_{cast_str}{seed_str}.png")

    print(f"\nCreating visualization...")
    create_validation_plot(selected, args.output, args.seed, explicit_paths=explicit_paths)
    
    print(f"\nDone! Visualization saved to: {args.output}")


if __name__ == "__main__":
    main()
