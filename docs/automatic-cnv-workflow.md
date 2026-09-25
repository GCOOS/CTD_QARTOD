# Automatic CNV conversion, dataset-owned QC limits, and XML

Work from `/Volumes/Crucial X9/CTD_QARTOD`. The standard workflow is:

```bash
python main.py convert-cnv cnv_data/WS24258
python main.py qc --profile config/ws24258/dataset_profile.json
python main.py erddap-xml --profile config/ws24258/dataset_profile.json
```

Conversion accepts one file, one cruise folder, or a multi-cruise input folder.
Without `--profile`, it discovers/creates `config/<scope>/dataset_profile.json`.
One recognized cruise uses its cruise ID as the scope; a collection uses its
input folder name. An explicit profile remains the way to choose other paths.
Existing profiles and reviewed mappings are never silently overwritten.
Inspect the complete intended scope before incremental conversion, so the
mapping includes variables that occur only in later files.

For inspection without conversion:

```bash
python main.py inspect-cnv cnv_data/02_CNV --output output/02_cnv_mapping.json
```

If `--output` is omitted, inspection selects `config/<scope>/cnv_mapping.json`.
An existing mapping is protected; choose a new output path when re-inspecting.
Failed headers are recorded and inspection exits nonzero. Valid NMEA header
coordinates are supported without preparation. Invalid/missing coordinate sources
are listed in `inspection.conversion_issues`; `inspection.coordinate_sources`
counts files using `data_columns` versus `nmea_header`.

## Variable catalog and units

`config_template/cnv_catalog.json` is keyed by the Walton Smith measurement name, such as
`variables.sea_water_temperature`, **not** the QC category `temperature`. Each
definition contains CNV source aliases, description checks, permitted units,
known metadata and sensor tags. QC category membership is derived from
`config_template/qc_variable_mapping.json` when settings are
initialized. The resulting category map is saved in the dataset folder. Existing
NetCDF names such as `seawater temperature`, `temperature_1`, and `temperature_2`
can be recognized without changing those existing files. New CNV conversions
write the Walton measurement names.
The historical `02_CNV` archive supplies the evidence for aliases such as:

| Source codes | Output base |
| --- | --- |
| `xmiss`, `CStarTr0` | `beam_transmission` |
| `bat`, `CStarAt0` | `beam_attenuation` |
| `t090C`, `t190C` | `sea_water_temperature` |
| `c0S/m`, `c1S/m` | `sea_water_electrical_conductivity` |
| `prDM`, `prdM` | `sea_water_pressure` |
| `sal00`, `sal11` | `sea_water_salinity` |
| `sigma-t00`, `sigma-t11` | `sea_water_sigma_t` (not full density) |
| `flSP` | `seapoint_fluorescence` |
| `flECO-AFL` | `chlorophyll_fluorescence` |

The ECO-AFL/FL measurement belongs to QC category `chlorophyll`; the unitless
Seapoint measurement is retained without QC. Likewise, `t090C` maps to
`sea_water_temperature`, which belongs to QC category `temperature`. Duplicate
destinations are numbered after mapping (`sea_water_temperature`,
`sea_water_temperature_2`), including different source codes in the same cast.
Values are unchanged. Original source names, descriptions, occurrences, and changed
unit spellings remain in variable provenance. Known aliases are matched only when
their descriptions and units agree. Unknown/conflicting sources have `mapped_to: null`, which blocks conversion.
Add or correct their definition in the shared catalog, then select the catalog
key in the dataset mapping or inspect again into a new inventory file.

No numeric unit conversion is implemented. Only equivalent units with unchanged
numeric values are normalized. Every file is checked against the selected catalog definition, including its
actual description and units; a stale inventory cannot silently relabel incompatible data. Missing units remain missing. For example, `par` may
be `W m-2`, `umol m-2 s-1`, or unspecified; inspection lists the observed units and conversion preserves/normalizes each
source's units independently.
It never converts energy flux to photon flux or invents fluorescence units.

Metadata is resolved from the shared catalog at conversion time, not copied into
individual inventories. Edit standard definitions once in `cnv_catalog.json`.
Standard-name URLs are derived using pinned CF table v94; syntax validation is
not full CF compliance validation. Catalog sensor associations are used only
when the sensor tag is present in the inspected sensor inventory.

A schema-5 inventory entry contains only observations and its selected catalog key:

