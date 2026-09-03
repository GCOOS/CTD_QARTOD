"""
Main entry point for CNV conversion, QC, visualization, and ERDDAP XML generation.

Usage:
    python main.py inspect-cnv INPUT --output cnv_mapping.json
    python main.py convert-cnv INPUT --profile PROFILE
    python main.py generate-sensor-config --profile PROFILE
    python main.py qc --profile PROFILE
    python main.py erddap-xml --profile PROFILE
    python main.py viz --profile PROFILE
"""

from __future__ import annotations

import argparse
import logging
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
        required=True,
        help="Dataset profile containing the mapping, output root, and global metadata",
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
    python main.py inspect-cnv cnv_data/WS24258 --output config/walton_smith/cnv_mapping.json
    python main.py convert-cnv cnv_data/WS24258 --profile config/ws24258/dataset_profile.json
    python main.py generate-sensor-config --profile config/walton_smith/dataset_profile.json
    python main.py qc
    python main.py qc --profile config/walton_smith/dataset_profile.json
    python main.py erddap-xml --profile config/hogarth_cnv/dataset_profile.json
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
    inspect_parser.add_argument("--output", type=Path, required=True)
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
    profile = load_dataset_profile(args.profile)
    mapping_path = resolve_config_path("cnv_mapping", profile)
    if not mapping_path.is_file():
        logger.error("CNV mapping does not exist: %s", mapping_path)
        raise SystemExit(1)

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
    if counts["failed"]:
        raise SystemExit(1)


def _run_inspect_cnv(args: argparse.Namespace) -> None:
    from cnv_mapping import inspect_cnv

    setup_logging(verbose=args.verbose)
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
    logger.info("Comment-derived mapping: %s", args.output)


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
    base_dir = profile.data_root
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
    elif args.command == "inspect-cnv":
        _run_inspect_cnv(args)
    elif args.command == "generate-sensor-config":
        _run_generate_sensor_config(args)
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
