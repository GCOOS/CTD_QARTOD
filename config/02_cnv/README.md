# `02_CNV` conversion configuration

This directory owns the detachable filename rules and schema-2 scientific
mapping for the historical `cnv_data/02_CNV` archive. Conversion creates new
source NetCDF files only; it does not modify the CNV archive, old NetCDF files,
QARTOD results, or `datasets.xml`.

The detailed field-by-field contract is in
[`docs/02-cnv-conversion-process-proposal.md`](../../docs/02-cnv-conversion-process-proposal.md).

## Configuration files

| File | Responsibility |
| --- | --- |
| `filename_identity.json` | External, detachable cleanup rules that produce reviewed cruise/station identities and exclusions |
| `cnv_mapping.json` | Destination-keyed source columns, occurrences, science attributes, CTD/sensor metadata, sensor links, and platform aliases |

The converter does not contain filename-cleaning fallbacks. If future input
filenames are standardized, replace the preparation stage or its configuration;
the core conversion does not change.

## 1. Prepare and review identities

Run from the repository root:

```bash
python main.py prepare-cnv-identities \
  --input-dir cnv_data/02_CNV \
  --config config/02_cnv/filename_identity.json \
  --manifest output/02_cnv/identity_manifest.json
```

The manifest contains one record for every exact case-insensitive `.cnv` file
and the explicit `.2cnv` exception. Review every `unresolved` row before treating
a run as complete. Do not change an unresolved station, platform, call sign, or
`.2cnv` authority decision without provider confirmation.

The current reviewed inventory is 3,792 records: 3,660 included casts, 118
exclusions, and 14 unresolved filename identities. The Captiva Bluehole
`.2cnv` record is an enforced exclusion pending provider confirmation.

## 2. Convert reviewed casts

Use a new output root, separate from both `cnv_data/02_CNV` and the old NetCDF
dataset tree:

```bash
python main.py convert-cnv \
  --input-dir cnv_data/02_CNV \
  --output-dir output/SFER_CNV_02 \
  --manifest output/02_cnv/identity_manifest.json \
  --mapping config/02_cnv/cnv_mapping.json \
  --report output/SFER_CNV_02/conversion_report.json
```

For each included cast, the output path is:

```text
<output-root>/<cruiseID>/<cruiseID>_<station-token>[-<repeat-number>].nc
```

Repeated stations are ordered by parsed UTC cast start time and then source
relative path. Existing targets are failures unless `--overwrite` is supplied.
Each file is written to a sibling temporary file, reopened and validated, then
atomically installed.

The current production manifest still contains unresolved rows. The converter
continues through all resolved includes and writes the complete report, but the
CLI exits nonzero while any unresolved identity or included-cast failure remains.

## NetCDF boundary

- `time(profile, z)` contains unchanged CNV `timeS` values.
- Its units are `seconds since <# start_time in UTC>` and its calendar is
  `proleptic_gregorian`.
- There is no cast-start `time` variable, `time_elapsed`, or standalone `z`
  variable.
- `station(profile, z)` and `cruiseID(profile, z)` use the reviewed manifest
  identity; global `station_name` is the complete output stem.
- Science destinations, source aliases/occurrences, attributes, and instrument
  links come from `cnv_mapping.json`; values and source units are not converted.
- Platform fields are emitted only for an approved `** Ship` alias. Walton
  Smith's scalar call sign remains an empty string pending confirmation.
- No QARTOD variables are created in this stage.

## ERDDAP XML boundary

The shared generator publishes new source `time` directly. For old NetCDF
files it omits the old cast-start `time` when `time_elapsed` exists and maps
`time_elapsed` to destination `time`. The one published time receives:

```xml
<att name="time_precision">1970-01-01T00:00:00.000Z</att>
```

NetCDF globals remain the source attributes ERDDAP reads. Publication metadata
that is absent from NetCDF must be supplied deliberately through the selected
profile's `erddap.global_add_attributes`; the generator writes only those
additions, overrides, or `null` removals to global XML `<addAttributes>`.

`main.py erddap-xml` remains profile-driven. Select or create an approved
publication profile whose data root, output XML, server path, required globals,
and global additions describe the intended converted or QC tree. This directory deliberately does not invent that profile
while provider questions and downstream QC/publication policy remain unresolved.
