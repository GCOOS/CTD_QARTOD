# CNV inspection and conversion

Input can be one `.cnv` file, one cruise folder, or a folder whose
subfolders contain multiple cruises.

```text
CNV input
  -> inspect-cnv
  -> comment-derived cnv_mapping.json
  -> convert-cnv
  -> source NetCDF + conversion_report.json
  -> generate-sensor-config
  -> sensor_specs.json + variable_sensor_map.json
  -> QC
  -> generated ERDDAP XML
```

## 1. Inspect the complete input scope

```bash
PYTHONPATH=. uv run python main.py inspect-cnv \
  /path/to/cnv/input \
  --output config/YOUR_DATASET/cnv_mapping.json
```

Inspection reads CNV headers without loading the numerical table. It
recursively inventories every exact source name, its maximum occurrence count
within one file, descriptions, units, file count, and every embedded Sensors
XML tag. It also verifies that `nquan` matches the declared columns and that
`nvalues` declares at least one row. Invalid files appear under
`inspection.failed_files` and do not influence the mapping.

Stable descriptions and units are mapped automatically; conflicting or unsafe
entries remain in `review` state. `inspection.sensor_tags` gives humans the
exact allowed spelling and reports how many valid files and occurrences contain
each tag.

The fixed source fields are:

- `timeS` -> `time`
- `longitude` -> `longitude`
- `latitude` -> `latitude`

If every inspected file contains one `depSM`, inspection selects it as the
vertical source. Otherwise the human must set `vertical_field.source_name`.

Stable comments produce entries like:

```json
{
  "action": "map",
  "target_name": "temperature",
  "attributes": {
    "long_name": "Temperature",
    "units": "deg C",
    "standard_name": null,
    "ioos_category": null,
    "ncei_name": null
  },
  "sensor_tag": null
}
```

`long_name`, a safe destination, and one stable source unit come directly from
the CNV comment. A missing or conflicting source unit produces
`"units": null`. The other values require review:

- `standard_name`: fill only with a real CF Standard Name Table v94 entry that
  describes the quantity. Leave it null when no exact name exists.
- `ioos_category`: fill with the appropriate discovery category if this output
  will be published through an IOOS/ERDDAP service.
- `ncei_name`: optional archive/product terminology. It is not a CF name and is
  not used by this QC pipeline; leave it null unless an NCEI delivery contract
  supplies the value.
- `sensor_tag`: use an exact key listed in `inspection.sensor_tags` for a
  physical measurement. Use the reserved string `derived` for a calculated
  variable with no dedicated sensor. A null is allowed for variables not
  selected for sensor-aware QC.

There is no editable `standard_name_url` field. When `standard_name` is filled,
conversion derives its version-pinned CF URL automatically. A human may change
`attributes.units` only to an equivalent notation, such as `deg C` to
`degree_Celsius`; the converter never scales or offsets numerical values.

If conflicting descriptions or units leave an entry in `review`, resolve it as
`map` or explicitly exclude it as:

```json
{
  "action": "ignore",
  "target_name": null,
  "attributes": {
    "long_name": null,
    "units": null,
    "standard_name": null,
    "ioos_category": null,
    "ncei_name": null
  },
  "sensor_tag": null
}
```

Conversion refuses mappings that still contain `review`.

## 2. Convert

```bash
PYTHONPATH=. uv run python main.py convert-cnv \
  /path/to/cnv/input \
  --profile config/YOUR_DATASET/dataset_profile.json
```

The profile supplies `paths.cnv_mapping`, writes source NetCDF beneath
`data_root`, and supplies confirmed dataset-level metadata through
`netcdf_global_attributes`.

Each actual basename must match:

```text
<cruiseID>_Stn.<station>.cnv
```

The embedded CNV `FileName` is provenance only. A mismatch is reported as a
warning. Numeric leading zeroes are removed without interpreting suffixes:

```text
WS24258_Stn.054.cnv  -> WS24258_54.nc
WS24258_Stn.054b.cnv -> WS24258_54b.nc
```

Only files with the same parsed cruise and station are repetitions. They are
ordered by `start_time` and source path, then named with `-2`, `-3`, and later
suffixes.

Mapped destination suffixes are assigned after source mapping. For example,
one mapping `t090C -> temperature` produces `temperature` and
`temperature_2` when `t090C` occurs twice in one CNV file. Source values are
never converted. The reviewed `attributes.units` spelling is written as
`units`; if it differs from the CNV spelling, that original is retained as
`source_units`.

The output tree is:

```text
<profile data_root>/
|-- conversion_report.json
`-- WS24258/
    |-- WS24258_54.nc
    `-- WS24258_54b.nc
```

NetCDF `time`, `longitude`, `latitude`, `depth`, station, cruise, and mapped
science variables use `(profile, z)`. `timeS` values are preserved under
`time`, with `# start_time` used as the CF time origin. Each NetCDF is written
to a sibling temporary file, reopened and validated, and then atomically
installed. Existing outputs require `--overwrite`.

Every mapped standard name receives a URL such as:

