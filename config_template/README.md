# Config templates for new datasets

Copy this entire folder into a dataset-specific configuration directory and replace placeholder values with settings for that dataset.

```bash
cp -R config_template config/YOUR_DATASET
# Replace YOUR_DATASET in dataset_profile.json, then edit the dataset-specific files.
```

Point conversion and QC at that dataset's profile:

```bash
python main.py convert-cnv \
  --profile config/YOUR_DATASET/dataset_profile.json \
  --input-dir /path/to/cnv/files \
  --output-dir /path/to/source/netcdf

python main.py qc --profile config/YOUR_DATASET/dataset_profile.json \
  --base-dir /path/to/nc/files
```

## Folder layout (mirrors `config/`)

| Subfolder | File | Purpose |
|-----------|------|---------|
| root | `dataset_profile.json` | Dataset root, output mode, metadata variable names, and `sample_dimension` |
| root | `cnv_mapping.json` | Complete CNV filename, structural source/transform, and science source-to-NetCDF mapping |
| root | `qc_variable_mapping.json` | QC **category** → list of mapped **NetCDF variable names** |
| `location_test/` | `Station_Mean_Coords.csv`, `location_config.json` | Expected latitude/longitude per station ID and tolerance |
| `gross_range_test/` | `sensor_specs.json`, `variable_sensor_map.json` | Instrument matching + unit-aware min/max for gross range test |
| `climatology_test/` | `station_climatology_config.json`, `station_depth_classification.json` | Seasonal/value limits by cast type; which stations are deep vs shallow |
| `spike_test/` | `spike_thresholds.json` | Per-variable spike thresholds (QARTOD average method) |
| `rate_of_change_test/` | `rate_of_change_thresholds.json` | Per-variable max \|Δvalue\| between consecutive samples |
| `flat_line_test/` | `flat_line_config.json` | Repeat-count thresholds and epsilon for flat-line checks |

## Checklist for a new dataset

1. **Dataset profile** — Replace `YOUR_DATASET`, then set `data_root`, `output.mode`, metadata variable names, and `metadata.sample_dimension`. For SFER-style files shaped `(profile, z)`, use `"sample_dimension": "z"`.
2. **CNV source mapping** — Replace the filename expression, structural source candidates/transforms, units, and every science source-to-target entry. The filename pattern must contain named groups `cruise`, `station`, and `sequence`.
3. **QC variable mapping** — List mapped NetCDF variables under the correct QC category (see `qc_config.py` → `ALL_CATEGORIES` and `TEST_CATEGORIES`). This is independent of CNV source-name mapping.
4. **Location test** — Add one station-coordinate row per station ID and set `location_config.json` tolerance.
5. **Gross range** — Add a sensor entry per instrument type (`identifiers.long_names` must match source-derived `instrument` metadata). Map each variable to a sensor key; units in `ranges` must exactly match units written by `cnv_mapping.json`.
6. **Climatology** (optional) — Assign each station to `deep_cast` or `shallow_cast`, then define `tspan` / `vspan` / `period` rules per variable under the matching `*_limits` block. Leave it empty when defensible limits are unavailable; QC will emit `NOT_EVALUATED`.
7. **Spike / rate of change / flat line** — Add one object per variable for spike and rate of change in the stored source units; omit variables to skip those tests for that variable. Set global flat-line repeat counts in `flat_line_config.json`.

`metadata.depth` names the NetCDF variable containing depth values. `metadata.sample_dimension` names the dimension along which count-based tests compare consecutive samples.

The Walton Smith CTD QC configuration and NOAA AOML Hogarth CNV configuration
are independent examples under `config/walton_smith/` and `config/hogarth_cnv/`.
