"""
Main entry point for CNV conversion, QC, visualization, and ERDDAP XML sync.

Usage:
    python main.py convert-cnv --input-dir DIR --output-dir DIR ...
    python main.py qc [--base-dir DIR] ...
    python main.py erddap-xml [--input-xml PATH] [--data-root DIR] ...
    python main.py viz [--data-root DIR] ...
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from cnv_converter import convert_cnv_directory
from dataset_profile import (
    DEFAULT_CNV_PROFILE_PATH,
    DEFAULT_PROFILE_PATH,
    load_dataset_profile,
    resolve_config_path,
)
from erddap_xml_sync import (
    add_erddap_xml_arguments,
    resolve_erddap_data_root,
    run_erddap_xml_sync,
    run_erddap_xml_sync_for_profile,
)
from qc_config import load_location_config
from qc_runner import run_qc_for_all

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
        "--base-dir",
        type=str,
        default=None,
        help="Base directory containing cruise directories (overrides profile data_root)",
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
    parser.add_argument(
        "--sync-erddap-xml",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After QC, sync ERDDAP datasets.xml from NetCDF output to output/erddap/ "
            "(default: on; use --no-sync-erddap-xml to skip)"
        ),
    )
    parser.add_argument(
        "--erddap-input-xml",
        type=Path,
        default=None,
        help="Input ERDDAP XML for post-QC sync (default: datasets/mod_CTD_datasets.xml)",
    )
    parser.add_argument(
        "--erddap-output-xml",
        type=Path,
        default=None,
        help="Output ERDDAP XML path (default: output/erddap/mod_CTD_datasets_qc.xml)",
    )
    parser.add_argument(
        "--erddap-filedir-prefix",
        type=str,
        default=None,
        help="fileDir prefix written into synced dataset blocks (default from qc_config)",
    )


def _add_viz_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        type=str,
        default=str(DEFAULT_PROFILE_PATH),
        help=f"Path to dataset profile JSON (default: '{DEFAULT_PROFILE_PATH}')",
    )
    parser.add_argument(
        "--data-root",
        type=str,
        default=None,
        help=(
            "Root containing QC NetCDF cruise directories. Defaults to profile output.directory "
            "when output.mode is duplicate, otherwise profile data_root."
        ),
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
        "--profile",
        type=str,
        default=str(DEFAULT_CNV_PROFILE_PATH),
        help=f"Path to dataset profile JSON (default: '{DEFAULT_CNV_PROFILE_PATH}')",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing one cruise's Sea-Bird CNV and companion files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output root for cruise netCDF files and conversion_report.json",
    )
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SFER CTD CNV conversion, QC, and ERDDAP datasets.xml sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py convert-cnv --profile config/hogarth_cnv/dataset_profile.json --input-dir cnv_data/cruise --output-dir output/SFER_CNV
    python main.py qc
    python main.py qc --profile config/walton_smith/dataset_profile.json
    python main.py qc --base-dir /path/to/datasets/SFER_CTD_SOAK_REMOVED
    python main.py qc --no-sync-erddap-xml
    python main.py erddap-xml --data-root output/SFER_QC
    python main.py viz
    python main.py viz --data-root output/SFER_QC
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    convert_parser = sub.add_parser(
        "convert-cnv",
        help="Convert NOAA AOML Sea-Bird CNV casts to standardized netCDF profiles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_convert_cnv_arguments(convert_parser)

    qc_parser = sub.add_parser(
        "qc",
        help="Run QARTOD QC on NetCDF files under each cruise directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_qc_arguments(qc_parser)

    erddap_parser = sub.add_parser(
        "erddap-xml",
        help="Sync ERDDAP datasets.xml dataVariable blocks from NetCDF files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_erddap_xml_arguments(erddap_parser)

    viz_parser = sub.add_parser(
        "viz",
        help="Launch a local Dash viewer for QC NetCDF outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_viz_arguments(viz_parser)

    return parser.parse_args(argv)


def _run_convert_cnv(args: argparse.Namespace) -> None:
    setup_logging(verbose=args.verbose)
    if not args.input_dir.is_dir():
        logger.error("CNV input directory does not exist: %s", args.input_dir)
        raise SystemExit(1)
    try:
        profile = load_dataset_profile(args.profile)
        cnv_mapping = resolve_config_path("cnv_mapping", profile)
        qc_variable_mapping = resolve_config_path("variable_mapping", profile)
    except Exception as exc:
        logger.error("Could not resolve CNV configuration: %s", exc)
        raise SystemExit(1) from exc
    for label, path in (
        ("CNV mapping", cnv_mapping),
        ("QC variable mapping", qc_variable_mapping),
    ):
        if not path.is_file():
            logger.error("%s does not exist: %s", label, path)
            raise SystemExit(1)

    logger.info("Converting Sea-Bird CNV casts...")
    logger.info("  Input directory:  %s", args.input_dir.absolute())
    logger.info("  Output directory: %s", args.output_dir.absolute())
    logger.info("  Dataset profile: %s", Path(args.profile).absolute())
    logger.info("  CNV mapping:     %s", cnv_mapping.absolute())
    logger.info("  QC mapping:       %s", qc_variable_mapping.absolute())
    try:
        report = convert_cnv_directory(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            profile=profile,
            overwrite=args.overwrite,
        )
    except KeyboardInterrupt:
        logger.warning("CNV conversion interrupted by user.")
        raise SystemExit(1)
    except Exception as exc:
        logger.exception("CNV conversion failed: %s", exc)
        raise SystemExit(1)

    counts = report.as_dict()["counts"]
    logger.info(
        "CNV conversion complete: discovered=%s converted=%s skipped=%s failed=%s",
        counts["cnv_discovered"],
        counts["converted"],
        counts["skipped"],
        counts["failed"],
    )
    logger.info("Conversion report: %s", report.report_path.absolute())
    if report.has_failures:
        raise SystemExit(1)


def _run_qc(args: argparse.Namespace) -> None:
    setup_logging(verbose=args.verbose, log_file=args.log_file)

    profile = load_dataset_profile(args.profile)
    base_dir = Path(args.base_dir) if args.base_dir else profile.data_root
    if not base_dir.exists():
        logger.error("Base directory does not exist: %s", base_dir)
        sys.exit(1)
    if not base_dir.is_dir():
        logger.error("Base path is not a directory: %s", base_dir)
        sys.exit(1)

    mapping_path = resolve_config_path("variable_mapping", profile)
    if not mapping_path.exists():
        logger.error("Mapping file does not exist: %s", mapping_path)
        sys.exit(1)

    station_coords_csv = resolve_config_path("station_coords", profile)
    location_config = resolve_config_path("location_config", profile)
    station_climatology_config = resolve_config_path("station_climatology", profile)
    station_depth_classification = resolve_config_path("station_depth_classification", profile)
    sensor_specs_path = resolve_config_path("sensor_specs", profile)
    variable_sensor_map_path = resolve_config_path("variable_sensor_map", profile)
    spike_thresholds_path = resolve_config_path("spike_thresholds", profile)
    rate_of_change_thresholds_path = resolve_config_path("rate_of_change_thresholds", profile)
    flat_line_config = resolve_config_path("flat_line_config", profile)
    location_cfg = load_location_config(location_config)

    logger.info("Starting QC pipeline...")
    logger.info("  Dataset profile:          %s", Path(args.profile).absolute())
    logger.info("  Base directory:            %s", base_dir.absolute())
    logger.info("  Output mode:               %s", profile.output.mode)
    logger.info("  Output directory:          %s", profile.output.directory.absolute())
    logger.info("  Sample dimension:          %s", profile.metadata.sample_dimension)
    logger.info("  Mapping file:              %s", mapping_path.absolute())
    logger.info("  Station coords CSV:        %s", station_coords_csv.absolute())
    logger.info("  Location config JSON:      %s", location_config.absolute())
    logger.info("  Station depth class JSON:  %s", station_depth_classification.absolute())
    logger.info("  Station climatology JSON:  %s", station_climatology_config.absolute())
    logger.info("  Sensor specs JSON:         %s", sensor_specs_path.absolute())
    logger.info("  Variable-sensor map JSON:  %s", variable_sensor_map_path.absolute())
    logger.info("  Spike thresholds JSON:       %s", spike_thresholds_path.absolute())
    logger.info("  Rate-of-change thresholds:   %s", rate_of_change_thresholds_path.absolute())
    logger.info("  Flat-line config JSON:       %s", flat_line_config.absolute())
    logger.info("  Location tolerance:        %s degrees", location_cfg["tolerance"])

    try:
        run_qc_for_all(
            base_dir=base_dir,
            profile=profile,
        )
        logger.info("QC pipeline completed successfully!")

        if args.sync_erddap_xml:
            logger.info("Syncing ERDDAP datasets.xml from QC NetCDF output...")
            out_path = run_erddap_xml_sync_for_profile(
                profile,
                input_xml=args.erddap_input_xml,
                output_xml=args.erddap_output_xml,
                filedir_prefix=args.erddap_filedir_prefix,
                verbose=args.verbose,
            )
            logger.info("ERDDAP XML written to %s", out_path.absolute())
    except KeyboardInterrupt:
        logger.warning("QC pipeline interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logger.exception("QC pipeline failed: %s", e)
        sys.exit(1)


def _run_erddap_xml(args: argparse.Namespace) -> None:
    profile = load_dataset_profile(args.profile)
    data_root = resolve_erddap_data_root(profile, args.data_root)
    try:
        run_erddap_xml_sync(
            input_xml=args.input_xml,
            output_xml=args.output_xml,
            data_root=data_root,
            profile=profile,
            filedir_prefix=args.filedir_prefix,
            dataset_type=args.dataset_type,
            dataset_template_xml=args.dataset_template_xml,
            create_missing_datasets=not args.no_create_missing_datasets,
            remove_orphan_datasets=not args.keep_orphan_datasets,
            in_place=args.in_place,
            preserve_erddap_ui=not args.no_preserve_erddap_ui,
            verbose=args.verbose,
        )
    except KeyboardInterrupt:
        logging.getLogger("erddap_xml_sync").warning("Interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logging.getLogger("erddap_xml_sync").exception("ERDDAP XML sync failed: %s", e)
        sys.exit(1)


def _run_viz(args: argparse.Namespace) -> None:
    profile = load_dataset_profile(args.profile)
    data_root = Path(args.data_root) if args.data_root else None
    try:
        from qc_dashboard import default_viz_data_root, run_dashboard

        resolved_root = data_root or default_viz_data_root(profile)
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
