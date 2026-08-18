# CNV conversion process

This document follows the implemented conversion in execution order. It
explains how the converter reads each configuration section and each section of
a Sea-Bird CNV file, what it writes to NetCDF, and where conversion stops and
the existing QC pipeline begins.

The workflow boundary is:

```text
Sea-Bird CNV files
    + dataset_profile.json
    + cnv_mapping.json
        |
        v
QC-compatible source NetCDF + conversion_report.json
        |
        v
existing QC pipeline + dataset-specific QC configuration
        |
        v
QC NetCDF
```

The converter interprets the source structure. It does not run QARTOD tests,
choose scientific limits, or add publication metadata that is absent from the
CNV files.

## Run the Hogarth conversion

From the repository root:

```bash
PYTHONPATH=. uv run python main.py convert-cnv \
  --profile config/hogarth_cnv/dataset_profile.json \
  --input-dir cnv_data/2026_07_Hogarth_NOAA_CTD \
  --output-dir output/SFER_CNV
```

This creates:

```text
output/SFER_CNV/
|-- conversion_report.json
`-- HG26193/
    |-- HG26193_1.nc
    |-- HG26193_2.nc
    `-- ...
```

Use `--overwrite` only when existing converted NetCDF files should be replaced.
Without it, existing files are reported as skipped.

## Step 1: load the dataset profile

The `--profile` argument selects the dataset-level configuration. For Hogarth,
this is `config/hogarth_cnv/dataset_profile.json`.

### `data_root`

```json
"data_root": "output/SFER_CNV"
```

This is the default source-NetCDF directory used by downstream commands. The
conversion command still uses its explicit `--output-dir` argument, so those
two locations should normally be the same.

### `output`

```json
"output": {
  "mode": "duplicate",
  "directory": "output/SFER_QC"
}
```

This section is used by the QC stage, not by CNV parsing. `duplicate` tells QC
to preserve the source NetCDF under `output/SFER_CNV` and write a separate QC
copy under `output/SFER_QC`.

### `metadata`

```json
"metadata": {
  "station": "station",
  "cruise_id": "cruiseID",
  "longitude": ["longitude", "lon"],
  "latitude": ["latitude", "lat"],
  "depth": ["depth"],
  "time": ["time"],
  "sample_dimension": "z"
}
```

These are NetCDF names, not CNV source names.

- `station` and `cruise_id` are the identifier-variable names written by the
  converter.
- The first name in each longitude, latitude, depth, and time list is the name
  written by the converter. Additional names are aliases that let downstream
  code read other compatible NetCDF datasets.
- `sample_dimension` is the NetCDF dimension used for samples in one cast.

For example, the CNV mapping says that `depSM` supplies the structural role
`depth`; this profile says that the role is written to the NetCDF variable
`depth`.

### `paths`

The paths section selects all dataset-owned conversion and QC files.

| Key | Stage | Purpose |
| --- | --- | --- |
| `cnv_mapping` | Conversion | Interprets filenames, structural columns, and science columns |
| `variable_mapping` | Conversion report and QC | Selects the NetCDF science variables assigned to QC categories |
| `station_coords` | QC | Provides expected station positions |
| `location_config` | QC | Provides the allowed location tolerance |
| `station_climatology` | QC | Provides climatology limits |
| `station_depth_classification` | QC | Classifies stations for climatology |
| `sensor_specs` | QC | Defines reviewed instrument ranges by source unit |
| `variable_sensor_map` | QC | Associates NetCDF variables with sensor specifications |
| `spike_thresholds` | QC | Defines spike thresholds |
| `rate_of_change_thresholds` | QC | Defines rate-of-change thresholds |
| `flat_line_config` | QC | Defines repeated-value settings |

Only `cnv_mapping` and `variable_mapping` are read during conversion. The
remaining paths are selected here so that the generated NetCDF can enter the
existing QC command without a Walton-specific fallback.

## Step 2: load and validate `cnv_mapping.json`

Every CNV dataset supplies a complete mapping. The current converter accepts
mapping schema version 1.

