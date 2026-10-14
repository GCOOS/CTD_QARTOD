"""Ownership and derivation rules for CNV NetCDF global metadata."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

from cnv_mapping import CF_STANDARD_NAME_VOCABULARY

FIXED_DEFAULTS = {
    "source": "Sea-Bird CNV processed ASCII", "source_format": "Sea-Bird CNV",
    "processing_level": "Geophysical units from processed CNV data",
    "featureType": "Profile", "cdm_data_type": "Profile", "cdm_altitude_proxy": "depth",
    "geospatial_lat_units": "degrees_north", "geospatial_lon_units": "degrees_east",
    "geospatial_vertical_positive": "down", "geospatial_bounds_crs": "EPSG:4326",
    "standard_name_vocabulary": CF_STANDARD_NAME_VOCABULARY,
}
DERIVED_NAMES = frozenset({
    "title", "summary", "id", "platform", "platform_name", "platform_id",
    "station_name", "instrument", "source_file", "cdm_profile_variables", "history",
    "time_coverage_start", "time_coverage_end", "time_coverage_duration", "time_coverage_resolution",
    "geospatial_lat_min", "geospatial_lat_max", "geospatial_lon_min", "geospatial_lon_max",
    "geospatial_vertical_min", "geospatial_vertical_max", "geospatial_vertical_units", "comment",
    "date_created", "date_modified", "date_metadata_modified", "date_issued",
})


@dataclass(frozen=True)
class PublicationSettings:
    """Dataset-owned global metadata applied to every converted cast."""

    global_attributes: Mapping[str, object] = field(default_factory=dict)
    fixed_attributes: Mapping[str, object] = field(default_factory=dict)
    derived_attributes: Mapping[str, object] = field(default_factory=dict)
    platforms: Mapping[str, str] = field(default_factory=dict)
    dataset_id_prefix: str = ""


def validate_ownership(human, fixed, derived):
    for name, section in (("human", human), ("fixed", fixed)):
        conflict = set(section) & DERIVED_NAMES
        if conflict:
            raise ValueError(f"{name} global attributes cannot override generated attributes: {', '.join(sorted(conflict))}")
    overlap = set(human) & (set(fixed) | set(FIXED_DEFAULTS))
    if overlap:
        raise ValueError(f"global attributes have multiple owners: {', '.join(sorted(overlap))}")
    unknown = set(derived) - DERIVED_NAMES
    if unknown:
        raise ValueError(f"unknown derived global attributes: {', '.join(sorted(unknown))}")


def dataset_id(stem, settings):
    return f"{settings.dataset_id_prefix}{stem}"


def publication_metadata(cruise, platform, stem, date, latitude, longitude, settings):
    platforms = settings.platforms
    if platforms and platform not in platforms:
        raise ValueError(f"unknown platform {platform!r} for cruise {cruise}; add it to the dataset profile platforms")
    identifier = dataset_id(stem, settings)
    position = f"{abs(latitude):.4f}{'N' if latitude >= 0 else 'S'} {abs(longitude):.4f}{'E' if longitude >= 0 else 'W'}"
    context = {"id": identifier, "cruise": cruise, "station_name": stem,
               "date": date, "position": position,
               "sea_name": settings.fixed_attributes.get("sea_name", "the study area")}
    templates = {}
    for name in ("title", "summary"):
        value = settings.derived_attributes.get(name)
        if not isinstance(value, str) or "{id}" not in value:
            raise ValueError(f"derived {name} must be a template containing {{id}}, not an example cast")
        templates[name] = value.format_map(context)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"id": identifier, **templates,
            **({key: platforms[platform] for key in ("platform", "platform_name", "platform_id")} if platforms else {}),
            **{key: now for key in ("date_created", "date_modified", "date_metadata_modified")}}
