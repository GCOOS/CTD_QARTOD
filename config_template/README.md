# Config templates for new datasets

Copy this entire folder (or individual subfolders) into a project `config/` directory and replace placeholder values with settings for your dataset.

```bash
cp -R config_template/ config/
# Edit files under config/ — do not commit dataset-specific secrets if applicable
```

Point the QC CLI at your filled `config/` using `config/dataset_profile.json`:

```bash
python main.py qc --profile config/dataset_profile.json \
  --base-dir /path/to/nc/files
```

## Folder layout (mirrors `config/`)

| Subfolder | File | Purpose |
|-----------|------|---------|
| root | `dataset_profile.json` | Dataset root, output mode, metadata variable names, and `sample_dimension` |
| `variable_mapping/` | `walton_mapping.json` | Walton **category** → list of **NetCDF variable names** in your files |
| `location_test/` | `Station_Mean_Coords.csv`, `location_config.json` | Expected latitude/longitude per station ID and tolerance |
| `gross_range_test/` | `sensor_specs.json`, `variable_sensor_map.json` | Instrument matching + unit-aware min/max for gross range test |
| `climatology_test/` | `station_climatology_config.json`, `station_depth_classification.json` | Seasonal/value limits by cast type; which stations are deep vs shallow |
| `spike_test/` | `spike_thresholds.json` | Per-variable spike thresholds (QARTOD average method) |
| `rate_of_change_test/` | `rate_of_change_thresholds.json` | Per-variable max \|Δvalue\| between consecutive samples |
| `flat_line_test/` | `flat_line_config.json` | Repeat-count thresholds and epsilon for flat-line checks |

## Checklist for a new dataset

1. **Dataset profile** — Set `data_root`, `output.mode`, metadata variable names, and `metadata.sample_dimension`. For SFER-style files shaped `(profile, z)`, use `"sample_dimension": "z"`.
2. **Variable mapping** — List every NetCDF data variable you want QC'd under the correct Walton category (see `qc_config.py` → `ALL_CATEGORIES` and `TEST_CATEGORIES` for which tests apply).
3. **Location test** — Add one station-coordinate row per station ID and set `location_config.json` tolerance.
4. **Gross range** — Add a sensor entry per instrument type (`identifiers.long_names` must match `instrument` metadata in files). Map each variable to a sensor key; units in `ranges` must match variable `units` attributes.
5. **Climatology** (optional) — Assign each station to `deep_cast` or `shallow_cast`, then define `tspan` / `vspan` / `period` rules per variable under the matching `*_limits` block.
6. **Spike / rate of change / flat line** — Add one object per variable for spike and rate of change; omit variables to skip those tests for that variable (flags will be NOT_EVALUATED). Set global flat-line repeat counts in `flat_line_config.json`.

`metadata.depth` names the NetCDF variable containing depth values. `metadata.sample_dimension` names the dimension along which count-based tests compare consecutive samples.

The reference implementation for the SFER dataset lives in the repo's `config/` directory (not in `config_template/`).