### `schema_version`

```json
"schema_version": 1
```

The conversion stops if this is not a supported version. This prevents a file
written for a different mapping structure from being interpreted silently.

### `identity`

```json
"identity": {
  "filename_pattern": "^(?P<cruise>[^_]+)_Stn\\.(?P<station>.+)_(?P<sequence>\\d+)_DatCnv_processed\\.cnv$",
  "cruise_case": "upper",
  "station_case": "upper",
  "normalize_numeric_station": true
}
```

`filename_pattern` is a regular expression applied to the complete filename.
It must contain three named capture groups:

- `cruise` extracts the cruise identifier;
- `station` extracts the station identifier;
- `sequence` extracts the numeric cast sequence used for deterministic
  ordering and duplicate resolution.

For example:

```text
HG26193_Stn.9.5_001_DatCnv_processed.cnv
```

produces cruise `HG26193`, station `9.5`, and sequence `1`.
`cruise_case` and `station_case` then standardize letter case.
`normalize_numeric_station` removes insignificant leading and trailing zeros
from purely numeric station IDs while preserving a meaningful decimal point.

### `structural_fields`

This section must define exactly four roles: `depth`, `time`, `latitude`, and
`longitude`. These fields need limited structural handling so downstream QC can
locate them reliably.

Each role can contain:

- `source_candidates`: CNV column names tried in order;
- `required`: whether conversion fails when none of those columns exists;
- `transform`: the numerical interpretation applied to the source values;
- `units` and optional `calendar`: NetCDF coordinate attributes;
- `reducer`: an optional operation that creates one profile-level value;
- `retain_samples_as`: an optional variable that preserves the original
  per-sample source values.

Supported transforms are deliberately limited:

- `identity` copies the numbers unchanged;
- `epoch_offset` adds the configured number of seconds to every value.

The only supported reducer is `median`. It converts a per-sample coordinate
into one representative value for the profile while ignoring missing values.
It is not a filter and does not modify the retained sample array.

The Hogarth structural processing is:

| Role | CNV source | Processing | NetCDF result |
| --- | --- | --- | --- |
| Depth | `depSM` | Identity | `depth(profile, z)` in m |
| Time | `timeQ` | Add `946684800` seconds | `time(profile, z)` as Gregorian Unix time |
| Latitude | `latitude` | Identity, then median | `latitude(profile)` plus `latitude_sample(profile, z)` |
| Longitude | `longitude` | Identity, then median | `longitude(profile)` plus `longitude_sample(profile, z)` |

The time step uses the already offset-corrected `timeQ` values from the CNV. It
does not reconstruct time from `# start_time` or combine elapsed-time columns.

### `science_variables`

Each entry maps one CNV column name to one QC-facing NetCDF variable name:

```json
"sbeox0Mg/L": {
  "target": "dissolved_oxygen",
  "units": "mg/l"
}
```

The converter copies the numeric array unchanged, writes it under `target`, and
sets the configured unit label. It does not numerically convert science units.
The NetCDF variable also retains the original CNV field name and description as
`source_name` and `source_description` attributes.

The target is the project's QC-facing NetCDF variable name. It must be safe to
write to NetCDF and match the name used in the dataset's QC configuration, but
the converter does not verify it against the CF standard-name table. It does
not have to be the literal CNV name. Duplicate target names are rejected.

## Step 3: inventory the input directory

The converter reads files directly inside `--input-dir`; it does not recurse
into subdirectories. It inventories these suffixes case-insensitively:

- `.cnv`
- `.xmlcon`
- `.hex`
- `.hdr`
- `.mrk`
- `.bl`
- all other files

Only `.cnv` files become NetCDF casts. Companion-file counts and per-cast
presence are written to `conversion_report.json`.

The output directory must be outside the input directory. This prevents a
conversion from accidentally treating generated products as source data.

## Step 4: parse the identity from each filename

The converter applies `identity.filename_pattern` to each `.cnv` filename. A
file that does not match becomes a failed record in the conversion report; it
is not guessed from the header.

