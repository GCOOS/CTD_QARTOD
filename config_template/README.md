# Shared defaults for new datasets

`config_template/` is the generic starting point for a new Sea-Bird CNV program.
Program-specific identity, platforms, and climatology live in `presets/`.
`python main.py convert-cnv INPUT --prepare-profile --preset sfer_aoml` copies
the SFER/AOML preset; omit `--preset` to start from this template.

```text
config_template/
  dataset_profile.json
  station_identity.json
  soak_config.json
  cnv_catalog.json
  qc_variable_mapping.json
  gross_range_test/
  climatology_test/
  location_test/
  spike_test/
  rate_of_change_test/
  flat_line_test/
presets/sfer_aoml/
  dataset_profile.json
  station_identity.json
  climatology_test/
  location_test/Station_Mean_Coords.csv
```

- `dataset_profile.json`: generic display name, empty platforms, and null human
  contacts. SFER publication defaults are in `presets/sfer_aoml/`.
- `station_identity.json`: permissive cruise/station filename rules. SFER aliases
  and hyphen stations are in the preset.
- `climatology_test/station_classification.json`: named classes plus ordered
  fallback rules (`max_cast_max_depth`, bbox). The template has no classes.
- `cnv_catalog.json`: recognized CNV names/descriptions, canonical measurement
  names, attributes, sensor associations and spelling-only unit normalization.
  It is shared by conversion, not duplicated into dataset inventories.
- `qc_variable_mapping.json`: QC categories mapped to Walton measurement names.
  It determines which kinds of measurements receive each test; it is not the
  source-CNV alias catalog.
- Per-test folders: populated Walton Smith limits, station references, and
  settings. These are defaults to review, not scientific certification for every
  cruise. Missing units/references can leave tests NOT_EVALUATED.

## Automatic CNV workflow

```bash
python main.py convert-cnv /path/to/cruise_cnv --prepare-profile --preset sfer_aoml
python main.py convert-cnv /path/to/cruise_cnv --profile config/CRUISE/dataset_profile.json
python main.py qc --profile config/CRUISE/dataset_profile.json
python main.py erddap-xml --profile config/CRUISE/dataset_profile.json
```

Conversion creates a dataset profile from this template and an observed
`cnv_mapping.json`. After conversion, it copies/adapts QC defaults to the actual
NetCDF variable names and units, directly under `config/<dataset>/`. The dataset
gets its own `qc_variable_mapping.json`, per-test settings, and generated
`variable_mapping/source_variable_mapping.json` provenance inventory. No nested
`qc` folder or `qc_limits` switch is needed.

For schema-5 CNV mappings, an entry contains `observed` and `mapped_to` (a catalog
key), not duplicated attributes or sensor tags. Unknown/conflicting sources use
`mapped_to: null` and require a catalog correction or an explicit selection.
Intentional exclusion uses `ignore: true` and `mapped_to: null`. Source values
are never rescaled, missing units are never guessed, and duplicate destinations
receive `_2`, `_3`, etc. after mapping.

## Existing NetCDF datasets

Create a dataset-specific profile with its own input/output paths and no QC paths
(`"paths": {}`), using the metadata defaults here. Then run:

```bash
python main.py generate-limits --profile config/MY_DATASET/dataset_profile.json
python main.py qc --profile config/MY_DATASET/dataset_profile.json
```

Generation inventories those NetCDF files and saves independent local settings.
Do not run QC directly against the template profile or leave new profiles pointing
at `config_template/` settings. Profiles with existing QC paths are treated as
configured and are not silently rewritten. Existing outputs/settings are protected
from replacement.

Edit `config_template/` to change defaults for future datasets. Edit
`config/<dataset>/` to change that dataset's limits or metadata. Catalog changes
apply to future conversions; previously written NetCDF files do not change.
