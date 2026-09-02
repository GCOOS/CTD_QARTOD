# Config templates for new datasets

Copy this entire folder into a dataset-specific configuration directory and replace placeholder values with settings for that dataset.

```bash
cp -R config_template config/YOUR_DATASET
# Replace YOUR_DATASET in dataset_profile.json, then edit the dataset-specific files.
```

Generate the CNV mapping, resolve any ambiguous entries, then run conversion and QC:

```bash
python main.py inspect-cnv /path/to/cnv/files \
  --output config/YOUR_DATASET/cnv_mapping.json

# Edit only entries that remain action=review; mapped comments are ready to use.
python main.py convert-cnv \
  /path/to/cnv/files \
  --mapping config/YOUR_DATASET/cnv_mapping.json \
  --output /path/to/source/netcdf

python main.py qc --profile config/YOUR_DATASET/dataset_profile.json
python main.py erddap-xml --profile config/YOUR_DATASET/dataset_profile.json
```

## Folder layout (mirrors `config/`)

| Subfolder | File | Purpose |
|-----------|------|---------|
| root | `dataset_profile.json` | Dataset root, output mode, metadata variable names, ERDDAP generation settings, and `sample_dimension` |
| root | `cnv_mapping.json` | Header inventory plus comment-derived source-to-NetCDF mapping |
| root | `qc_variable_mapping.json` | QC **category** → list of mapped **NetCDF variable names** |
| `location_test/` | `Station_Mean_Coords.csv`, `location_config.json` | Expected latitude/longitude per station ID and tolerance |
| `gross_range_test/` | `sensor_specs.json`, `variable_sensor_map.json` | Instrument matching + unit-aware min/max for gross range test |
| `climatology_test/` | `station_climatology_config.json`, `station_depth_classification.json` | Seasonal/value limits by cast type; which stations are deep vs shallow |
| `spike_test/` | `spike_thresholds.json` | Per-variable spike thresholds (QARTOD average method) |
| `rate_of_change_test/` | `rate_of_change_thresholds.json` | Per-variable max \|Δvalue\| between consecutive samples |
| `flat_line_test/` | `flat_line_config.json` | Repeat-count thresholds and epsilon for flat-line checks |

## Checklist for a new dataset

1. **Dataset profile** — Replace `YOUR_DATASET`, then set `data_root`, `output.mode`, metadata variable names, `metadata.sample_dimension`, `qc_test_modes`, and the complete `erddap` generation policy. Set `output_xml`, the absolute ERDDAP-server `filedir_prefix`, any `dataset_id_prefix`, the global metadata names required for publication, and deliberate `global_add_attributes`. For SFER-style files shaped `(profile, z)`, use `"sample_dimension": "z"`. Every `paths` entry is required; commands do not provide path overrides.
2. **CNV source mapping** — Run `inspect-cnv` on the complete input scope. Stable comments automatically provide `target_name` and `long_name`; the converter uses each column's declared unit. Fill the generated `standard_name`, `standard_name_url`, `ioos_category`, `ncei_name`, and `sensor_tag` placeholders where known, plus `units` where the source declares none. Untouched `null` placeholders are ignored. Resolve any entries left as `review`. Repeated destinations receive `_2`, `_3`, and later suffixes.
3. **QC variable mapping** — List mapped NetCDF variables under the correct QC category (see `qc_config.py` → `ALL_CATEGORIES` and `TEST_CATEGORIES`). This is independent of CNV source-name mapping.
4. **Location test** — Add one station-coordinate row per station ID and set `location_config.json` tolerance.
5. **Gross range** — Add a sensor entry per instrument type (`identifiers.long_names` must match source-derived `instrument` metadata). Map each variable to a sensor key; units in `ranges` must exactly match units written by `cnv_mapping.json`.
6. **Climatology** (optional) — Assign each station to `deep_cast` or `shallow_cast`, then define `tspan` / `vspan` / `period` rules per variable under the matching `*_limits` block. Leave it empty when defensible limits are unavailable; QC will emit `NOT_EVALUATED`.
7. **Spike / rate of change / flat line** — Add one object per variable for spike and rate of change in the stored source units; omit variables to skip those tests for that variable. Set global flat-line repeat counts in `flat_line_config.json`.

Current gap and syntax implementations support `not_evaluated`. Keep that mode
until a real implementation is added; selecting `run` fails preflight instead
of silently producing unevaluated flags.

`metadata.depth` names the NetCDF variable containing depth values. `metadata.sample_dimension` names the dimension along which count-based tests compare consecutive samples.

NetCDF global attributes are the primary ERDDAP source metadata. Put only
missing publication values, intentional overrides, or `null` removals in
`erddap.global_add_attributes`; the generator validates the combined view and
emits only those configured additions in XML. It creates a complete new
`datasets.xml` and never reads a sample/template XML.

The Walton Smith CTD QC configuration and NOAA AOML Hogarth CNV configuration
are independent examples under `config/walton_smith/` and `config/hogarth_cnv/`.