```json
"t090C": {
  "observed": {
    "file_count": 31,
    "max_occurrences_per_file": 1,
    "descriptions": ["Temperature [ITS-90, deg C]"],
    "units": ["deg C"],
    "missing_units": false
  },
  "mapped_to": "sea_water_temperature"
}
```

There are no per-dataset `attributes`, `sensor_tag`, or routine `action` fields.
An intentional exclusion uses `"ignore": true` with `"mapped_to": null`.
The required fields, vertical selection, and inspection diagnostics remain.
Catalog changes affect future conversions, not NetCDF files already written.

## Latitude and longitude: source determines dimensions

The converter automatically resolves coordinates separately for every file:

| CNV source | NetCDF latitude/longitude dimensions | Meaning |
| --- | --- | --- |
| One `latitude` and one `longitude` data column | `(profile, z)` | Original measured positions for each sample |
| Neither column, but one valid `NMEA Latitude` and `NMEA Longitude` header | `(profile)` | One position for the entire cast |

For example, `24 42.75 N` becomes `24.7125` degrees north, and `080 49.95 W`
becomes `-80.8325` degrees east. Minutes must be below 60, hemispheres must match
their axes, and the resulting coordinates must be within latitude/longitude
bounds. These are actual NetCDF variables with `standard_name`, `units`, `axis`
and a `source_header` provenance attribute. Global coverage bounds and title/
summary positions use the same selected coordinates.

The coordinate roles in `cnv_mapping.json.required_fields` keep their standard
names; no manual coordinate-source setting is needed. A complete column pair
takes precedence over NMEA headers. A partial or duplicated column pair is
rejected, and invalid measured coordinates are not replaced by header values.
Missing, duplicate or malformed required header coordinates also block conversion.
The conversion report records `coordinate_source` for each parsed cast.

Header positions are not repeated into fabricated per-sample positions. Elapsed
`timeS`, depth and science values remain `(profile, z)` with their original
samples; a nonzero elapsed-time start is retained. QC broadcasts cast-level
positions internally as needed, without changing their saved NetCDF dimensions.
The same mapping can serve a folder containing both coordinate layouts.

## Global metadata ownership

| Profile section | Behavior |
| --- | --- |
| `netcdf_global_attributes` | Human-provided creator/contributor, acknowledgment, product version, geographic polygon. Nulls are omitted. |
| `netcdf_fixed_global_attributes` | Configured SFER publication and convention values, including institution, publisher and license. |
| `netcdf_derived_global_attributes` | Registered computed fields. Values describe derivations; title/summary are actual format templates. |

Attributes cannot have multiple owners. Fixed or human fields cannot override
derived identifiers, time/spatial coverage, processing history, or platform.
The single reusable `config_template/dataset_profile.json` contains the SFER fixed
defaults and populated human defaults copied from the approved WS24258 profile. New profiles
receive their own editable copy: creator/contributor fields, acknowledgment,
product version, and geographic polygon, including existing literal `"unknown"`
values. These are supplied defaults, not facts inferred from the new CNV.
Review the creator and polygon before publishing a different dataset; set any
inapplicable human attribute to null to omit it. Existing profiles are never
repopulated automatically, so human edits and deliberate nulls survive reruns.

`title`/`summary` accept `{id}`, `{cruise}`, `{station_name}`, `{date}`, `{position}`,
and `{sea_name}`. They must contain `{id}`. The identifier is `SFER_CTD_<output-stem>`;
date is the CNV start date; display position is the first coordinate sample or the NMEA cast position,
rounded to four decimals with hemisphere letters. Coverage min/max uses all samples.
The other derived-section descriptions are not evaluated as expressions or copied
as literal attributes. This avoids publishing example dates or another cast's ID.

Platform prefixes: WS -> `RV_FG_Walton_Smith`, WB -> `RV_Weatherbird_II`,
SV/SAV -> `RV_Savannah`, HG -> `RV_hogarth`. Unknown prefixes require review.
Vessel ownership does not replace the configured NOAA dataset institution.

`date_created` is conversion UTC; QC updates `date_modified` and
`date_metadata_modified` while preserving creation time and appending history.
`date_issued` is omitted: local conversion/XML preparation is not proof of an
actual publication event. No historical example timestamp is copied.

WS24258's supplied bounding polygon was repaired to longitude/latitude WKT for
the same rectangle (longitude -83 to -80, latitude 24 to 26). It remains human-owned.

