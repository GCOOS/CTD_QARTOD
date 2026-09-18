# `02_CNV` to NetCDF and ERDDAP XML conversion specification

> Historical source/design record, not the current conversion contract. Source observations below are retained, but old mappings, timeQ/coordinate reduction rules, metadata ownership and CLI examples are superseded. Use the [current conversion process](cnv-conversion-process.md), [automatic workflow](automatic-cnv-workflow.md), and [shared template guide](../config_template/README.md). The current converter requires schema 5 and uses timeS, source-specific coordinate dimensions, and catalog-owned attributes.

## Status

This records the former baseline for converting the historical
`cnv_data/02_CNV` archive. It records the decisions made during design review
and is preserved for historical context only; it is not the active contract.

The implementation is split across `cnv_identity.py`, `cnv_mapping.py`,
`cnv_converter.py`, and `xml_generator/`. Operational commands and the
remaining provider-review boundary are documented in
`config/02_cnv/README.md`. Implementation and acceptance did not modify the CNV
inputs, old NetCDF files, or the repository's actual `datasets.xml`.

## 1. Required outcome and scope

The complete flow has three separate responsibilities:

```text
current, irregular source paths
        |
        v
detachable filename-identity preparation
        |
        +--> reviewed identity manifest
                         |
CNV files --------------+
                         v
                  core CNV converter
                         |
                         +--> new source NetCDF profiles
                         +--> conversion report
                                      |
                                      v
                            ERDDAP XML generation
```

The current filename cleaning is deliberately outside the core converter.
The core converter must receive resolved identities and must not contain the
legacy archive's filename regular expressions, special cases, or overrides.
When future input filenames are standardized, the legacy preparation step can
be replaced or bypassed while the conversion and XML logic remain unchanged.

In scope:

- recursively inventory the actual `02_CNV` archive;
- derive and standardize cruise and station identities from source paths;
- parse acquisition metadata, column declarations, processing metadata,
  sensor metadata, and the numerical table from each CNV;
- create one source NetCDF profile per included cast;
- publish elapsed `timeS` as the only NetCDF `time` variable;
- generate ERDDAP XML from NetCDF metadata and profile-owned additions;
- repair the published time mapping for old datasets without modifying their
  NetCDF files;
- produce deterministic validation and exception reports.

Out of scope:

- modifying any old NetCDF file;
- QARTOD tests, QARTOD flags, or QC threshold selection;
- converting source values to different scientific units;
- inventing missing station, ship, call-sign, or sensor metadata;
- copying the complete CNV header or sensor XML into NetCDF global attributes.

## 2. Separation of filename preparation from conversion

### 2.1 Current legacy preparation stage

The filename-identity preparer is a standalone, removable stage. It consumes
source relative paths and a small, reviewable set of ordered filename rules
and exact-path overrides. It emits an identity manifest containing at least:

| Field | Meaning |
| --- | --- |
| `source` | CNV path relative to the source root |
| `disposition` | `include`, `exclude`, or `unresolved` |
| `cruise_id` | Standardized cruise identifier |
| `station` | Display identity, such as `29.5` or `GP5` |
| `station_token` | Filename-safe station identity, such as `29_5` |
| `variant` | Source annotation such as `do_up`, if present |
| `embedded_filename` | Optional `* FileName` value retained for provenance |
| `warnings` | Identity mismatches or other review notes |
| `reason` | Required for excluded or unresolved rows |

An illustrative row is:

```json
{
  "source": "WS0603_cnv/WS0603_029_5.cnv",
  "disposition": "include",
  "cruise_id": "WS0603",
  "station": "29.5",
  "station_token": "29_5",
  "variant": null,
  "embedded_filename": "\\\\filesrv\\Data\\ctd\\WS0603_029_5.dat",
  "warnings": []
}
```

The exact storage format can be JSON, but the contract is more important than
the filename: every discovered `.cnv` must have one reviewable manifest row.
The manifest is input to conversion, not an implicit side effect hidden inside
the converter.

### 2.2 Future standardized filenames

Once providers deliver standardized filenames, a simple standardized-name
adapter can emit the same identity fields. The following must not change:

- the CNV parser;
- the NetCDF data model;
- science-variable mapping;
- repeat assignment;
- global-attribute generation;
- XML generation.

This boundary is the test for detachability: removing the legacy preparer must
not require deleting filename-specific branches from the converter, because
those branches must never be placed there.

## 3. Verified source-archive characteristics

The design was checked against the actual archive under `cnv_data/02_CNV`.
The inventory snapshot used for this specification is:

