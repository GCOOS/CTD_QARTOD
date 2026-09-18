"""Ownership and derivation rules for CNV NetCDF global metadata."""
from datetime import datetime, timezone
import re
from cnv_mapping import CF_STANDARD_NAME_VOCABULARY

PLATFORMS = {"WS": "RV_FG_Walton_Smith", "WB": "RV_Weatherbird_II", "SV": "RV_Savannah", "SAV": "RV_Savannah", "HG": "RV_hogarth"}
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


def publication_metadata(cruise, stem, date, latitude, longitude, fixed, derived):
    prefix = re.split(r"\d", cruise, maxsplit=1)[0].upper()
    if prefix not in PLATFORMS:
        raise ValueError(f"unknown cruise platform prefix {prefix!r}; add a reviewed platform mapping")
    identifier = f"SFER_CTD_{stem}"
    position = f"{abs(latitude):.4f}{'N' if latitude >= 0 else 'S'} {abs(longitude):.4f}{'E' if longitude >= 0 else 'W'}"
    context = {"id": identifier, "cruise": cruise, "station_name": stem,
               "date": date, "position": position, "sea_name": fixed.get("sea_name", "the study area")}
    title = derived.get("title", "SFER CTD, {id}, {date}, {position}")
    summary = derived.get("summary", "SFER CTD data, {id}, {date}, {position}. Hydrographic Measurements in {sea_name}.")
    for name, value in (("title", title), ("summary", summary)):
        if not isinstance(value, str) or "{id}" not in value:
            raise ValueError(f"derived {name} must be a template containing {{id}}, not an example cast")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"id": identifier, "title": title.format_map(context), "summary": summary.format_map(context),
            **{key: PLATFORMS[prefix] for key in ("platform", "platform_name", "platform_id")},
            **{key: now for key in ("date_created", "date_modified", "date_metadata_modified")}}
