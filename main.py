"""
Main entry point for CNV conversion, soak removal, QC, visualization, and ERDDAP XML generation.

Usage:
    python main.py inspect-cnv INPUT --output cnv_mapping.json
    python main.py convert-cnv INPUT --prepare-profile
    python main.py convert-cnv INPUT --profile PROFILE
    python main.py remove-soak --profile PROFILE [--input-root INPUT] [--output-root OUTPUT]
    python main.py review-soak --profile PROFILE [--input-root INPUT] [--output-root OUTPUT]
    python main.py generate-sensor-config --profile PROFILE
    python main.py qc --profile PROFILE [--input-root INPUT]
    python main.py erddap-xml --profile PROFILE
    python main.py viz --profile PROFILE
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from dataset_profile import (
    DEFAULT_PROFILE_PATH,
    load_dataset_profile,
    resolve_config_path,
)
from qc_runner import run_qc_for_all
from xml_generator.generate import generate_erddap_xml_for_profile

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False, log_file: str | None = None) -> None:
    """Configure logging for the QC pipeline."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
    )


def _add_qc_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        type=str,
        default=str(DEFAULT_PROFILE_PATH),
        help=f"Path to dataset profile JSON (default: '{DEFAULT_PROFILE_PATH}')",
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        help="Override the profile data_root for this QC run; use the same dataset configuration",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (debug) logging",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to log file (in addition to console output)",
    )


def _add_viz_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        type=str,
        default=str(DEFAULT_PROFILE_PATH),
        help=f"Path to dataset profile JSON (default: '{DEFAULT_PROFILE_PATH}')",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Dash server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8050,
        help="Dash server port (default: 8050)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run Dash in debug mode",
    )


def _add_convert_cnv_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input",
        type=Path,
        help="One CNV file, one cruise folder, or a root containing cruise folders",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Dataset profile (default: discover/create config/<dataset>/dataset_profile.json)",
    )
    parser.add_argument(
        "--prepare-profile",
        action="store_true",
        help="Create missing dataset profile and CNV mapping, then exit without converting; preserve existing configuration",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Atomically replace existing converted netCDF files",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (debug) logging",
    )


def _add_soak_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        type=str,
        default=str(DEFAULT_PROFILE_PATH),
        help=f"Dataset profile for the input data_root (default: '{DEFAULT_PROFILE_PATH}')",
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        help="Override the profile data_root with a NetCDF root containing cruise folders",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help=(
            "Override the soak output root (default: a sibling of profile data_root, "
            "replacing a trailing _CNV with _SOAK_REMOVED or appending _SOAK_REMOVED; "
            "an input subfolder is mirrored beneath it)"
        ),
    )


def _add_remove_soak_arguments(parser: argparse.ArgumentParser) -> None:
    _add_soak_paths(parser)
    parser.add_argument("--dry-run", action="store_true", help="Analyze casts without writing NetCDF files")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing soak-removed files")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N files (0 = all)")
    parser.add_argument("--verbose", action="store_true", help="Print detection details for each cast")
    parser.add_argument("--report-jsonl", type=Path, help="Write one result per file (not written during dry runs)")


def _add_review_soak_arguments(parser: argparse.ArgumentParser) -> None:
    _add_soak_paths(parser)
    parser.add_argument("--progress-json", type=Path, help="Review state (default: OUTPUT_ROOT/review_progress.json)")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8050, help="Dashboard port (default: 8050)")