The extracted cruise and station values are normalized as configured. The
original station text is also retained as the NetCDF global attribute
`source_station`.

## Step 5: process each section of the CNV file

A Sea-Bird CNV contains several logically different sections. They are not all
used in the same way.

| CNV section | Example | How conversion handles it |
| --- | --- | --- |
| Human-readable preamble | Lines beginning with `*`, including instrument and acquisition text | Kept in the source CNV but not copied as a general structured metadata block |
| Column declarations | `# name 2 = t090C: Temperature [ITS-90, deg C]` | Parsed into ordered column index, source name, and description; controls numeric-table interpretation |
| Observed spans | `# span 2 = ...` | Stored among parsed header key/value pairs but not used as QC limits or as authoritative data ranges |
| Required table declarations | `# nquan`, `# nvalues`, `# bad_flag` | Used to validate the table and replace the declared bad value with missing data |
| General header values | Other `# key = value` lines | Parsed into the internal header dictionary; only specifically supported values are promoted to NetCDF metadata |
| Embedded sensor XML | `# <Sensors ...>` through `# </Sensors>` | Parsed into active sensor records; the raw XML block is not copied to NetCDF |
| DatCnv processing fields | `# datcnv_* = ...` | Copied into `source_processing_parameters`; supported oxygen-correction flags also contribute to `source_processing` |
| Header terminator | `*END*` | Separates header from numeric rows; exactly one is required |
| Numeric sample table | Space-separated values after `*END*` | Parsed as floating-point rows using the `# name N` order |

### 5.1 Column declarations

The declared column indices must be contiguous from zero and their count must
match `# nquan`. Each numeric row must contain exactly that many values.

`# name N` is authoritative for which measurement a table position contains.
`# span N` is only a Sea-Bird summary of values observed in that same column.
It is not an instrument specification or a QARTOD gross-range threshold, so the
converter does not turn it into one.

### 5.2 Missing values and row count

Every table value equal to `# bad_flag` becomes `NaN`. The parsed row count must
match `# nvalues`; otherwise that cast fails conversion.

If the mapped science field whose target is `time_elapsed` is present, its
finite values must not decrease. This catches a malformed sample sequence
without changing the values.

### 5.3 Embedded sensor XML

The converter requires one complete embedded `<Sensors>` block. For every
active sensor wrapper, it extracts:

- channel number;
- sensor element type;
- serial number;
- calibration date;
- remaining leaf values as calibration coefficients.

Empty wrappers and `NotInUse` channels are skipped. The channel number is the
Sea-Bird configuration channel; it does not by itself prove which processed
science column was calculated from that sensor. Dataset-specific relationships
between NetCDF variables and sensor types remain in
`variable_sensor_map.json` for QC.

The current parser does not preserve the XML `SensorID` attribute as a separate
field. See [NOAA AOML Sea-Bird CNV data findings](cnv-data-findings.md) for the
observed instrument inventory and remaining sensor-identification limits.

### 5.4 DatCnv processing records

All parsed header keys beginning with `datcnv_` are serialized into the NetCDF
global attribute `source_processing_parameters`. The converter also builds a
short `source_processing` description containing:

- Sea-Bird DatCnv conversion;
- oxygen hysteresis correction, when explicitly recorded as enabled;
- oxygen tau correction, when explicitly recorded as enabled.

This is provenance, not new processing performed by this converter.

## Step 6: check companion files

The converter associates companion files with a cast by filename stem. For the
CNV stem, `_DatCnv_processed` is removed before matching.

The `.xmlcon` file receives one additional check. The ordered active-sensor
signature from CNV is compared with the XMLCON signature using:

- sensor type;
- serial number;
- calibration date.

The result is `match`, `mismatch`, or `missing` in the conversion report. A
mismatch is reported as evidence; it does not prevent writing the NetCDF.
The `.hex`, `.hdr`, `.mrk`, and `.bl` files are currently checked for presence
only and are not used to calculate values.

