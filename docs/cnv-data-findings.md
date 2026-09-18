# NOAA AOML Sea-Bird CNV Data Findings

> Historical source/design record, not the current conversion contract. Source observations below are retained, but old mappings, timeQ/coordinate reduction rules, metadata ownership and CLI examples are superseded. Use the [current conversion process](cnv-conversion-process.md), [automatic workflow](automatic-cnv-workflow.md), and [shared template guide](../config_template/README.md). The current converter requires schema 5 and uses timeS, source-specific coordinate dimensions, and catalog-owned attributes.

This record describes the supplied cruise directory
`cnv_data/2026_07_Hogarth_NOAA_CTD`, audited on 2026-08-09. Conversion and QC
configuration status was updated on 2026-08-18 to match the implemented
configurable CNV workflow.

## Cruise inventory

- Cruise identifier: `HG26193`.
- Platform: `R/V W.T. Hogarth`, verified against the
  [Florida Institute of Oceanography vessel page](https://www.fio.usf.edu/research-vessels/r-v-hogarth/).
- 95 processed `.cnv` casts containing 143,366 sample rows.
- 97 `.XMLCON`, 97 `.hdr`, 97 `.bl`, 96 `.hex`, and 91 conventionally named
  `.mrk` files. Two additional station-063 files omit the separator before
  `bl`/`mrk`; the report classifies these as other companion files.
- 95 CNVs use one identical 22-column schema. Declared row and column counts
  match every file, elapsed time is monotonic, no declared bad sentinel occurs,
  and the supplied Sea-Bird `flag` is zero throughout.
- Cast sizes range from 222 to 11,714 samples. Maximum cast depths range from 1.845 m to 942 m.
- 93 unique standardized station IDs are present. `BG16` occurs at source
  sequences 030 and 031; `V7` occurs at sequences 023 and 024. Sequence order
  produces `HG26193_BG16.nc`, `HG26193_BG16-2.nc`, `HG26193_V7.nc`, and
  `HG26193_V7-2.nc`.
- XMLCON/raw records exist without processed CNVs for station 063
  (`HG26193_STN.063_073`) and `Test` (`HG26193_TEST`). They are findings, not
  conversion failures, because no CNV measurement table exists to convert.
- All 95 embedded active-sensor signatures are identical by channel, sensor
  type, serial number, and calibration date. Each also matches its companion
  XMLCON by sensor type, serial number, and calibration date.

## What CNV contains

A `.cnv` file is Sea-Bird's processed ASCII engineering-unit product created
by the Data Conversion (`DatCnv`) step. It is not the raw instrument stream;
the `.hex` file is the raw source. Each CNV contains a human-readable header,
an embedded sensor/calibration XML block, processing records, and a numeric
sample table.

The supplied schema is:

| Index | CNV field | Header meaning and units |
| ---: | --- | --- |
| 0 | `scan` | Scan count |
| 1 | `depSM` | Salt-water depth, m |
| 2 | `c0S/m` | Conductivity, S/m |
| 3 | `t090C` | ITS-90 temperature, degrees C |
| 4 | `sal00` | Practical salinity, PSU |
| 5 | `oxsatMg/L` | Oxygen saturation, Weiss, mg/L |
| 6 | `sbeox0Mg/L` | SBE 43 oxygen, mg/L |
| 7 | `flECO-AFL` | WET Labs ECO-AFL/FL fluorescence, mg/m3 |
| 8 | `wetCDOM` | WET Labs CDOM fluorescence, mg/m3 |
| 9 | `turbWETntu0` | WET Labs ECO turbidity, NTU |
| 10 | `density00` | Density, kg/m3 |
| 11 | `svCM` | Chen-Millero sound velocity, m/s |
| 12 | `latitude` | Per-scan NMEA latitude, degrees |
| 13 | `longitude` | Per-scan NMEA longitude, degrees |
| 14 | `oxsolMg/L` | Garcia and Gordon oxygen solubility, mg/L |
| 15 | `timeS` | Elapsed seconds |
| 16 | `timeM` | Elapsed minutes |
| 17 | `timeH` | Elapsed hours |
| 18 | `timeJ` | Julian day-of-year value |
| 19 | `timeQ` | NMEA seconds since 2000-01-01 |
| 20 | `altM` | Altimeter range, m |
| 21 | `flag` | Sea-Bird processing flag |

The header also provides source HEX/XMLCON paths, Sea-Bird software versions,
upload/start/NMEA time, NMEA position, sample interval, declared counts,
bad-value sentinel, column spans, and DatCnv settings.

The first header line explicitly identifies the CTD system as a Sea-Bird SBE
25plus. Eight active sensor records and their calibration values are embedded
in every CNV:

| Component and source-reported type/model | Channel | SensorID | Serial number | Calibration date | Calibration fields present |
| --- | ---: | ---: | --- | --- | --- |
| CTD system, Sea-Bird SBE 25plus | n/a | n/a | not supplied | not supplied | n/a |
| Temperature, `TemperatureSensor` | 1 | 55 | `6623` | `24-Jul-25` | `G`, `H`, `I`, `J`, `F0`, slope, offset |
| Conductivity, `ConductivitySensor` | 2 | 3 | `0351` | `23-Jul-25` | `G`, `H`, `I`, `J`, `CPcor`, `CTcor`, slope, offset |
| Pressure, `PressureSensor` | 3 | 46 | `1230` | `11-Sep-25` | `PA*`, `PTEMP*`, `PTCA*`, `PTCB*`, offset |
| Oxygen, SBE 43 / `OxygenSensor` | 4 | 38 | `1959` | `18-Jul-25` | `Soc`, offset, `A`-`E`, `Tau20`, `H1`-`H3` |
| WET Labs ECO CDOM | 6 | 19 | `FLCDRTD-1111` | `25-Jul-2025` | scale factor, blank voltage |
| WET Labs ECO-AFL/FL | 8 | 20 | `FLNTURTD-2022` | `24-Jul-2025` | scale factor, blank voltage |
| WET Labs ECO-NTU turbidity | 9 | 67 | `FLNTURTD-2022` | `24-Jul-2025` | scale factor, dark voltage |
| `AltimeterSensor`; exact model unavailable | 10 | 0 | not supplied | `2020` | scale factor, offset |

Channels 5, 7, and 11 are unused. Channels 8 and 9 share a serial number and
represent two measurement channels from the same combined optical instrument.
`SensorID` is a Sea-Bird configuration code, not a self-describing model name;
an exact model is not inferred from it.

The calibration fields are coefficients used by Sea-Bird to produce the
engineering-unit values. They are not measurement ranges or QARTOD limits. The
converter writes the complete flattened coefficient set as sorted JSON on each
scalar `instrument<n>` variable and does not use it to recalculate supplied
DatCnv values. The current converter preserves sensor type, channel, serial
number, calibration date, and coefficients; `SensorID` remains available in
the source XML but is not yet written to NetCDF.

The CNV does not provide a formal variable-to-sensor foreign key. Direct
measurement relationships can be established for this cruise from Sea-Bird
field names, column descriptions, channels, and sensor types. Derived fields
such as salinity, depth, density, sound velocity, and oxygen solubility depend
on multiple inputs and must not be assigned to one sensor as provenance.

## Processing actually recorded

The headers record Sea-Bird Data Conversion, input HEX/XMLCON paths,
`datcnv_skipover = 0`, oxygen hysteresis correction, oxygen tau correction,
and ASCII output. Those are the only processing stages claimed in output
provenance.

Unlike representative older netCDF files, these CNVs do not record Filter,
Align CTD, Cell Thermal Mass, Loop Edit, or Bin Average settings. The converter
does not claim or recreate those stages. If NOAA later supplies processing
configuration or a PSA file, reproducing those operations should be a separate
scientifically validated change.

The CNV `flag` is preserved and explicitly labelled as a Sea-Bird source flag;
it is not treated as QARTOD. The existing QC runner remains the only component
that creates `*_qc_*` variables.

## Configured source-to-NetCDF mapping

| CNV field | netCDF variable | Output units | Treatment |
| --- | --- | --- | --- |
| `depSM` | `depth` | `m` | Preserved; positive down |
| `c0S/m` | `sea_water_electrical_conductivity` | `S/m` | Preserved unchanged |
| `t090C` | `sea_water_temperature` | `deg C` | Preserved unchanged |
| `sal00` | `sea_water_salinity` | `PSU` | Preserved unchanged |
| `sbeox0Mg/L` | `dissolved_oxygen` | `mg/l` | Preserved unchanged |
| `oxsolMg/L` | `oxygen_saturation` | `mg/l` | Preserved unchanged |
| `oxsatMg/L` | `oxygen_saturation_2` | `mg/l` | Preserved unchanged |
| `flECO-AFL` | `chlorophyll_concentration` | `mg/m^3` | Preserved unchanged |
| `wetCDOM` | `CDOM` | `mg/m^3` | Preserved unchanged |
| `turbWETntu0` | `sea_water_turbidity` | `NTU` | Preserved |
| `density00` | `sea_water_density` | `kg/m^3` | Preserved as density |
| `svCM` | `sound_velocity` | `m/s` | Preserved |
| `altM` | `altimeter` | `m` | Preserved |
| `scan` | `scan` | `1` | Preserved |
| `flag` | `flag` | `1` | Preserved, non-QARTOD |
| `timeS` | `time_elapsed` | `seconds` | Preserved |
| `timeM` | `seabird_elapsed_minutes` | `minutes` | Preserved |
| `timeH` | `seabird_elapsed_hours` | `hours` | Preserved |
| `timeJ` | `seabird_julian_day` | `days` | Preserved |
| `timeQ` | `time` | seconds since 1970-01-01 | Configured epoch offset applied to every sample |
| `latitude` | `latitude_sample` | `degrees_north` | Every scan preserved |
| `longitude` | `longitude_sample` | `degrees_east` | Every scan preserved |

Station identifiers are uppercased for named stations, numeric leading zeroes
are removed, and meaningful decimals are retained. Decimal points become
underscores only in filenames. Profile latitude/longitude are medians of the
per-scan values; global ranges retain the full per-scan coverage. Profile time
contains every `timeQ` sample plus exactly 946,684,800 seconds and uses the
Gregorian calendar.

## Metadata source matrix

| Field | CNV | Derived | Configured | Unavailable |
| --- | :---: | :---: | :---: | :---: |
| Cruise, source station, sequence | filename | canonical identifiers/output stem |  |  |
| Measurements, source descriptions/units | yes | configured NetCDF names; values and parsed source units preserved | CNV `# name` declarations and `config/hogarth_cnv/cnv_mapping.json` |  |
| Cast time and spatial coverage | per-scan values | profile coordinates and min/max |  |  |
| CTD and sensor identity | SBE 25plus header; XML channels, types, SensorID, serials, calibration dates | scalar instrument variables, excluding SensorID |  | exact model for some sensors; CTD and altimeter serials |
| Sensor calibration coefficients | embedded XML | flattened calibration JSON |  | calibration certificates and traceability records |
| Direct variable-to-sensor relationship | field names and descriptions, but no formal key |  | dataset sensor map for QC lookup only | authoritative relationship for derived fields |
| DatCnv and oxygen-correction provenance | yes | normalized provenance text |  |  |
| Platform long/short name |  |  |  | yes; official FIO source available at the publication boundary |
| Program, project, institution, naming authority |  |  |  | yes |
| Publisher, license, sea name, country |  |  |  | yes |
| Creator and contributor identity/contact |  |  |  | yes |
| References, acknowledgment, info URL |  |  |  | yes |
| ERDDAP metadata URL |  |  |  | unavailable until publication |
| Creation/issue/modified dates and product version |  |  |  | yes; conversion time is not substituted |
| Fixed program-level geospatial bounds |  | per-cast bounds only |  | yes |
| Undocumented Sea-Bird post-processing stages |  |  |  | yes |
| QARTOD limits and thresholds |  |  | empty Hogarth templates | yes; scientific approval required |

The converter intentionally omits publication metadata that cannot be recovered
from CNV: platform names and registry identifiers/call sign; creator and
contributor details; references and acknowledgment; publication and metadata
URLs; publication dates and product version; fixed program bounds; and any
processing history not present in the CNV. These values can be added later at
the publication boundary. Unknown optional values are not filled with `other`,
copied from an unrelated cruise, or inferred from the conversion date.

## Scientific transformations and exclusions

The converter performs no measurement-unit conversion. Science arrays,
including all three oxygen fields, retain the values and units declared in the
CNV mapping. Structural conversion is limited to the configured `timeQ` epoch
offset, latitude/longitude median profile coordinates while retaining every
sample, and station/filename normalization.

`density00` is absolute density in kg/m3 and is written as
`sea_water_density`; it is not sigma-t. No `sea_water_pressure` is fabricated:
the CNV has pressure-sensor calibration metadata but no pressure measurement
column. No secondary temperature, conductivity, or salinity channels are
created because they are absent from this schema.

## QC configuration status

The Hogarth gross-range, spike, rate-of-change, and location files are complete
templates, but all unverified limit fields are `null`. Station climatology is
also empty. Those tests therefore report `NOT_EVALUATED`; they do not inherit
Walton limits. Source units and variable names remain in the templates so
approved dataset-specific values can be added later without changing the
converter. Flat-line configuration remains populated and is separate from
these empty-limit templates.

Any QC NetCDF files generated before the limits were emptied contain obsolete
flags and must be regenerated before inspection or publication.

## Operational handoff

Convert without altering the source directory:

```bash
PYTHONPATH=. uv run python main.py convert-cnv \
  --profile config/hogarth_cnv/dataset_profile.json \
  --input-dir cnv_data/2026_07_Hogarth_NOAA_CTD \
  --output-dir output/SFER_CNV
```

Inspect `output/SFER_CNV/conversion_report.json` before QC. A malformed cast is
listed under `failed`, successfully converted casts remain available, and the
command exits nonzero. Existing netCDF outputs are skipped unless
`--overwrite` is supplied. Missing station-063/Test CNVs are reported but do
not cause failure.

Then use the existing netCDF workflow:

```bash
PYTHONPATH=. uv run python main.py qc \
  --profile config/hogarth_cnv/dataset_profile.json
PYTHONPATH=. uv run python main.py viz \
  --profile config/hogarth_cnv/dataset_profile.json
PYTHONPATH=. uv run python main.py erddap-xml \
  --profile config/hogarth_cnv/dataset_profile.json
```

Conversion creates source netCDF only. QC output remains separate under the
profile's configured output directory and is the tree to visualize or publish.

## Known limits

- A future NOAA CNV schema may add, remove, or rename columns. Unknown columns
  appear in `conversion_report.json` and require an explicit scientific mapping
  review; they are never silently relabelled.
- The shared station-coordinate table covers 92 of 93 unique converted station
  IDs, but location remains `NOT_EVALUATED` for every station while tolerance
  is `null`. Climatology remains `NOT_EVALUATED` for every station while its
  configuration is empty. `BG19` also lacks an expected station coordinate.
- Companion validation confirms instrument identity fields, not byte-for-byte
  equality of every XMLCON setting.
- `SensorID` is present in source XML but not yet retained by the converter.
  The SBE 25plus system model is source-supported for this cruise but currently
  written as a converter constant rather than parsed as a general CNV field.
- CNV is already processed. This path accelerates publication but cannot audit
  or reproduce undocumented Sea-Bird processing performed before NOAA wrote
  the CNV.
