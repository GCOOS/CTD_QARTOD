# CNV inspection and conversion

The active conversion path targets the confirmed final CNV format represented
by WS24258. Input can be one `.cnv` file, one cruise folder, or a folder whose
subfolders contain multiple cruises.

```text
CNV input
  -> inspect-cnv
  -> comment-derived cnv_mapping.json
  -> convert-cnv
  -> source NetCDF + conversion_report.json
  -> QC
  -> generated ERDDAP XML
```

## 1. Inspect the complete input scope

```bash
PYTHONPATH=. uv run python main.py inspect-cnv \
  /path/to/cnv/input \
  --output config/YOUR_DATASET/cnv_mapping.json
```

Inspection reads only CNV headers. It recursively inventories every exact
source name, its maximum occurrence count within one file, descriptions,
units, and file count. Stable descriptions and units are mapped automatically;
only conflicting or unsafe entries remain in `review` state.

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
    "standard_name": null,
    "standard_name_url": null,
    "ioos_category": null,
    "ncei_name": null
  },
  "sensor_tag": null
}
```

Inspection leaves publication metadata and sensor linkage as explicit `null`
placeholders for human review. It also adds `"units": null` when the CNV
comment declares no unit. Conversion ignores untouched placeholders and uses
any non-null values that are filled in.

If conflicting descriptions or units leave an entry in `review`, resolve it as
`map` or explicitly exclude it as:

```json
{
  "action": "ignore",
  "target_name": null,
  "attributes": {
    "long_name": null,
    "standard_name": null,
    "standard_name_url": null,
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
  --mapping config/YOUR_DATASET/cnv_mapping.json \
  --output output/SFER_CNV
```

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
`temperature_2` when `t090C` occurs twice in one CNV file. Source values and
units are not converted.

The output tree is:

```text
output/SFER_CNV/
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

Conversion stops at source NetCDF. QARTOD configuration and ERDDAP publication
metadata remain separate downstream responsibilities.
