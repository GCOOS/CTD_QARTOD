"""Read config_template only when creating a dataset-owned QC configuration."""
import json

from cnv_catalog import normalize_unit, qc_entry
from dataset_profile import CONFIG_TEMPLATE_DIR


def template_reference(variable_name, variable):
    entry = qc_entry(variable_name, dict(variable.attrs))
    if entry is None:
        return None
    unit = normalize_unit(variable.attrs.get("units"))
    if unit is None or unit not in entry.get("qc_units", entry["units"]):
        return None
    return entry.get("qc_reference")


def template_gross_ranges(dataset):
    root = CONFIG_TEMPLATE_DIR / "gross_range_test"
    specs = json.loads((root / "sensor_specs.json").read_text())["sensors"]
    links = json.loads((root / "variable_sensor_map.json").read_text())
    resolved = {}
    for name, variable in dataset.variables.items():
        reference = template_reference(name, variable)
        sensor = specs.get(links.get(reference), {})
        unit = normalize_unit(variable.attrs.get("units"))
        for source_unit, span in sensor.get("ranges", {}).items():
            if unit is not None and normalize_unit(source_unit) == unit and span.get("min") is not None and span.get("max") is not None:
                resolved[name] = {"fail_span": (float(span["min"]), float(span["max"]))}
                break
    return resolved


def template_thresholds(dataset, thresholds):
    """Only publish applicable entries; incompatible or missing units stay unevaluated."""
    return {name: thresholds[reference]
            for name, variable in dataset.variables.items()
            if (reference := template_reference(name, variable)) in thresholds}
