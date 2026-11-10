"""Station names used by CNV conversion and QC reference lookup."""

STATION_ALIASES = {
    "21": "21LK",
    "mrv2": "MR-2",
    "54b": "54-2",
}


def normalize_reference_station(value: str) -> str:
    """Match a stored station ID to dotted station names in QC references."""
    station = value.strip().lower().replace("_", ".")
    if station.endswith(".0"):
        station = station[:-2]
    return station
