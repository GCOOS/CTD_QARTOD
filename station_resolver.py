"""
Utilities to resolve station coordinates from station IDs.

Station IDs are read from the ``station`` variable inside each NetCDF file
and looked up in the dataset's station coordinates CSV to obtain expected
(lat, lon). Both sides are compared with the dataset's station normalization.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

from station_identity import StationNormalization


@lru_cache(maxsize=None)
def load_station_coords(
    csv_path: Path | str,
    normalization: StationNormalization = StationNormalization(),
) -> Dict[str, Tuple[float, float]]:
    """
    Load normalized station key -> (lat, lon) from a station coordinates CSV.
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
            try:
                lat = float(lat_raw)
                lon = float(lon_raw)
            except ValueError:
                continue
            coords[normalization.key(station_raw)] = (lat, lon)
    return coords


def resolve_coords_by_station_id(
    station_id: str | None,
    station_csv: Path | str,
    normalization: StationNormalization = StationNormalization(),
) -> Optional[Tuple[float, float]]:
    """
    Resolve expected (lat, lon) for a station ID.

    The *station_id* is typically obtained from :func:`qc_data_loader.get_station_id`
    which reads the ``station`` variable from the NetCDF file.
    """
    if not station_id:
        return None
    return load_station_coords(station_csv, normalization).get(normalization.key(station_id))
