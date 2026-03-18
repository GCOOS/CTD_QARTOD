"""
Main entry point for running QC pipeline on SFER_CTD NetCDF files.

Usage:
    python main.py [--base-dir DIR] [--mapping-path JSON] [--station-coords CSV] ...
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from qc_config import (
    DATASET_DIR,
    LOCATION_TOLERANCE,
    SENSOR_SPECS_JSON,
    STATION_CLIMATOLOGY_JSON,
    STATION_COORDS_CSV,
    VARIABLE_MAPPING_JSON,
    VARIABLE_SENSOR_MAP_JSON,
    
)
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


def main():
    """Main entry point for QC application."""
    parser = argparse.ArgumentParser(
        description="Run QC pipeline on SFER_CTD NetCDF files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run QC on default SFER_CTD directory
    python main.py

    # Run QC on a specific directory
    python main.py --base-dir /path/to/datasets/SFER_CTD

    # Use custom config files
    python main.py --sensor-specs /path/to/sensor_specs.json
        """,
    )

    parser.add_argument(
        "--base-dir",
        type=str,
        default=str(DATASET_DIR),
        help=f"Base directory containing cruise directories (default: '{DATASET_DIR}')",
    )

    parser.add_argument(
        "--mapping-path",
        type=str,
        default=str(VARIABLE_MAPPING_JSON),
        help=f"Path to variable mapping JSON (default: '{VARIABLE_MAPPING_JSON}')",
    )

    parser.add_argument(
        "--location-tolerance",
        type=float,
        default=LOCATION_TOLERANCE,
        help=f"Tolerance in degrees for location test (default: {LOCATION_TOLERANCE})",
    )

    parser.add_argument(
        "--station-coords",
        type=str,
        default=str(STATION_COORDS_CSV),
        help=f"Path to station coordinates CSV (default: '{STATION_COORDS_CSV}')",
    )

    parser.add_argument(
        "--station-climatology-config",
        type=str,
        default=str(STATION_CLIMATOLOGY_JSON),
        help=f"Path to station climatology config JSON (default: '{STATION_CLIMATOLOGY_JSON}')",
    )

    parser.add_argument(
        "--sensor-specs",
        type=str,
        default=str(SENSOR_SPECS_JSON),
        help=f"Path to sensor specs JSON (default: '{SENSOR_SPECS_JSON}')",
    )

    parser.add_argument(
        "--variable-sensor-map",
        type=str,
        default=str(VARIABLE_SENSOR_MAP_JSON),
        help=f"Path to variable-sensor map JSON (default: '{VARIABLE_SENSOR_MAP_JSON}')",
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (debug) logging",
    )

    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to log file (in addition to console output)",
    )

    args = parser.parse_args()
    
    setup_logging(verbose=args.verbose, log_file=args.log_file)

    base_dir = Path(args.base_dir)
    if not base_dir.exists():
        logger.error("Base directory does not exist: %s", base_dir)
        sys.exit(1)
    if not base_dir.is_dir():
        logger.error("Base path is not a directory: %s", base_dir)
        sys.exit(1)

    mapping_path = Path(args.mapping_path)
    if not mapping_path.exists():
        logger.error("Mapping file does not exist: %s", mapping_path)
        sys.exit(1)

    station_coords_csv = Path(args.station_coords)
    station_climatology_config = Path(args.station_climatology_config)
    sensor_specs_path = Path(args.sensor_specs)
    variable_sensor_map_path = Path(args.variable_sensor_map)

    logger.info("Starting QC pipeline...")
    logger.info("  Base directory:            %s", base_dir.absolute())
    logger.info("  Mapping file:              %s", mapping_path.absolute())
    logger.info("  Station coords CSV:        %s", station_coords_csv.absolute())
    logger.info("  Station climatology JSON:  %s", station_climatology_config.absolute())
    logger.info("  Sensor specs JSON:         %s", sensor_specs_path.absolute())
    logger.info("  Variable-sensor map JSON:  %s", variable_sensor_map_path.absolute())
    logger.info("  Location tolerance:        %s degrees", args.location_tolerance)

    try:
        run_qc_for_all(
            base_dir=base_dir,
            mapping_path=mapping_path,
            location_tolerance=args.location_tolerance,
            station_climatology_config_path=station_climatology_config,
            sensor_specs_path=sensor_specs_path,
            variable_sensor_map_path=variable_sensor_map_path,
            station_coords_csv=station_coords_csv,
        )
        logger.info("QC pipeline completed successfully!")
    except KeyboardInterrupt:
        logger.warning("QC pipeline interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logger.exception("QC pipeline failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