| Property | Observed value |
| --- | --- |
| Cruise folders | 72: 2 SAV and 70 WS |
| Files whose exact extension is `.cnv` | 3,791 |
| SBE 9 files | 3,728 |
| SBE 25plus files | 63 |
| Ordered column schemas | 10 |
| Columns per cast | 17 to 25 |
| Embedded sensor XML | Present in every `.cnv`; malformed in one file |
| Text encoding | All `.cnv` files decode as strict UTF-8 |
| Latitude/longitude source | Header only; not numerical data columns |
| Duplicate `oxsatML/L` columns | Two occurrences in all 3,791 files |

There is also one nonstandard file:

```text
cnv_data/02_CNV/WS19119_cnv/WS19119_Stn Captiva Bluehole.2cnv
```

It has the same declared start and row count as its `.cnv` counterpart, but
many numerical rows differ. It is excluded from automatic conversion and
recorded as a provider question rather than silently selecting one version.

## 4. Cruise, station, and output filename identity

### 4.1 Authority order

Identity is derived from the actual source path, not from the embedded
Sea-Bird `* FileName` value:

1. `cruise_id` comes from the immediate parent directory after removing the
   terminal `_cnv` suffix.
2. `station` comes from the actual CNV basename through ordered, configurable
   filename patterns.
3. An exact relative-path override resolves a genuinely ambiguous basename.
4. The embedded `* FileName` is a provenance cross-check only. A mismatch
   creates a warning; it never overrides the actual path.

For example, the source path
`WS23010_cnv/WS23011_Kelble_Stn.057.2.cnv` becomes cruise `WS23010` and
station `57.2`. The `WS23011` text in the basename is retained in a mismatch
warning but does not change the parent-derived cruise.

### 4.2 Station standardization

The canonical display value and filename token are different fields:

- remove leading zeros from a numeric station component;
- preserve a real decimal point in the display value;
- replace the decimal point with `_` only in the filename token;
- uppercase named stations;
- do not treat a processing or repeat annotation as part of station identity.

Examples:

| Source relative path | Cruise | Station | Token | Variant |
| --- | --- | --- | --- | --- |
| `WS0603_cnv/WS0603_029_5.cnv` | `WS0603` | `29.5` | `29_5` | none |
| `WS1418_cnv/ws141803.cnv` | `WS1418` | `3` | `3` | none |
| `SAV18173_cnv/STA02.cnv` | `SAV18173` | `2` | `2` | none |
| `WS19322_cnv/StnGP5.cnv` | `WS19322` | `GP5` | `GP5` | none |
| `WS22215_cnv/2022_08_Weatherbird_Smith_CTD_AMI3.cnv` | `WS22215` | `AMI3` | `AMI3` | none |
| `WS23010_cnv/WS23011_Kelble_Stn.057.2.cnv` | `WS23010` | `57.2` | `57_2` | none |
| `WS22022_cnv/WS22022_Stn.10_do_up.cnv` | `WS22022` | `10` | `10` | `do_up` |
| `WS21093_cnv/STN.007ws21093.cnv` | `WS21093` | `7` | `7` | none |

The known suffixes `b`, `v2`, `(1)`, `do`, `up`, `surface`, and `do_up` are
variants or repeat annotations. They are preserved in provenance but removed
from canonical station identity.

Named stations such as `CAL6`, `GP5`, `RP1`, `CH2`, `DT5`, `AMI3`, `GLIDER`,
and `MR` are valid stations. A station is not required to be numeric.

### 4.3 Exclusions and unresolved names

Deck, dunk, wet, or generic test casts are exclusions, not failed scientific
conversions. Examples include:

```text
WS15152_cnv/WS15152decktest.cnv
WS23010_cnv/decktst.cnv
WS23010_cnv/dunktst.cnv
WS0618_cnv/WS0618_test.cnv
```

They remain visible in the manifest and conversion report with an exclusion
reason. If a scientific cast's station cannot be resolved after the ordered
patterns and exact override table, no NetCDF is created and the cast is
reported as an identity failure.

### 4.4 Repeat numbering

Repeat assignment happens only after identities and CNV start times have been
validated:

1. group included casts by `(cruise_id, station)`;
2. sort each group by parsed `# start_time`;
3. use source relative path as the deterministic tie-breaker;
4. leave the first cast unsuffixed;
5. assign `-2`, `-3`, and so on to later casts.

The running number affects the output stem and `station_name`; it does not
change the `station` variable or title station text.

### 4.5 Output path

The required naming contract is:

```text
<output-root>/<cruiseID>/<cruiseID>_<station-token>[-<repeat-number>].nc
```

Examples:

```text
<output-root>/WS0603/WS0603_29_5.nc
<output-root>/WS0603/WS0603_29_5-2.nc
<output-root>/WS19322/WS19322_GP5.nc
```