## Step 7: choose a deterministic output name

Casts are sorted by cruise, normalized station, sequence, and source filename.
The normal output stem is:

```text
<CRUISE>_<STATION>
```

A decimal point in the station token becomes an underscore for the filename,
so station `9.5` is stored in `HG26193_9_5.nc` while its NetCDF station value
remains `9.5`.

When a cruise contains more than one cast for the same station, later casts in
the deterministic order receive `-2`, `-3`, and so on. The sequence therefore
resolves filenames without changing the station identifier inside the file.

## Step 8: construct the NetCDF dataset

### 8.1 Dimensions and coordinates

Each output represents one cast and starts with:

- `profile = 1`;
- `z = number of numeric CNV rows` for the Hogarth profile;
- `profile(profile)` containing integer `0` and marked as the profile ID;
- `z(z)` containing sample indices from zero.

The name `z` comes from `metadata.sample_dimension` and can differ in another
dataset profile.

### 8.2 Structural variables

For each structural role, the converter:

1. selects the first available `source_candidates` column;
2. fails the cast if no candidate exists and `required` is true;
3. applies the configured transform;
4. optionally writes the original source samples under `retain_samples_as`;
5. optionally reduces the transformed values with the configured reducer;
6. writes the result using the first destination name in the profile's
   corresponding `metadata` list.

Structural variables receive CF-style coordinate attributes such as
`standard_name`, `axis`, units, and the source transform. This is the limited
normalization required for the current QC pipeline to understand time and
location.

### 8.3 Science variables

The converter walks the CNV columns in their declared order. If a column is in
`science_variables`, it writes a floating-point variable with dimensions
`(profile, z)` and these attributes:

- configured `units`;
- CNV `source_name`;
- CNV `source_description`;
- coordinate-variable names;
- WGS 84 grid-mapping reference;
- `coverage_content_type = physicalMeasurement`.

The numeric values are copied after only the general `bad_flag`-to-`NaN`
replacement. Oxygen, temperature, salinity, conductivity, fluorescence, and
other science arrays are not recalculated or unit-converted.

A CNV column that is neither a configured structural candidate nor a configured
science variable is listed under `unknown_columns` in the conversion report and
is not written silently under an invented name.

### 8.4 Cruise and station identifiers

The normalized cruise and station strings are repeated across `(profile, z)`
as `cruiseID` and `station` for compatibility with the existing workflow.
They are written as strings, so numeric and alphanumeric stations are both
preserved safely.

### 8.5 Coordinate reference system

A scalar `crs` variable records WGS 84 / EPSG:4326. Science variables refer to
it through their `grid_mapping` attribute.

### 8.6 Instrument variables

The output contains:

- a scalar `instrument` variable describing the CTD as Sea-Bird SBE 25plus;
- `instrument1`, `instrument2`, and so on for active embedded sensors.

Each sensor variable contains the parsed type, channel, serial number,
calibration date, and calibration coefficients as attributes. The generic CTD
model currently reflects the Hogarth source header and is not selected from
`cnv_mapping.json`; a future CNV dataset with a different CTD model requires
that behavior to be made dataset-configurable before its output can make the
correct model claim.

### 8.7 Derived global attributes

The converter derives global attributes that can be supported by the file and
conversion context, including:

- title containing cruise/station context, ID, station name, and source
  filename;
- source format and source station/sequence;
- DatCnv processing provenance;
- time, latitude, longitude, and depth coverage computed from sample values;
- processing level and conversion history;
- the selected structural source columns.

It does not add creator contacts, contributors, platform registry IDs,
acknowledgments, references, product version/date, publication URLs, or fixed
program-wide bounds. Those are publication metadata and are not reliably
available from the CNV.

## Step 9: write the file atomically

The dataset is first written beside its destination with a `.cnvtmp` suffix.
Floating-point arrays are stored as `float64` with NetCDF4 compression. The
temporary file replaces the final `.nc` only after the complete write succeeds.
If writing fails, the temporary file is removed and the error is recorded.

