"""Resolve measured coordinate columns or a single NMEA cast position."""
from collections import Counter
from collections.abc import Sequence
import re


_NMEA_HEADER = re.compile(r"^[*#]\s*NMEA\s+(Latitude|Longitude)\s*=\s*(.*?)\s*$", re.IGNORECASE)
_DEGREES_MINUTES = re.compile(r"(\d{1,3})\s+(\d+(?:\.\d+)?)\s*([NSEW])", re.IGNORECASE)


def resolve_header_coordinates(source_names: Sequence[str], header_lines: Sequence[str]) -> dict[str, float] | None:
    """Return header coordinates, or None when both sample columns exist.

    A partial/duplicate column pair is an error, not permission to substitute
    header values for measured data. Header positions never become fake samples.
    """
    counts = Counter(source_names)
    if counts["latitude"] == counts["longitude"] == 1:
        return None
    if counts["latitude"] or counts["longitude"]:
        raise ValueError("latitude and longitude columns must both occur exactly once, or both be absent")
    headers: dict[str, list[str]] = {"latitude": [], "longitude": []}
    for line in header_lines:
        match = _NMEA_HEADER.fullmatch(line)
        if match:
            headers[match[1].lower()].append(match[2])
    coordinates = {}
    for name, hemispheres, maximum in (("latitude", "NS", 90), ("longitude", "EW", 180)):
        values = headers[name]
        if len(values) != 1:
            raise ValueError(f"missing coordinate columns: NMEA {name.title()} header must occur exactly once")
        match = _DEGREES_MINUTES.fullmatch(values[0])
        if match is None or match[3].upper() not in hemispheres:
            raise ValueError(f"invalid NMEA {name.title()}: expected degrees minutes {hemispheres}")
        degrees, minutes = int(match[1]), float(match[2])
        value = degrees + minutes / 60
        if not 0 <= minutes < 60 or value > maximum:
            raise ValueError(f"NMEA {name.title()} is outside valid degree/minute bounds")
        coordinates[name] = -value if match[3].upper() in "SW" else value
    return coordinates
