# Shared defaults for new datasets

`config_template/` is the single source of reusable configuration. Its QC values
were copied from `config/walton_smith/`; they are now independent defaults, not
live references to that dataset. Existing dataset configs are never recopied on
normal reruns.

```text
config_template/
  dataset_profile.json
  cnv_catalog.json
  qc_variable_mapping.json
  gross_range_test/
  climatology_test/
  location_test/
  spike_test/
  rate_of_change_test/
  flat_line_test/
```

- `dataset_profile.json`: populated human, fixed and derived metadata, output and
  XML defaults, QC modes, and paths to the template settings. Human values retain
  the approved WS24258/SFER defaults. Review the creator and geographic polygon
  for other datasets; null omits an inapplicable human value.
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
python main.py convert-cnv /path/to/cruise_cnv
python main.py qc --profile config/CRUISE_ID_LOWERCASE/dataset_profile.json
python main.py erddap-xml --profile config/CRUISE_ID_LOWERCASE/dataset_profile.json
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
