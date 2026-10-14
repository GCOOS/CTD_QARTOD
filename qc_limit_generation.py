"""Create independent, dataset-owned QC settings from a preset or the generic template."""
from collections import defaultdict
import json
from pathlib import Path
import shutil

import xarray as xr

from cnv_catalog import CATALOG_PATH, qc_entry
from cnv_mapping import _atomic_json
from dataset_profile import CONFIG_LAYOUT, load_dataset_profile, reference_file
from qc_template_limits import template_gross_ranges, template_reference
from qc_validation import REQUIRED_QC_PATHS

GENERATED_SETTINGS = (*REQUIRED_QC_PATHS, "source_variable_mapping")
REFERENCE_SETTINGS = (
    "station_coords", "location_config", "station_climatology", "station_classification",
    "spike_thresholds", "rate_of_change_thresholds", "flat_line_config",
)


def generate_limits(profile, output=None, *, preset=None, activate=None):
    if profile.profile_path is None:
        raise ValueError("generate-limits requires a saved dataset profile")
    destination = Path(output) if output else profile.profile_path.parent
    activate = output is None if activate is None else activate
    profile_target = destination / "dataset_profile.json"
    if profile_target.resolve() == profile.profile_path.resolve() and not activate:
        raise ValueError("writing settings beside the active profile requires activation")
    if profile_target.exists() and profile_target.resolve() != profile.profile_path.resolve():
        raise FileExistsError(f"refusing to replace existing profile {profile_target}")
    sources = sorted(profile.data_root.rglob("*.nc"))
    if not sources:
        raise ValueError(f"no converted NetCDF files under {profile.data_root}")
    preset = profile.preset if preset is None else preset
    # Validate every destination before writing any setting; preserve user edits.
    targets = [destination / CONFIG_LAYOUT[key] for key in GENERATED_SETTINGS]
    targets.append(destination / "limit_report.json")
    for target in targets:
        if target.exists():
            raise FileExistsError(f"refusing to replace existing QC setting {target}")
    def reference_path(key):
        return reference_file(CONFIG_LAYOUT[key], preset)

    def reference_json(key):
        return json.loads(reference_path(key).read_text(encoding="utf-8"))

    sensors = {}
    categories = defaultdict(set)
    references = defaultdict(set)
    source_mapping = {}
    issues = set()
    mapping_path = profile.paths.variable_mapping
    old_mapping = json.loads(mapping_path.read_text()) if mapping_path and mapping_path.is_file() else {}
    index_path = profile.data_root / "conversion_index.json"
    index = json.loads(index_path.read_text()) if index_path.is_file() else {}
    cnv_paths = {target: source for source, target in index.items()}
    for source in sources:
        with xr.open_dataset(source, decode_cf=False) as dataset:
            relative_file = source.relative_to(profile.data_root).as_posix()
            ranges = template_gross_ranges(dataset)
            for name, variable in dataset.variables.items():
                entry = qc_entry(name, dict(variable.attrs))
                if entry is not None:
                    files = source_mapping.setdefault(entry["target"], {"files": {}})["files"]
                    files.setdefault(relative_file, []).append({
                        "variable": name,
                        "source_file": cnv_paths.get(relative_file, dataset.attrs.get("source_file", relative_file)),
                        "source_name": variable.attrs.get("source_name", name),
                        "source_occurrence": int(variable.attrs.get("source_occurrence", 1)),
                        "units": variable.attrs.get("units"),
                        "source_units": variable.attrs.get("source_units", variable.attrs.get("units")),
                    })
                category = (entry or {}).get("qc_category")
                if category is None:
                    category = next((key for key, names in old_mapping.items() if name in names), None)
                if category is None:
                    continue
                categories[category].add(name)
                references[name].add(template_reference(name, variable))
                spec = sensors.setdefault(name, {
                    "description": "Dataset-owned limit initialized from the reference gross ranges; not an instrument model assignment",
                    "requires_instrument": False, "identifiers": {"long_names": []}, "ranges": {},
                })
                unit = variable.attrs.get("units")
                span = ranges.get(name, {}).get("fail_span")
                if unit is not None:
                    spec["ranges"][str(unit)] = {"min": span[0] if span else None, "max": span[1] if span else None}
                if span is None:
                    issues.add(f"{name}: no template gross range for units {unit!r}")
    if not categories:
        raise ValueError("no supported QC measurements found")
    def remap(table):
        result = {}
        for name, refs in references.items():
            if len(refs) == 1 and None not in refs:
                ref = next(iter(refs))
                if ref in table:
                    result[name] = table[ref]
        return result

    payloads = {
        "source_variable_mapping": {"schema_version": 1, "data_root": str(profile.data_root),
                                    "catalog": str(CATALOG_PATH),
                                    "variables": source_mapping},
        "variable_mapping": {key: sorted(names) for key, names in categories.items()},
        "sensor_specs": {"sensors": sensors},
        "variable_sensor_map": {name: name for name in sensors},
        "spike_thresholds": remap(reference_json("spike_thresholds")),
        "rate_of_change_thresholds": remap(reference_json("rate_of_change_thresholds")),
        "station_climatology": {name: remap(table) for name, table in reference_json("station_climatology").items()},
        "station_classification": reference_json("station_classification"),
        "location_config": reference_json("location_config"),
        "flat_line_config": reference_json("flat_line_config"),
    }
    updated = json.loads(profile.profile_path.read_text())
    # Profiles resolve relative paths against the repository, even after relocation.
    updated.setdefault("paths", {})
    for key, payload in payloads.items():
        target = destination / CONFIG_LAYOUT[key]
        _atomic_json(target, payload)
        updated["paths"][key] = str(target.resolve())
    coords = destination / CONFIG_LAYOUT["station_coords"]
    coords.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(reference_path("station_coords"), coords)
    updated["paths"]["station_coords"] = str(coords.resolve())
    report = {"preset": preset, "netcdf_file_count": len(sources),
              "references": {key: str(reference_path(key)) for key in REFERENCE_SETTINGS},
              "issues": sorted(issues), "profile": str(destination / "dataset_profile.json"),
              "rate_of_change_basis": "unchanged reference per-adjacent-sample thresholds; not per-second"}
    _atomic_json(destination / "limit_report.json", report)
    from cnv_converter import _atomic_json as replace_json
    if profile_target.resolve() == profile.profile_path.resolve():
        replace_json(profile_target, updated)
    else:
        _atomic_json(profile_target, updated)
        if activate:
            replace_json(profile.profile_path, updated)
    return report


def prepare_dataset_qc(profile):
    """Initialize only profiles with no QC paths; never replace configured settings."""
    # Explicit paths are the configuration, not a separate template/custom switch.
    qc_keys = REQUIRED_QC_PATHS
    if any(profile.paths.get(key) is not None for key in qc_keys):
        return profile
    if profile.profile_path is None:
        raise ValueError("QC initialization requires a saved dataset profile")
    saved = load_dataset_profile(profile.profile_path)
    if any(saved.paths.get(key) is not None for key in qc_keys):
        return saved
    destination = saved.profile_path.parent
    for key in GENERATED_SETTINGS:
        target = destination / CONFIG_LAYOUT[key]
        if target.is_file():
            target.unlink()
    (destination / "limit_report.json").unlink(missing_ok=True)
    generate_limits(saved, activate=True)
    return load_dataset_profile(profile.profile_path)