## 5. CNV parsing contract

### 5.1 Discovery and file boundary

- Search recursively below the configured source root.
- Include files whose exact extension is `.cnv`, case-insensitively.
- Ignore unrelated extensions.
- Do not treat `.2cnv` as `.cnv`.
- Decode input as strict UTF-8.
- The first line equal to `*END*` ends the header.
- Nonblank lines after `*END*` are numerical rows.

### 5.2 Where each source value appears

The following concrete locations are from
`cnv_data/02_CNV/WS0603_cnv/WS0603_029_5.cnv`:

| CNV location | Example | Use |
| --- | --- | --- |
| line 1 | `* Sea-Bird SBE 9 Data File:` | CTD model |
| line 2 | `* FileName = ...WS0603_029_5.dat` | provenance cross-check only |
| lines 10-11 | `* NMEA Latitude`, `* NMEA Longitude` | profile position |
| line 14 | `** Ship: R/V FG Walton Smith` | platform normalization |
| lines 17-18 | `# nquan`, `# nvalues` | table structure |
| line 20 | `# name 0 = timeS: ... [seconds]` | elapsed-time column |
| line 37 | `# name 17 = depSM: ... [m]` | depth column |
| lines 30 and 38 | duplicate `oxsatML/L` declarations | occurrence-aware mapping |
| line 66 | `# interval = seconds: 0.0416667` | time resolution |
| line 67 | `# start_time = Jan 21 2006 21:27:48 ...` | time origin and cast date |
| line 68 | `# bad_flag = -9.990e-29` | source missing value |
| line 69 onward | `# <Sensors count="15">` | embedded sensor metadata |
| lines 293-318 | `datcnv`, `filter`, `alignctd`, `celltm`, `loopedit`, `Derive` | processing comment |
| line 320 | `*END*` | header/data boundary |

The SBE 25plus example is
`cnv_data/02_CNV/WS22215_cnv/2022_08_Weatherbird_Smith_CTD_AMI3.cnv`:

- line 1 identifies `SBE 25plus`;
- lines 7-8 hold latitude and longitude;
- line 16 declares `timeS`;
- line 51 holds `# start_time`;
- line 53 starts embedded sensor XML;
- no `** Ship` field appears before the column header at line 13, so platform
  identity must not be inferred from its filename.

### 5.3 Header sections to parse

The parser must distinguish these sections:

1. the first `* Sea-Bird ... Data File` model line;
2. acquisition entries written as `* key = value`;
3. free metadata such as `** Ship: value`;
4. structural `#` entries: `nquan`, `nvalues`, `name`, `span`, `interval`,
   `start_time`, and `bad_flag`;
5. embedded Sensors XML after removing the leading CNV comment marker;
6. processing entries with `datcnv`, `filter`, `alignctd`, `celltm`,
   `loopedit`, and `derive` prefixes, compared case-insensitively.

Parsing a field does not automatically make it a NetCDF global attribute.
Only the mappings in this specification may be written to NetCDF and XML.
Unknown header fields are counted and summarized in the report, not copied as
new metadata and not treated as failures.

### 5.4 Structural validation

Before writing a cast, require all of the following:

- one usable `*END*` boundary;
- contiguous `# name` indices from `0` through `nquan - 1`;
- exactly `nquan` declared columns;
- exactly `nvalues` numerical rows;
- exactly `nquan` numerical values in every row;
- one `timeS` column and one `depSM` column;
- a parseable `# start_time`;
- a parseable positive sampling interval;
- valid NMEA latitude and longitude within geographic ranges;
- monotonically nondecreasing finite elapsed-time values after missing-value
  handling;
- every numerical source column either structurally consumed or represented by
  the science mapping.

`# span` is parsed for diagnostics only. It is never authoritative for
`valid_min`, `valid_max`, or geospatial coverage; those values come from the
actual finite arrays.

An unknown numerical `# name` fails that cast. Silently dropping an
unrecognized column would create incomplete scientific data and is not
allowed.

## 6. NetCDF dimensions and core variables

Each included file produces one profile with these dimensions:

```text
profile = 1
z       = validated numerical row count
```

`z` is a dimension only. Do not create a standalone `z` coordinate variable.