## Names, locations, and preservation

Typical defaults:

```text
config/ws24258/dataset_profile.json
config/ws24258/cnv_mapping.json
config/ws24258/variable_mapping/walton_mapping.json
config/ws24258/variable_mapping/source_variable_mapping.json
output/WS24258_CNV/WS24258/WS24258_10.nc
output/WS24258_QC/WS24258/WS24258_10.nc
output/erddap/ws24258_datasets.xml
```

Recognized filename forms include `_Stn.`, `.Stn`, `Sta`, `Stv`, and cruise/station
underscore or hyphen separators. Identity comes from the actual filename,
never the embedded `FileName`. Leading numeric zeros are removed; source alias
`054b` becomes `54-2`; `021LK` becomes `21LK`. Cruise IDs with a letter suffix,
such as `WS2425A`, are also accepted. Named and decimal stations are preserved.
Conflicting cruise folder/basename identities, ambiguous multi-part station tokens, and test/
recast markers require explicit preparation; station renaming beyond the explicit
filename aliases is not automatically applied.

Initial repeats are ordered by start time and source path. The persistent
`conversion_index.json` records absolute source path -> relative NetCDF filename,
so later incremental runs keep prior assignments and allocate `-2`, `-3`, etc.
Moving sources requires reviewing this index; it is not content-based deduplication.
Existing outputs are protected unless `--overwrite` is explicitly supplied.

Scanning a processing tree prefers a sibling `06-drv` over numbered intermediate
stages. Selecting a stage directly is explicit. Other duplicate copies are not
assumed identical; select one authoritative input tree. The ERDDAP server base
path stays in the profile; the program cannot infer the deployment filesystem.

## Independent QC settings for every dataset

There is no `qc_limits` switch. A new automatic profile initially has only its
CNV mapping path. After successful CLI conversion (or on the first QC call), the
program inventories the actual NetCDF variable names and units, creates independent
Walton Smith-based settings from `config_template/` directly under `config/<dataset>/`, and saves their
paths in the profile. This happens after conversion because the generated settings
must address the actual channels and units. Profiles with explicit QC paths are
used as configured, not regenerated; missing configured files are validation errors. Runtime QC never redirects
paths to Walton Smith. Existing custom profiles continue to use their explicit
settings; editing a dataset limit does not change the template or other datasets.

The generated `source_variable_mapping.json` groups observations by canonical
name, then relative NetCDF filename. Each record preserves the actual NetCDF
variable name, original source file/name/occurrence, and original/normalized units.
This is a reversible name-and-provenance lookup, not a CNV export writer. For
example, `sea_water_temperature` can contain `sea_water_temperature` and
`sea_water_temperature_2` from one cast, both originally named `t090C` with
occurrences 1 and 2. New datasets use `qc_variable_mapping.json` to map QC
categories to actual file variable names, following the shared template layout.
Existing WS24258/SAV1803 profiles retain their saved `variable_mapping/walton_mapping.json`
paths; those are independent files and do not require rewriting.

The copied limits are measurement/unit policies, not claims that a new instrument
is the original Walton Smith model. Missing/incompatible units, absent station
references, and unavailable thresholds produce `NOT_EVALUATED`, not an invented
pass. Not every catalog variable has a supported QC category or every test.
Instrument metadata is left unchanged. Review `limit_report.json` for gaps.

Walton Smith rate-of-change thresholds remain per adjacent sample, not per second;
they are not rescaled for different sampling intervals. The default is the policy
requested by the user, not automatic scientific certification for every cruise.

The usual conversion/QC commands initialize settings automatically. To explicitly
create another independent snapshot from converted NetCDF, choose a new folder:

```bash
python main.py generate-limits --profile config/ws24258/dataset_profile.json --output config/ws24258_alternative
python main.py qc --profile config/ws24258_alternative/dataset_profile.json
```

`generate-limits` creates variable mappings, unit-specific gross ranges, spike,
rate-of-change, flat-line, climatology, station coordinates/classification,
location settings and a provenance/review
report. Without `--output`, it writes beside the dataset profile and saves the
local QC paths. With an explicit new `--output` folder, it writes another
profile and leaves the original profile untouched. Neither mode changes the
shared `config_template/` defaults. Existing nonempty folders are protected, and later QC
runs never regenerate or overwrite edited settings. Inspect the whole intended
dataset before initialization; a snapshot's mappings describe files present then.
Missing template gross ranges are explicit null placeholders. The older
`generate-sensor-config` command remains a null-limit skeleton utility, not the
standard template-copy workflow.

