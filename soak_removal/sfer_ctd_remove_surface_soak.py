"""
Batch remove CTD surface soak from SFER_CTD NetCDF profile files.

This script applies the soak detection logic in ``soak_removal/soak_detection_util.py``
(classification + ``get_soak_removal_index``).

It reads each NetCDF file under an input root (default: datasets/SFER_CTD),
drops all scans before depth first reaches 2 m, then runs soak detection on
the remainder (descent / shallow / fallback), trims the end at the first
global maximum depth (same rule as the review app markers), and writes a
trimmed NetCDF to an output root while preserving the original directory structure.

Typical usage (from repo root):

  source venv/bin/activate
  python soak_removal/sfer_ctd_remove_surface_soak.py

Defaults read ``datasets/SFER_CTD`` and write ``datasets/SFER_CTD_SOAK_REMOVED``.
Override with ``--input-root`` / ``--output-root`` if needed.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from nc_util import guess_scan_dim, safe_depth_1d, sanitize_encodings_for_netcdf
from soak_detection_util import get_soak_removal_index

_SOAK_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SOAK_DIR.parent
_DEFAULT_INPUT = _REPO_ROOT / "datasets" / "SFER_CTD"
_DEFAULT_OUTPUT = _REPO_ROOT / "datasets" / "SFER_CTD_SOAK_REMOVED"


@dataclass
class ProcessResult:
    input_file: str
    output_file: str | None
    ok: bool
    error: str | None = None

    total_scans: int | None = None
    keep_from_idx: int | None = None
    kept_scans: int | None = None
    station_type: str | None = None
    method: str | None = None
    detected: bool | None = None
    fallback_reason: str | None = None
    pre_depth_trim_idx: int | None = None
    post_pretrim_keep_from_idx: int | None = None
    keep_until_idx: int | None = None


def process_one_file(
    input_path: Path,
    output_path: Path,
    *,
    overwrite: bool,
    dry_run: bool,
    verbose: bool,
) -> ProcessResult:
    res = ProcessResult(
        input_file=str(input_path),
        output_file=None if dry_run else str(output_path),
        ok=False,
    )

    try:
        if (not overwrite) and (not dry_run) and output_path.exists():
            res.ok = True
            return res

        ds = xr.open_dataset(input_path)
        try:
            scan_dim = guess_scan_dim(ds)
            depth_1d = safe_depth_1d(ds)

            res.total_scans = int(depth_1d.size)
            n = int(ds.dims[scan_dim])

            soak = get_soak_removal_index(depth_1d, verbose=verbose)
            keep_from_idx = int(soak["keep_from_idx"])
            keep_until_idx = int(soak.get("keep_until_idx", n))

            # Guardrails
            keep_from_idx = max(0, min(keep_from_idx, n))
            keep_until_idx = max(keep_from_idx + 1, min(keep_until_idx, n))

            res.station_type = soak.get("station_type")
            res.keep_from_idx = keep_from_idx
            res.keep_until_idx = keep_until_idx
            res.kept_scans = int(keep_until_idx - keep_from_idx)
            res.method = str(soak.get("method"))
            res.detected = bool(soak.get("detected"))
            res.fallback_reason = soak.get("fallback_reason")
            res.pre_depth_trim_idx = soak.get("pre_depth_trim_idx")
            res.post_pretrim_keep_from_idx = soak.get("post_pretrim_keep_from_idx")

            if dry_run:
                res.ok = True
                return res

            trimmed = ds.isel({scan_dim: slice(keep_from_idx, keep_until_idx)})
            trimmed = sanitize_encodings_for_netcdf(trimmed)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Keep attributes; write in a portable way.
            trimmed.to_netcdf(output_path, engine="netcdf4")
            res.ok = True
            return res
        finally:
            ds.close()
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
        return res


def iter_input_files(input_root: Path) -> list[Path]:
    return sorted(p for p in input_root.rglob("*.nc") if p.is_file())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Remove CTD surface soak from SFER_CTD NetCDF profiles.")
    ap.add_argument(
        "--input-root",
        type=str,
        default=str(_DEFAULT_INPUT),
        help=f"Input root folder containing cruise subfolders (default: {_DEFAULT_INPUT})",
    )
    ap.add_argument(
        "--output-root",
        type=str,
        default=str(_DEFAULT_OUTPUT),
        help=f"Output root folder for trimmed NetCDFs (default: {_DEFAULT_OUTPUT})",
    )
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    ap.add_argument("--dry-run", action="store_true", help="Analyze only; do not write outputs")
    ap.add_argument("--limit", type=int, default=0, help="Process only first N files (0 = all)")
    ap.add_argument("--verbose", action="store_true", help="Print verbose detection debug for each file")
    ap.add_argument(
        "--report-jsonl",
        type=str,
        default="",
        help="Optional path to write a JSON Lines report (one record per file)",
    )
    args = ap.parse_args(argv)

    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()

    if not input_root.is_dir():
        print(f"Input root is not a directory or does not exist: {input_root}")
        return 1

    files = iter_input_files(input_root)
    if args.limit and args.limit > 0:
        files = files[: args.limit]

    report_fp = Path(args.report_jsonl) if args.report_jsonl else None
    report_fh = None
    if report_fp and (not args.dry_run):
        report_fp.parent.mkdir(parents=True, exist_ok=True)
        report_fh = report_fp.open("w", encoding="utf-8")

    ok = 0
    fail = 0

    try:
        for i, in_file in enumerate(files, start=1):
            rel = in_file.relative_to(input_root)
            out_file = output_root / rel

            res = process_one_file(
                in_file,
                out_file,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )

            if res.ok:
                ok += 1
            else:
                fail += 1

            if report_fh:
                report_fh.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")

            if (i <= 5) or (i % 250 == 0) or (i == len(files)):
                msg = f"[{i}/{len(files)}] ok={ok} fail={fail} last={in_file.name} keep_from_idx={res.keep_from_idx} method={res.method}"
                if not res.ok and res.error:
                    msg += f" error={res.error}"
                print(msg)

    finally:
        if report_fh:
            report_fh.close()

    print(f"Done. ok={ok}, fail={fail}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

