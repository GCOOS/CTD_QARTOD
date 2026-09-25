"""Station names used by CNV conversion and QC reference lookup."""

STATION_ALIASES = {
    "21": "21LK",
    "21-5": "21_5",
    "57-1": "57_1",
    "57-2": "57_2",
    "57-3": "57_3",
    "mrv2": "MR-2",
    "54b": "54-2",
}


def normalize_reference_station(value: str) -> str:
    """Match a stored station ID to dotted station names in QC references."""
    station = value.strip().lower().replace("_", ".")
    if station.endswith(".0"):
        station = station[:-2]
    return station