For example, a new `WS2425A` dataset is arranged as follows (configuration folder
names are lowercase):

```text
config/ws2425a/
  dataset_profile.json
  cnv_mapping.json
  limit_report.json
  qc_variable_mapping.json
  variable_mapping/
    source_variable_mapping.json
  gross_range_test/
    sensor_specs.json
    variable_sensor_map.json
  climatology_test/
    station_climatology_config.json
    station_depth_classification.json
  location_test/
    Station_Mean_Coords.csv
    location_config.json
  spike_test/spike_thresholds.json
  rate_of_change_test/rate_of_change_thresholds.json
  flat_line_test/flat_line_config.json
```

There is no inner `qc/` folder and no separate per-cast override file. To change
limits, edit the relevant test configuration directly in this dataset folder,
then rerun QC. Both batch QC and single-test previews use those saved settings.
Changes apply to the matching variables in this dataset, without changing Walton
Smith or another dataset. No numeric unit conversion occurs.

## Time decoding for climatology

NetCDF time values are numbers whose meaning comes from their `units` and
`calendar`. `0 seconds since 2024-09-15` is September 15, 2024, not January 1,
1970. The old numeric-to-datetime cast incorrectly assumed Unix seconds and could
select winter limits for a September CNV cast.

QC now decodes only the selected time coordinate using xarray's CF decoder, then
broadcasts real timestamps to the science variable's dimensions. The original
numeric time values, units, calendar, fractional seconds and science values are
preserved in the saved NetCDF. Original Walton files already using seconds since
1970 retain their intended dates. Missing times are `NOT_EVALUATED`; missing
science values are `MISSING`. Missing time units or unsupported calendars cause
a clear error when a time-dependent test needs them, rather than guessed dates.
Supported calendars are `standard`, `gregorian` and `proleptic_gregorian`, for
dates representable by NumPy. Climatology still requires depth and applicable
limits. Rate-of-change remains per adjacent sample; this fix does not change it
to a per-second test.

## XML and verification boundary

XML still comes from final NetCDF, not CNV or a previous datasets.xml.
The source globals are inherited by ERDDAP; `erddap.global_add_attributes`
contains only intentional XML additions/removals. New profile dataset IDs use
the `SFER_CTD_` prefix to align with NetCDF IDs. No live ERDDAP deployment/load
test is performed by local XML generation.

Historical `02_CNV` files with valid NMEA header positions can now convert directly
when their other fields satisfy the source contract. This does not resolve
ambiguous station identities, missing scientific units, or unknown source variables.
WS24258 currently has 88 usable files plus header-only 053 and 056. Invalid input
files remain reported failures and cause a nonzero conversion exit; they are not
silently skipped or assigned invented observations.

### Verified WS24258 results

The active `config/ws24258/dataset_profile.json` now uses the dataset-root layout.
Its human/fixed/derived metadata sections and existing numerical QC limits were
preserved. The conversion refreshed 88 files and reported the two invalid headers;
QC completed on all 88 valid files, and the XML contains 88 dataset entries.
All source science-column values were compared with the previous 88 NetCDF files
and were unchanged. Separate source-versus-QC checks on stations 10, 49 and 60
also confirmed unchanged source arrays and encoded time attributes.

At station 10, 2,333 temperature samples change from `SUSPECT` under the incorrect
1970/January interpretation to `PASS` under the correct September climatology
limits. This describes the climatology test, not every test or aggregate flag.
Twenty samples from the original supplied NetCDF collection retained identical
climatology flags with the corrected time decoder. The unitless Seapoint `flSP`
measurement is now retained as `seapoint_fluorescence` without QC. The ECO-AFL/FL
measurement supplies `chlorophyll_fluorescence`; its WS24258 gross range uses the
Walton Smith `mg m-3` limit only when the source and sensor match.
WS24258 source station `54b` becomes stored station `54-2` at conversion. That reference is the
mean of 11 historical casts explicitly named `54-2` under
`cnv_data/SFER_CTD/DATA/01-SFER_CTD_SOAK_REMOVED_DATA/`.