| Variable | Dimensions | Value/source |
| --- | --- | --- |
| `profile` | `(profile)` | `int32` value `0`; `cf_role="profile_id"`; `long_name` is the output stem |
| `time` | `(profile, z)` | raw `timeS` values |
| `depth` | `(profile, z)` | raw `depSM` values |
| `latitude` | `(profile)` | decimal degrees from `* NMEA Latitude` |
| `longitude` | `(profile)` | decimal degrees from `* NMEA Longitude` |
| `station` | `(profile, z)` | canonical display station repeated over the cast |
| `cruiseID` | `(profile, z)` | canonical cruise ID repeated over the cast |
| `crs` | scalar | WGS84 horizontal CRS, EPSG:4326 |
| `platform` | scalar, conditional | canonical platform when supported by `** Ship` |
| `instrument` | scalar | main CTD identified by the first CNV line |
| `instrument1...N` | scalar, conditional | usable active sensors in source order |
| science variables | `(profile, z)` | configured source-column mappings |

The scalar CRS carries the standard WGS84 metadata: grid mapping name
`latitude_longitude`, longitude of prime meridian `0`, semi-major axis
`6378137`, inverse flattening `298.257223563`, and `epsg_code="EPSG:4326"`.

Each science variable uses the existing profile relationships:

```text
coordinates = "time longitude latitude depth"
grid_mapping = "crs"
platform     = "RV_FG_Walton_Smith"  # only when that platform was resolved
```

The data arrays preserve source row order. This source-NetCDF conversion does
not select only the downcast, bin, smooth, or otherwise subset rows.

### 6.1 Old Walton Smith reference and intentional differences

The reviewed old reference is:

```text
datasets/SFER_DIMENSIONSandCALENDAR_FIXED/WS0603/WS0603_29_5.nc
```

It confirms the requested structural precedent:

- `station(profile, z)` contains `29.5`;
- `cruiseID(profile, z)` contains `WS0603`;
- global `platform` and `platform_name` are `RV_FG_Walton_Smith`;
- a scalar `platform` variable exists;
- the title is
  `CTD data from SFER cruise WS0603, station 29.5, 2006-01-21`.

The new contract intentionally differs where this review established a clearer
rule:

- the old `time(profile)` cast-start variable is not copied;
- old `time_elapsed(profile, z)` becomes the sole new `time(profile, z)`;
- the old scalar platform call sign `WCZ6292` is not copied and remains empty;
- new `station_name` is the agreed output stem rather than the old duplicated
  `WS0603_WS0603_029_5` form;
- all validated source rows are retained rather than reproducing the old
  file's smaller row set without an approved selection rule.

## 7. Time contract

### 7.1 One time variable only

New NetCDF files contain only one variable named `time`:

- do not create a scalar or `(profile)` cast-start `time` variable;
- do not create `time_elapsed`;
- write elapsed `timeS` values directly to `time(profile, z)`.

The authoritative origin is `# start_time`, interpreted as UTC. Do not fall
back to `* System UpLoad Time`, filesystem timestamps, or the first numerical
value. A missing or invalid `# start_time` fails the cast.

### 7.2 Values and attributes

Do not convert elapsed values to Unix epoch seconds and do not subtract the
first value. If the CNV contains `[0.000, 0.042, ...]`, the NetCDF contains the
same values, apart from replacing the declared bad flag with `NaN`.

Required `time` attributes are:

```text
long_name        = "Elapsed Time in Second"
standard_name    = "time"
standard_name_url = "http://mmisw.org/ont/ioos/parameter/time"
axis             = "T"
units            = "seconds since <cast-start ISO timestamp with +00:00>"
calendar         = "proleptic_gregorian"
coordinates      = "depth latitude longitude time"
coverage_content_type = "physicalMeasurement"
ioos_category    = "Time"
valid_min        = minimum finite timeS
valid_max        = maximum finite timeS
```

Do not write placeholder `ancillary_variables="unknown"` or
`comment="unknown"` attributes.

For `WS0603_029_5.cnv`, line 67 is
`# start_time = Jan 21 2006 21:27:48`. Therefore:

```text
time:units = "seconds since 2006-01-21T21:27:48+00:00"
```

### 7.3 Coverage metadata

Let `origin` be parsed `# start_time`, `t_min` the minimum finite `timeS`, and
`t_max` the maximum finite `timeS`:

```text
time_coverage_start      = origin + t_min
time_coverage_end        = origin + t_max
time_coverage_duration   = t_max - t_min
time_coverage_resolution = parsed # interval
```

Timestamps use UTC ISO 8601 with terminal `Z`. Duration and resolution use ISO
8601 duration text while preserving the available sub-second precision. The
title date is the UTC calendar date of `# start_time`.

`time_precision="1970-01-01T00:00:00.000Z"` is an ERDDAP XML attribute only.
It is not a NetCDF `time` attribute.

## 8. Science-variable mapping

### 8.1 Approved source-to-destination names