def _add_erddap_xml_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        type=str,
        default=str(DEFAULT_PROFILE_PATH),
        help=f"Path to dataset profile JSON (default: '{DEFAULT_PROFILE_PATH}')",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (debug) logging",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SFER CTD CNV conversion, QC, and ERDDAP datasets.xml generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py convert-cnv /path/to/cnv/files
    python main.py remove-soak --profile config/your_dataset/dataset_profile.json --dry-run
    python main.py review-soak --profile config/your_dataset/dataset_profile.json
    python main.py qc --profile config/your_dataset/dataset_profile.json
    python main.py erddap-xml --profile config/your_dataset/dataset_profile.json
    python main.py viz
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    convert_parser = sub.add_parser(
        "convert-cnv",
        help="Convert NOAA AOML Sea-Bird CNV casts to standardized netCDF profiles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_convert_cnv_arguments(convert_parser)
    soak_parser = sub.add_parser(
        "remove-soak",
        help="Remove surface soak from converted NetCDF files into a separate folder",
    )
    _add_remove_soak_arguments(soak_parser)
    review_parser = sub.add_parser(
        "review-soak",
        help="Open the interactive surface soak review dashboard",
    )
    _add_review_soak_arguments(review_parser)
    inspect_parser = sub.add_parser(
        "inspect-cnv",
        help="Inspect CNV headers and create one comment-derived mapping",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    inspect_parser.add_argument(
        "input",
        type=Path,
        help="One CNV file, one cruise folder, or a root containing cruise folders",
    )
    inspect_parser.add_argument("--output", type=Path, default=None)
    inspect_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (debug) logging",
    )

    sensor_parser = sub.add_parser(
        "generate-sensor-config",
        help="Generate null-limit sensor specs and variable links from converted NetCDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sensor_parser.add_argument(
        "--profile",
        type=str,
        required=True,
        help="Path to the dataset profile JSON",
    )
    sensor_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing reviewed sensor configuration",
    )
    sensor_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (debug) logging",
    )

    qc_parser = sub.add_parser(
        "qc",
        help="Run QARTOD QC on NetCDF files under each cruise directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_qc_arguments(qc_parser)

    limits_parser = sub.add_parser("generate-limits", help="Initialize dataset-owned settings from the Walton Smith template")
    limits_parser.add_argument("--profile", required=True)
    limits_parser.add_argument("--output", type=Path, default=None, help="Optional new dataset folder; defaults beside the profile; existing settings are preserved")

    erddap_parser = sub.add_parser(
        "erddap-xml",
        help="Generate ERDDAP datasets.xml from NetCDF files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_erddap_xml_arguments(erddap_parser)

    viz_parser = sub.add_parser(
        "viz",
        help="Launch a local Dash viewer for QC NetCDF outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_viz_arguments(viz_parser)

    return parser.parse_args(argv)


def _run_convert_cnv(args: argparse.Namespace) -> None:
    from cnv_converter import convert_cnv

    setup_logging(verbose=args.verbose)
    if not args.input.exists():
        logger.error("CNV input does not exist: %s", args.input)
        raise SystemExit(1)
    if args.profile is None or args.prepare_profile:
        from cnv_workflow import prepare_profile
        args.profile = str(prepare_profile(args.input, profile_path=args.profile))
    profile = load_dataset_profile(args.profile)
    mapping_path = resolve_config_path("cnv_mapping", profile)
    if not mapping_path.is_file():
        logger.error("CNV mapping does not exist: %s", mapping_path)
        raise SystemExit(1)

    if args.prepare_profile:
        logger.info("Dataset profile ready: %s", profile.profile_path)
        logger.info("CNV mapping: %s", mapping_path)
        logger.info("Review the profile and mapping, then rerun convert-cnv without --prepare-profile.")
        return

    logger.info("Converting Sea-Bird CNV casts...")
    logger.info("  Dataset profile:   %s", Path(args.profile).absolute())
    logger.info("  Input:             %s", args.input.absolute())
    logger.info("  Output directory:  %s", profile.data_root.absolute())
    try:
        report = convert_cnv(
            args.input,
            profile.data_root,
            mapping_path,
            args.report,
            args.overwrite,
            netcdf_global_attributes=profile.netcdf_global_attributes,
            netcdf_fixed_global_attributes=profile.netcdf_fixed_global_attributes,
            netcdf_derived_global_attributes=profile.netcdf_derived_global_attributes,
        )
    except KeyboardInterrupt:
        logger.warning("CNV conversion interrupted by user.")
        raise SystemExit(1)
    except Exception as exc:
        logger.exception("CNV conversion failed: %s", exc)
        raise SystemExit(1)

    counts = report["counts"]
    logger.info(
        "CNV conversion complete: discovered=%s converted=%s failed=%s",
        counts["cnv_discovered"],
        counts["converted"],
        counts["failed"],
    )
    logger.info(
        "Conversion report: %s",
        args.report or profile.data_root / "conversion_report.json",
    )
    if counts["converted"]:
        from qc_limit_generation import prepare_dataset_qc
        prepared = prepare_dataset_qc(profile)
        logger.info("Dataset-owned QC settings: %s", prepared.paths.sensor_specs)
    if counts["failed"]:
        raise SystemExit(1)


def _soak_roots(args: argparse.Namespace) -> tuple[Path, Path]:
    profile = load_dataset_profile(args.profile)
    input_root = (args.input_root or profile.data_root).expanduser()
    if not input_root.is_dir():
        raise SystemExit(f"Soak input root is not a directory: {input_root}")
    if args.output_root is not None:
        output_root = args.output_root.expanduser()
    else:
        profile_root = profile.data_root
        output_root = profile_root.with_name(
            f"{profile_root.name.removesuffix('_CNV')}_SOAK_REMOVED"
        )
        if input_root.resolve().is_relative_to(profile_root.resolve()):
            output_root /= input_root.resolve().relative_to(profile_root.resolve())
    if output_root.resolve().is_relative_to(input_root.resolve()):
        raise SystemExit("Soak output root must be outside the input root")
    print(f"Soak input root:  {input_root.resolve()}")
    print(f"Soak output root: {output_root.resolve()}", flush=True)
    return input_root, output_root


def _run_remove_soak(args: argparse.Namespace) -> None:
    input_root, output_root = _soak_roots(args)
    script = Path(__file__).resolve().parent / "soak_removal" / "sfer_ctd_remove_surface_soak.py"
    command = [
        sys.executable, str(script),
        "--input-root", str(input_root),
        "--output-root", str(output_root),
    ]
    if args.dry_run:
        command.append("--dry-run")
    if args.overwrite:
        command.append("--overwrite")
    if args.limit:
        command.extend(("--limit", str(args.limit)))
    if args.verbose:
        command.append("--verbose")
    if args.report_jsonl is not None:
        command.extend(("--report-jsonl", str(args.report_jsonl)))
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def _run_review_soak(args: argparse.Namespace) -> None:
    input_root, output_root = _soak_roots(args)
    script = Path(__file__).resolve().parent / "soak_removal" / "review_app.py"
    progress_json = args.progress_json or output_root / "review_progress.json"
    command = [
        sys.executable, str(script),
        "--input-root", str(input_root),
        "--output-root", str(output_root),
        "--progress-json", str(progress_json),
        "--host", args.host,
        "--port", str(args.port),
        "--suspicious-export-jsonl", str(output_root / "suspicious_casts.jsonl"),
    ]
    try:
        result = subprocess.run(command, check=False)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    if result.returncode:
        raise SystemExit(result.returncode)


def _run_inspect_cnv(args: argparse.Namespace) -> None:
    from cnv_mapping import inspect_cnv

    setup_logging(verbose=args.verbose)
    if args.output is None:
        from cnv_workflow import default_mapping_path
        args.output = default_mapping_path(args.input)
    try:
        mapping = inspect_cnv(args.input, args.output)
    except KeyboardInterrupt:
        logger.warning("CNV inspection interrupted by user.")
        raise SystemExit(1)
    except Exception as exc:
        logger.exception("CNV inspection failed: %s", exc)
        raise SystemExit(1)
    inspection = mapping["inspection"]
    logger.info(
        "CNV inspection complete: discovered=%s inspected=%s failed=%s",
        inspection["cnv_file_count"],
        inspection["inspected_file_count"],
        len(inspection["failed_files"]),
    )
    logger.info("Catalog-assisted mapping: %s", args.output)
    logger.info("Mapping entries requiring review: %s", sum(item["mapped_to"] is None for item in mapping["science_variables"].values()))
    logger.info("Files needing coordinate preparation before conversion: %s", sum("source" in issue for issue in inspection["conversion_issues"]))
    if inspection["failed_files"]:
        raise SystemExit(1)


def _run_generate_sensor_config(args: argparse.Namespace) -> None:
    from cnv_sensor_config import generate_sensor_configs

    setup_logging(verbose=args.verbose)
    profile = load_dataset_profile(args.profile)
    try:
        report = generate_sensor_configs(profile, overwrite=args.overwrite)
    except Exception as exc:
        logger.exception("Sensor configuration generation failed: %s", exc)
        raise SystemExit(1)
    logger.info(
        "Sensor configuration complete: files=%s variables=%s sensors=%s",
        report["netcdf_file_count"],
        report["qc_variable_count"],
        report["sensor_count"],
    )
    logger.info("Sensor specs:        %s", report["sensor_specs"])
    logger.info("Variable sensor map: %s", report["variable_sensor_map"])
    if report["variables_without_units"]:
        logger.warning(
            "Variables without units have empty range tables: %s",
            ", ".join(report["variables_without_units"]),
        )


def _run_qc(args: argparse.Namespace) -> None:
    setup_logging(verbose=args.verbose, log_file=args.log_file)

    profile = load_dataset_profile(args.profile)
    base_dir = args.input_root.expanduser() if args.input_root is not None else profile.data_root
    if not base_dir.exists():
        logger.error("Base directory does not exist: %s", base_dir)
        sys.exit(1)
    if not base_dir.is_dir():
        logger.error("Base path is not a directory: %s", base_dir)
        sys.exit(1)

    logger.info("Starting QC pipeline...")
    logger.info("  Dataset profile:          %s", Path(args.profile).absolute())
    logger.info("  Base directory:            %s", base_dir.absolute())
    logger.info("  Output mode:               %s", profile.output.mode)
    logger.info("  Output directory:          %s", profile.output.directory.absolute())
    logger.info("  Sample dimension:          %s", profile.metadata.sample_dimension)

    try:
        summary = run_qc_for_all(
            base_dir=base_dir,
            profile=profile,
        )
        logger.info("QC pipeline completed successfully!")
        logger.info("QC run manifest: %s", summary.manifest_path.absolute())
    except KeyboardInterrupt:
        logger.warning("QC pipeline interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logger.exception("QC pipeline failed: %s", e)
        sys.exit(1)


def _run_erddap_xml(args: argparse.Namespace) -> None:
    setup_logging(verbose=args.verbose)
    profile = load_dataset_profile(args.profile)
    try:
        generate_erddap_xml_for_profile(profile, verbose=args.verbose)
    except KeyboardInterrupt:
        logging.getLogger("xml_generator").warning("Interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logging.getLogger("xml_generator").exception("ERDDAP XML generation failed: %s", e)
        sys.exit(1)


def _run_viz(args: argparse.Namespace) -> None:
    profile = load_dataset_profile(args.profile)
    try:
        from qc_dashboard import default_viz_data_root, run_dashboard

        resolved_root = default_viz_data_root(profile)
        print(f"Launching QC viewer for {resolved_root.absolute()}")
        print(f"Open http://{args.host}:{args.port} in your browser")
        run_dashboard(
            data_root=resolved_root,
            profile=profile,
            host=args.host,
            port=args.port,
            debug=args.debug,
        )
    except KeyboardInterrupt:
        logging.getLogger("qc_dashboard").warning("QC viewer interrupted by user.")
        sys.exit(1)
    except ImportError as e:
        logging.getLogger("qc_dashboard").error(
            "Dash viewer dependencies are missing. Install requirements.txt first: %s",
            e,
        )
        sys.exit(1)
    except Exception as e:
        logging.getLogger("qc_dashboard").exception("QC viewer failed: %s", e)
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command == "convert-cnv":
        _run_convert_cnv(args)
    elif args.command == "remove-soak":
        _run_remove_soak(args)
    elif args.command == "review-soak":
        _run_review_soak(args)
    elif args.command == "inspect-cnv":
        _run_inspect_cnv(args)
    elif args.command == "generate-sensor-config":
        _run_generate_sensor_config(args)
    elif args.command == "generate-limits":
        from qc_limit_generation import generate_limits
        report = generate_limits(load_dataset_profile(args.profile), args.output)
        print(f"QC profile: {report['profile']} (use qc --profile with this path)")
    elif args.command == "qc":
        _run_qc(args)
    elif args.command == "erddap-xml":
        _run_erddap_xml(args)
    elif args.command == "viz":
        _run_viz(args)
    else:
        raise RuntimeError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