```text
https://cfconventions.org/Data/cf-standard-names/94/build/cf-standard-name-table.html#sea_water_temperature
```

The NetCDF global `standard_name_vocabulary` is written as
`CF Standard Name Table v94`. Sensor serial number, calibration date, channel,
and XML tag are copied into scalar `instrumentN` variables. A mapped science
variable points to its matching instrument through its `instrument` attribute;
an exact configured tag that is absent in a cast is a conversion error.

The converter also merges the profile's human-owned
`netcdf_global_attributes` into each file. Null values are review placeholders
and are omitted. Human values cannot replace derived fields such as `title`,
`history`, or coverage bounds. See
[CNV-generated NetCDF versus the supplied NetCDF](cnv-vs-supplied-netcdf.md)
for the complete ownership rules and concrete reference examples.

## 3. Generate the sensor configuration

First list every converted NetCDF variable that should be QCed under the right
category in `qc_variable_mapping.json`. Then run:

```bash
PYTHONPATH=. uv run python main.py generate-sensor-config \
  --profile config/YOUR_DATASET/dataset_profile.json
```

The command scans all `.nc` files under the profile's `data_root`. For each
QC-selected variable, it reads `units`, follows `instrument` to the copied XML
sensor tag, and writes the profile-selected files:

- `gross_range_test/sensor_specs.json`: one physical or derived sensor group,
  exact `identifiers.long_names`, every observed output unit, and
  `{ "min": null, "max": null }` placeholders.
- `gross_range_test/variable_sensor_map.json`: every QC variable, including
  `_2` destinations, linked to its detected sensor group. If different casts
  use different sensor types, the value is a list of valid groups.

A QC-selected variable with neither an instrument link nor an explicit
`sensor_tag: "derived"` stops generation and names the unresolved variable.
Variables without a unit still receive a sensor link, but their sensor range
table is empty until the mapping unit is reviewed and the files reconverted.
The command replaces empty template files, but protects nonempty reviewed files
unless `--overwrite` is explicitly supplied. `--overwrite` also replaces any
limits already entered.

## 4. Complete scientific QC configuration and run QC

Automation can identify structure, provenance, sensor association, and units;
it cannot choose scientifically defensible thresholds. Humans must fill:

| File | Human decision |
|---|---|
| `gross_range_test/sensor_specs.json` | Minimum and maximum for every sensor/unit range |
| `location_test/Station_Mean_Coords.csv` | Expected coordinate for each station |
| `location_test/location_config.json` | Allowed coordinate tolerance |
| `climatology_test/*.json` | Station class and defensible depth/time/value spans |
| `spike_test/spike_thresholds.json` | Suspect and fail spike thresholds |
| `rate_of_change_test/rate_of_change_thresholds.json` | Maximum adjacent-sample change |
| `flat_line_test/flat_line_config.json` | Repeat counts and epsilon appropriate for sampling resolution |
| `dataset_profile.json` | Input/output roots, test modes, and confirmed values for null `netcdf_global_attributes` placeholders |

Run the strict preflight and QC together with:

```bash
PYTHONPATH=. uv run python main.py qc \
  --profile config/YOUR_DATASET/dataset_profile.json
```

Null or absent scientific limits produce `NOT_EVALUATED`, not fabricated pass
flags. Batch QC mirrors the cruise tree in duplicate mode and records the
configuration and run outcome in `qc_run_manifest.json`.

## Responsibility summary

| Output information | Source | Automatic? |
|---|---|---|
| Cruise ID and station | Actual filename `<cruiseID>_Stn.<station>.cnv` | Yes |
| Repeated-station suffix | Duplicate parsed cruise/station identities | Yes |
| `time`, latitude, longitude | Fixed CNV columns; time origin from `start_time` | Yes |
| Depth | Inspected `depSM` when present in every valid file | Yes, otherwise review |
| Science source inventory and duplicate count | `# name` declarations across the full input scope | Yes |
| Initial destination and `long_name` | Stable CNV description comment | Yes, then human review |
| Initial `units` | Bracketed CNV comment unit | Yes, then human review |
| `standard_name` | Scientific meaning checked against CF v94 | Human |
| `standard_name_url` and global vocabulary | Reviewed `standard_name` plus the pinned table version | Yes |
| `ioos_category` | Publication/discovery intent | Human |
| `ncei_name` | Dataset-specific NCEI delivery vocabulary, if required | Human/optional |
| Sensor XML tag, serial, calibration, channel | Embedded Sensors XML | Yes |
| Variable-to-instrument decision | Exact `sensor_tag` or `derived` in `cnv_mapping.json` | Human once |
| Sensor spec skeleton and variable links | Converted variable units and instrument references | Yes |
| Gross range and all other scientific thresholds | Sensor documentation and scientific review | Human |
| NetCDF publication globals | `netcdf_global_attributes`; shared by all converted files and not inferred from CNV | Human |
| ERDDAP-only global additions | `erddap.global_add_attributes` | Human |

Conversion stops at source NetCDF. QARTOD configuration remains a separate
downstream responsibility; ERDDAP inherits NetCDF globals and adds only its
explicit XML-only values.