| CNV source name | Occurrence | NetCDF destination name |
| --- | ---: | --- |
| `prDM` or `prdM` | 1 | `sea_water_pressure` |
| `t090C` | 1 | `sea_water_temperature` |
| `t190C` | 1 | `sea_water_temperature_2` |
| `c0S/m` | 1 | `sea_water_electrical_conductivity` |
| `c1S/m` | 1 | `sea_water_electrical_conductivity_2` |
| `flSP` | 1 | `chlorophyll_fluorescence` |
| `wetStar` or `flECO-AFL` | 1 | `chlorophyll_concentration` |
| `wetCDOM` | 1 | `CDOM` |
| `turbWETntu0` | 1 | `sea_water_turbidity` |
| `bat` or `CStarAt0` | 1 | `beam_attenuation` |
| `xmiss` or `CStarTr0` | 1 | `beam_transmission` |
| `sbeox0ML/L` | 1 | `dissolved_oxygen` |
| `oxsatML/L` | 1 | `oxygen_saturation` |
| `oxsatML/L` | 2 | `oxygen_saturation_2` |
| `par` | 1 | `photosynthetically_available_radiation` |
| `spar` | 1 | `surface_photosynthetically_available_radiation` |
| `altM` | 1 | `altimeter` |
| `dz/dtM` | 1 | `descent_rate` |
| `sal00` | 1 | `sea_water_salinity` |
| `sal11` | 1 | `sea_water_salinity_2` |
| `svCM` | 1 | `sound_velocity` |
| `sigma-t00` | 1 | `sea_water_sigma_t` |
| `sigma-t11` | 1 | `sea_water_sigma_t_2` |
| `flag` | 1 | `flag` |

`timeS` and `depSM` are consumed by structural `time` and `depth`, not by the
science-variable mapping.

### 8.2 Configuration, not hardcoding

All source aliases, occurrences, destination names, and destination-specific
scientific attributes must come from the conversion mapping file. The parser
and writer must not contain a chain of source-name conditionals.

The mapping is destination-keyed and has this shape:

```json
{
  "science_variables": {
    "sea_water_pressure": {
      "source_candidates": [
        {"name": "prDM", "occurrence": 1},
        {"name": "prdM", "occurrence": 1}
      ],
      "attributes": {
        "long_name": "<configured value>",
        "standard_name": "<configured value>",
        "standard_name_url": "<configured value>",
        "ioos_category": "<configured value>",
        "ncei_name": "<configured value>"
      }
    }
  }
}
```

Occurrence is mandatory even when it is `1`. This is required because every
archive cast declares `oxsatML/L` twice and the two arrays can differ.

Mapping validation must reject:

- reuse of the same `(source name, occurrence)` by two destinations;
- duplicate destinations;
- an occurrence less than one;
- a cast where more than one alias candidate for one destination is present;
- an unconsumed numerical source column.

One mapping configuration covers all ten observed schemas. Missing optional
candidates simply omit their destination variable; they do not require a
separate schema-specific mapping file.

### 8.3 Values and common attributes

- Preserve numerical values and source units; do not perform unit conversion.
- Parse units and descriptive source text from each `# name` declaration.
- If the source declares a quantity as unitless, omit `units`.
- Replace values equal to parsed `# bad_flag` with `NaN`.
- Compute `valid_min` and `valid_max` from finite output values.
- Generate common structural attributes in code: `coordinates`,
  `grid_mapping`, conditional `platform`, `coverage_content_type`, and valid
  range.
- Obtain target-specific attributes such as `long_name`, `standard_name`,
  `standard_name_url`, `ioos_category`, and `ncei_name` from the mapping file.
- Preserve the source `flag` column as source Sea-Bird data. It is not a
  QARTOD flag and this stage creates no QC variables.

## 9. Instrument and platform metadata

### 9.1 Main CTD instrument

The first CNV line is authoritative:

| CNV model text | Global `instrument` and scalar `long_name` | Scalar `make_model` |
| --- | --- | --- |
| `Sea-Bird SBE 9` | `CTD Sea-Bird SBE 9` | `Sea-Bird SBE 9` |
| `Sea-Bird SBE 25plus` | `CTD Sea-Bird SBE 25plus` | `Sea-Bird SBE 25plus` |

Do not infer `SBE-911plus` from an SBE 9 source line. The scalar main
`instrument` variable has empty `serial_number` and `calibration_date` because
the first line does not establish those values.

### 9.2 Embedded sensors

Parse the embedded Sensors XML independently from the numerical table. Each
active `<sensor>` wrapper that contains a usable sensor element becomes the
next scalar `instrumentN` variable in source order. Empty, free, unavailable,
or structurally unusable slots are omitted, and numbering remains contiguous.

Publish only:

- `long_name`;
- `make_model`;
- `serial_number` when present;
- `calibration_date` when present.

Retain serial numbers and calibration dates as source text. Do not guess a
date format for ambiguous historical values. Sensor-element to old-style
`long_name`/`make_model` mappings and science-variable `instrument` references
belong in the configurable conversion mapping, not filename or parser code.

Do not publish calibration coefficient JSON, `SensorID`, raw sensor XML, or
the entire CNV header. A companion XMLCON file is not required.

The malformed sensor block is in:

```text
cnv_data/02_CNV/WS0704_cnv/WS0704_01.cnv
```

Its numerical table remains convertible. Sensor XML failure produces a warning
and leaves only metadata that can be established independently, including the
main CTD instrument.

### 9.3 Platform

Platform is derived only from a valid `** Ship` header value. The initial
normalizations are:

| Source ship text | Canonical platform |
| --- | --- |
| `R/V FG Walton Smith` | `RV_FG_Walton_Smith` |
| `RVWS` | `RV_FG_Walton_Smith` |

When resolved, write:

- global `platform="RV_FG_Walton_Smith"`;
- global `platform_name="RV_FG_Walton_Smith"`;
- scalar `platform:long_name="RV_FG_Walton_Smith"`;
- scalar `platform:call_sign=""`;
- `platform="RV_FG_Walton_Smith"` on applicable science variables.

Do not copy the old value `WCZ6292` or infer any call sign. It remains empty
until the provider confirms it.

If `** Ship` is absent, blank, or not covered by an approved normalization,
omit cast-specific platform globals, the scalar platform variable, and
science-variable platform references. Record the value or absence for provider
review. A profile-owned XML override may set a publication-level platform value,
but it must be deliberate and must not pretend it came from that cast.

## 10. NetCDF global attributes and their XML mapping

### 10.1 Boundary

“Global attributes” here means the approved attributes that appear in each new
NetCDF and in the actual ERDDAP dataset XML. It does not mean adding every CNV
header line as a new attribute.

The NetCDF is authoritative for cast-specific metadata and ERDDAP reads those
globals as source attributes. XML generation does not duplicate them in global
`addAttributes`. Publication additions, intentional overrides, and removals are
owned by the selected dataset profile.

`station_name` is a global attribute, not another data variable. The identity
data variables are `station(profile, z)` and `cruiseID(profile, z)`.

### 10.2 Converter-owned attributes

| Global attribute | Source or rule | Example/behavior |
| --- | --- | --- |
| `title` | resolved identity plus UTC start date | `CTD data from SFER cruise WS0603, station 29.5, 2006-01-21` |
| `id` | cruise ID only | `WS0603` |
| `station_name` | complete output filename stem | `WS0603_29_5` or `WS0603_29_5-2` |
| `instrument` | normalized first CNV line | `CTD Sea-Bird SBE 9` |
| `platform` | approved `** Ship` normalization | omitted when unresolved |
| `platform_name` | same canonical platform | omitted when unresolved |
| `source` | fixed | `Rolling Deck to Repository (R2R) Program` |
| `processing_level` | fixed | `Geophysical units from raw data` |
| `featureType` | fixed | `Profile` |
| `cdm_data_type` | fixed | `Profile` |
| `cdm_altitude_proxy` | fixed | `depth` |
| `cdm_profile_variables` | generated from variables actually present | deterministic, no duplicates |
| `comment` | actual Sea-Bird processing settings | rules below |
| `history` | conversion event | rules below |
| `time_coverage_start` | `start_time + min(timeS)` | UTC ISO 8601 |
| `time_coverage_end` | `start_time + max(timeS)` | UTC ISO 8601 |
| `time_coverage_duration` | `max(timeS) - min(timeS)` | ISO 8601 duration |
| `time_coverage_resolution` | `# interval` | ISO 8601 duration |
| `geospatial_lat_min/max` | parsed profile latitude | equal for one profile |
| `geospatial_lat_units` | fixed | `degrees_north` |
| `geospatial_lon_min/max` | parsed profile longitude | equal for one profile |
| `geospatial_lon_units` | fixed | `degrees_east` |
| `geospatial_vertical_min/max` | finite `depSM` values | computed from data |
| `geospatial_vertical_units` | parsed depth units | normally `m` |
| `geospatial_vertical_positive` | fixed | `down` |
| `geospatial_bounds_crs` | fixed | `EPSG:4326` |

Latitude and longitude are parsed from degrees, minutes, and hemisphere. West
and south are negative. Do not add meaningless single-profile horizontal
resolution or irregular vertical resolution attributes merely to fill a
template. Do not write `NaN` as metadata.

