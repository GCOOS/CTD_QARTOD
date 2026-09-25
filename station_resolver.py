"""
Utilities to resolve station coordinates from station IDs.

Station IDs are read from the ``station`` variable inside each NetCDF file
and looked up in Station_Mean_Coords.csv to obtain expected (lat, lon).
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

from qc_config import STATION_COORDS_CSV
from station_names import normalize_reference_station as normalize_station_coord


@lru_cache(maxsize=None)
def load_station_coords(csv_path: Path | str = STATION_COORDS_CSV) -> Dict[str, Tuple[float, float]]:
    """
    Load station -> (lat, lon) from STATION_COORDS_CSV.
    """
    coords: Dict[str, Tuple[float, float]] = {}
    path = Path(csv_path)
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            station_raw = row.get("station")
            lat_raw = row.get("lat_mean")
            lon_raw = row.get("lon_mean")
            if not station_raw or lat_raw is None or lon_raw is None:
                continue
            key = normalize_station_coord(station_raw)
            try:
                lat = float(lat_raw)
                lon = float(lon_raw)
            except ValueError:
                continue
            coords[key] = (lat, lon)
    return coords

def resolve_coords_by_station_id(
    station_id: str | None,
    station_csv: Path | str = STATION_COORDS_CSV,
) -> Optional[Tuple[float, float]]:
    """
    Resolve expected (lat, lon) for a station ID.

    The *station_id* is typically obtained from :func:`qc_data_loader.get_station_id`
    which reads the ``station`` variable from the NetCDF file.
    """
    if not station_id:
        return None

    station_key = normalize_station_coord(station_id)
    station_coords = load_station_coords(station_csv)

    coords = station_coords.get(station_key)
    if coords:
        return coords

    alt_key = station_key.replace("-", "")
    return station_coords.get(alt_key)