The previous WS24258 configuration, nested QC folder, superseded category map,
and generated NetCDF/QC outputs were retained under
`output/ws24258_before_catalog_layout_MzpLy4/`. No original CNV inputs were changed.
Detailed three-cast and full-cruise source-value checks are recorded under
`output/ws24258_catalog_time_verification_GkB0w8/`. These are local verification
artifacts, not ERDDAP deployment receipts.

### Verified SAV1803 header-coordinate workflow

`cnv_data/02_CNV/SAV1803_cnv` now runs through the standard commands without
manual coordinate preparation or variable mapping:

```bash
python main.py convert-cnv cnv_data/02_CNV/SAV1803_cnv
python main.py qc --profile config/sav1803/dataset_profile.json
python main.py erddap-xml --profile config/sav1803/dataset_profile.json
```

The first run created dataset-owned settings under `config/sav1803/`, 31 source
NetCDF files under `output/SAV1803_CNV/`, 31 QC files under `output/SAV1803_QC/`,
and 31 XML dataset entries in `output/erddap/sav1803_datasets.xml`. Existing
conversion outputs remain protected; a deliberate rerun requires `--overwrite`.
All files store latitude/longitude on `(profile)` and retain time, depth and
science on `(profile, z)`. All 558 mapped source arrays were checked against the
CNV values, including both occurrences of `oxsatML/L`, and all source-variable
arrays were unchanged by QC. The station-12 coordinates also match the checked
supplied `SV18067_12.nc` coordinate values and dimensions. Cruise/station naming
still follows the actual CNV filenames; no historical identifier reinterpretation
was performed.

Completing the run does not mean every test was evaluated or passed. PAR has no
declared unit, so its gross-range test remains `NOT_EVALUATED` in all 31 casts.
The filename converter maps `21` to `21LK`, `21-5` to `21_5`,
`57-1` through `57-3` to `57_1` through `57_3`, and `MRv2` to `MR-2`.
During both location and climatology QC, stored `9_5` uses the `9.5` reference
and stored `57_2` uses `57.2`. The `MR-2` reference is the
mean of five historical casts explicitly named `MR-2` under
`cnv_data/SFER_CTD/DATA/01-SFER_CTD_SOAK_REMOVED_DATA/`. Climatology class
membership remains a separate dataset setting.


## Schema-5 configuration verification

The active WS24258 and SAV1803 inventories now contain observations and catalog
keys, without duplicated scientific attributes. Their profiles have no `qc_limits`
entry. Existing per-test settings and historical NetCDF/XML outputs were retained.
SAV1803's previously empty human globals were populated with the approved defaults;
future profile creation copies the shared template, and reruns preserve edits.

Verification inspected all 31 SAV1803 headers and processed only station 12 through
conversion, QC, and XML in an isolated output folder. Compared with the earlier
station-12 files, all 37 original variables retained their values, dimensions and
attributes, and all 110 QC flag arrays were identical. All 23 human global defaults
were present in both the converted and QC NetCDF. The receipt is
`output/config_schema5_verification_gd6uulzn/verification.json`.
The test suite passed: 257 tests, 12 skipped, one slow full-archive test deselected.
The previously documented missing PAR units and station-reference gaps are not
resolved by this configuration cleanup.


## Consolidated defaults and current acceptance

All reusable defaults now live in `config_template/`: `dataset_profile.json`,
`cnv_catalog.json`, `qc_variable_mapping.json`, and populated per-test files.
The former `config/cnv_catalog.json` and `config/cnv_profile_template.json` were
removed after consolidation. New configuration generation reads the template,
not the live Walton Smith dataset. Existing saved configs remain independent.

The consolidated-template test run passed 259 tests, with 12 skipped and one
slow full-archive test deselected. A template limit-change regression confirms
new datasets use the edited template rather than Walton Smith's live limits.
A real SAV1803 cast completed conversion -> QC -> XML, with all 43 existing
SAV1803, WS24258 and Walton Smith configuration files preserved.

The preceding full SAV1803 rerun produced 31 converted files, 31 QC files and
31 XML entries without failures. Comparison with the old outputs verified
1,147 unchanged original variable arrays and 3,410 unchanged QC flag arrays;
all 23 human metadata defaults were present in every rebuilt file. Old outputs
were moved to a recoverable local backup, not committed to Git. Receipts under
`output/` are local verification artifacts and are intentionally untracked.