The broad program polygon from older publication metadata is not a cast-derived
bound and remains profile-controlled when publication requires it. It is not generated as a new
per-cast NetCDF global.

### 10.3 Processing comment

Build `comment` from the actual per-cast processing parameters in source order.
Include useful non-date, non-input-path settings from `datcnv`, `filter`,
`alignctd`, `celltm`, `loopedit`, and `derive`. Exclude:

- keys ending in `_date`;
- keys ending in `_in`;
- Windows input paths;
- invented descriptions of steps not present in that CNV.

The `WS0603_029_5.cnv` source values are visible at lines 293-318, including
oxygen correction, filters, alignment, cell thermal mass, and loop-edit
settings. Preserve their actual values rather than copying one cruise's
comment to every file.

### 10.4 History

Add one conversion record using UTC and the source/output relative paths:

```text
<UTC>: converted <source> to <target> by CTD_QARTOD
```

Use an ISO UTC timestamp. Do not copy obsolete `ncatted` repair commands from
old files into newly created NetCDF files.

### 10.5 Missing optional metadata

Omit optional attributes that cannot be established. Do not write the literal
value `unknown`. The deliberate empty exceptions are:

- scalar `platform:call_sign=""` pending provider confirmation;
- main `instrument:serial_number=""`;
- main `instrument:calibration_date=""`.

### 10.6 Profile-owned XML metadata

The dataset profile must supply publication values absent from NetCDF, such as creator,
publisher, institution, license, project, program, keywords, references,
`infoUrl`, `metadata_link`, and platform vocabulary. These values do not come
from the cast header. Put only missing values, intentional overrides, or `null`
removals in `erddap.global_add_attributes`.

## 11. Output safety and conversion report

### 11.1 Write safety

- Write new files under a separate output root, never the old dataset root.
- Refuse to overwrite an existing target by default.
- Require an explicit overwrite option for intentional replacement.
- Write to a sibling temporary file.
- Reopen and validate the temporary NetCDF.
- Replace the final target only after successful validation.
- Remove no source or old dataset files.

### 11.2 Batch behavior

One bad cast must not stop inspection or conversion of unrelated casts. Process
the full batch, but return a nonzero command status if any included cast fails.
Exclusions do not make the command fail.

Preflight identity and repeat assignment before writes so filenames are not
dependent on filesystem traversal order or partial conversion success.

### 11.3 Report contents

The JSON conversion report records:

- inventory counts and source root;
- one disposition for every discovered `.cnv`;
- original path, canonical identity, filename token, variant, and repeat number;
- embedded filename and identity mismatch warnings;
- parsed start time and coverage;
- observed schema and resolved source-to-destination mapping;
- sensor parse status and instrument count;
- output path and validation result;
- exclusions, warnings, and failures with reasons;
- the `.2cnv` exception;
- unconsumed header keys summarized by key and count;
- unresolved provider questions.

The report is evidence of what happened; it is not an alternate source of
scientific values.

## 12. ERDDAP XML generation

### 12.1 New NetCDF datasets

A new NetCDF has one source variable named `time`, so XML exposes it directly:

```xml
<dataVariable>
  <sourceName>time</sourceName>
  <destinationName>time</destinationName>
  <dataType>double</dataType>
  <addAttributes>
    <att name="time_precision">1970-01-01T00:00:00.000Z</att>
  </addAttributes>
</dataVariable>
```

The full variable attributes, including dynamic `units`, come from the NetCDF.
The snippet is abbreviated to show the naming and XML-only precision rule.

### 12.2 Old datasets without NetCDF modification

Old NetCDF files remain byte-for-byte untouched. XML resolves their confusing
time names with this ordered rule:

| Variables in old NetCDF | XML result |
| --- | --- |
| both `time` and `time_elapsed` | omit source `time`; map source `time_elapsed` to destination `time` |
| only `time_elapsed` | map source `time_elapsed` to destination `time` |
| only `time` | map source `time` directly to destination `time` |
| neither | dataset generation error |

For the common old case, the published variable is:

```xml
<dataVariable>
  <sourceName>time_elapsed</sourceName>
  <destinationName>time</destinationName>
  <dataType>double</dataType>
  <addAttributes>
    <att name="time_precision">1970-01-01T00:00:00.000Z</att>
  </addAttributes>
</dataVariable>
```

Every generated dataset must have exactly one destination variable named
`time`. The rule belongs in the shared XML generation path so a later
generation cannot recreate the deleted old `time` mapping or reset
`destinationName` to `time_elapsed`.

When legacy `time_elapsed` has plain temporal units such as `seconds`, the
generator forms ERDDAP units as `seconds since <time_coverage_start>` and uses
the source calendar when available. Missing temporal units or coverage start is
a generation error; the generator does not invent an origin.