This prevents a partially written file from looking like a successful cast.

## Step 10: write and review `conversion_report.json`

The batch report is also written atomically. Review at least these sections
before starting QC:

| Report section | What to check |
| --- | --- |
| `counts` | Discovered, parsed, converted, skipped, and failed cast counts |
| `failed` | Filename, CNV-format, mapping, or NetCDF-write errors |
| `skipped` | Existing outputs not replaced because `--overwrite` was absent |
| `schemas` | Column layouts observed across the cruise |
| `unknown_columns` | Source columns not handled by the mapping |
| `source_to_target` | Effective science-variable names |
| `structural_fields` | Selected destination, transform, reducer, and retained samples |
| `qc_mapping_coverage` | How many mapped science variables enter QC categories |
| `mapped_variables_absent_from_qc_mapping` | Converted science variables not selected for QC |
| `duplicate_resolutions` | Multiple casts assigned to the same normalized station |
| `xmlcon` and `companions` | Sensor-signature result and companion presence per cast |
| `metadata_gaps` | Publication information that conversion cannot supply |

The command exits unsuccessfully when any cast failed, but the report still
records successful casts and the exact failures.

## Step 11: pass the source NetCDF to the existing QC pipeline

Run:

```bash
PYTHONPATH=. uv run python main.py qc \
  --profile config/hogarth_cnv/dataset_profile.json \
  --base-dir output/SFER_CNV \
  --no-sync-erddap-xml
```

The converter creates no `*_qc_*` variables. QC uses the same dataset profile
to locate variables and configuration:

1. `qc_variable_mapping.json` groups NetCDF target names by QC category.
2. The profile's metadata names let QC locate station, depth, time, latitude,
   longitude, and the sample dimension.
3. Each test reads its dataset-specific configuration file.
4. Each executed test writes a per-test QC variable.
5. QC combines those flags into an aggregate variable and records the QC
   variables in the science variable's `ancillary_variables` attribute.

For the current Hogarth configuration:

- gross-range entries retain source units but their unverified minimum and
  maximum values are `null`;
- spike and rate-of-change thresholds are `null`;
- location tolerance is `null`;
- climatology limits are empty;
- flat-line settings are populated and active.

Tests without approved limits return `NOT_EVALUATED`; the converter does not
derive limits from CNV `# span` values or sensor calibration coefficients.
This keeps source interpretation and scientific QC policy from overlapping.

With `output.mode = duplicate`, the source files remain unchanged under
`output/SFER_CNV` and QC products are written under `output/SFER_QC`.

## Step 12: inspect or prepare the QC output

Launch the viewer:

```bash
PYTHONPATH=. uv run python main.py viz \
  --profile config/hogarth_cnv/dataset_profile.json \
  --data-root output/SFER_QC
```

Generate ERDDAP XML from the QC tree when appropriate:

```bash
PYTHONPATH=. uv run python main.py erddap-xml \
  --data-root output/SFER_QC
```

Publication still requires review and addition of the metadata that CNV cannot
supply. Successful CNV conversion and QARTOD execution do not by themselves
make the file publication-complete.


## Adapting the process to another CNV dataset

Create a dataset-owned profile and complete CNV mapping rather than editing the
Hogarth mapping in place:

1. inspect the new filename convention and define the required named identity
   groups;
2. inspect every `# name N` field across the cruise;
3. choose the four structural sources and only the transformations needed for
   QC compatibility;
4. map each science source name to a valid, stable NetCDF target while
   preserving its source unit;
5. create a dataset-specific `qc_variable_mapping.json`;
6. create QC configuration templates with unreviewed limits left `null`;
7. convert the cruise and review schemas, unknown columns, duplicates,
   companions, and failures in `conversion_report.json`;
8. inspect representative NetCDF variables, dimensions, values, units, time,
   coordinates, and sensor attributes before running QC.

Do not assume another cruise shares the Hogarth filename, time encoding,
station normalization, column schema, CTD model, channel layout, sensor
inventory, units, or QC limits.