### 12.3 Global attributes

Validate `required_global_attributes` against the combined NetCDF globals and
configured `global_add_attributes`. Emit only the configured additions,
overrides, and `null` removals in dataset-level XML. Do not copy all NetCDF
globals into XML and do not insert `unknown` for missing optional metadata.

Variable generation must:

- mirror scientific attributes from NetCDF;
- infer `ioos_category=Quality` for QC flags when it is absent;
- add `time_precision` only to the one published destination `time`;
- format typed attribute lists with ERDDAP's space-separated syntax.

Generate the `<erddapDatasets>` root and every dataset block without reading an
existing XML or template. Server-wide branding/settings remain deployment-owned.
Use the existing `lxml` dependency, write XML atomically, and reparse the
temporary document before replacing the generated output.

## 13. Failure, warning, and exclusion policy

| Condition | Result |
| --- | --- |
| Unresolved station for a scientific cast | failure; no NetCDF |
| Invalid start time, position, table shape, or required column | failure; no NetCDF |
| Unknown numerical source column | failure; no silent column loss |
| Conflicting aliases for one destination | failure |
| Malformed embedded sensor XML | warning; convert numerical data |
| Missing or unapproved `** Ship` | warning/provider question; omit platform fields |
| Embedded `* FileName` mismatch | warning; actual path remains authoritative |
| Deck/dunk/wet/test cast | exclusion; no NetCDF; not a failure |
| Nonstandard `.2cnv` | excluded exception pending provider decision |
| Unknown nonnumerical header key | report summary; continue |
| Existing output without explicit overwrite | failure; preserve existing file |

## 14. Acceptance criteria

Implementation is complete only when all of the following are demonstrated.

### 14.1 Inventory and identity

- All 3,791 exact-extension `.cnv` files are accounted for as included,
  excluded, or failed.
- The `.2cnv` file has an explicit recorded disposition.
- Every included identity and output name is unique and deterministic.
- Tests cover numeric, decimal, named, repeated, variant, compact, mismatched,
  excluded, and unresolved filenames.
- The core converter has no legacy filename-cleaning patterns or overrides.

### 14.2 Parsing and NetCDF

- Representative tests cover all ten column schemas and both CTD models.
- Tests cover nonzero first elapsed time, duplicate `oxsatML/L`, optical source
  aliases, malformed sensor XML, missing ship metadata, and source bad flags.
- Every temporary output reopens successfully before publication.
- `profile=1`, `z` equals the source numerical row count, and there is no `z`
  variable.
- Required core variables and dimensions match Section 6.
- `time` equals source `timeS`; `time_elapsed` is absent.
- Time origin, calendar, coverage, and resolution match the CNV header and data.
- Cruise/station variables, filename, title, ID, and `station_name` agree with
  the one canonical identity.
- Both oxygen-saturation occurrences map to the approved distinct variables.
- Every source numerical column is accounted for.
- Global valid ranges and coverage come from actual finite arrays.

### 14.3 XML

- The generated XML reparses successfully.
- Every dataset has exactly one destination `time`.
- New and all three valid legacy time cases follow Section 12.
- Published time has the exact approved `time_precision`.
- Converter-owned global attributes match NetCDF values.
- Template-owned publication metadata remains unchanged.

### 14.4 Non-destructive evidence

- Hashes taken before and after conversion prove old NetCDF files are
  unchanged.
- No unfinished temporary files remain.
- The focused parser, mapping, writer, and XML checks pass.
- Repository whitespace/diff checks pass for implementation-owned files.

## 15. Provider questions retained for follow-up

These values must remain unresolved rather than inferred:

1. What call sign should be published for `RV_FG_Walton_Smith`? Until
   confirmed, write the scalar platform call sign as an empty string.
2. For Captiva Bluehole, which of the differing `.cnv` and `.2cnv` numerical
   versions is authoritative?
3. What platform should be assigned to casts with no usable `** Ship` header,
   including the SBE 25plus AMI3 example? Until confirmed, omit cast-derived
   platform metadata.

## 16. Implemented sequence

The smallest end-to-end implementation sequence is:

1. implement the detachable identity preparer and review its complete manifest;
2. implement CNV structural parsing and mapping validation;
3. write and reopen one representative NetCDF with the approved time model;
4. expand to all schemas, sensors, globals, atomic batch output, and reporting;
5. generate XML for new and legacy time plus profile-owned global additions;
6. run the full non-destructive acceptance suite against the archive.

This sequence was followed: filename preparation and mapping remain separate,
and the archive-wide identity review precedes source-NetCDF conversion or XML
publication work.
