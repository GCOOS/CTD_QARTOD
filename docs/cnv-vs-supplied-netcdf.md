# CNV-generated NetCDF versus the supplied NetCDF

## Scope of this comparison

There is no provider-supplied WS24258 NetCDF in this repository. The concrete
comparison therefore uses:

- supplied/reference NetCDF:
  `datasets/SFER_DIMENSIONSandCALENDAR_FIXED/WS0603/WS0603_2.nc`;
- CNV-generated NetCDF:
  `output/WS24258_CNV/WS24258/WS24258_2.nc`;
- source CNV:
  `cnv_data/WS24258/WS24258_Stn.002.cnv`.

The files belong to different cruises, so different dates, values, sample
counts, and cruise IDs are expected. The useful comparison is their structure,
metadata ownership, and the rules used to produce each field.

## The short version

The CNV contains measurements and acquisition/processing metadata. It does not
contain enough information to publish a complete archival NetCDF. The new
workflow consequently has four explicit metadata owners:

| Owner | What it supplies | Example |
| --- | --- | --- |
| Actual CNV file | Measurements, column descriptions and source units, cast time, positions, instrument XML, processing settings | `# name 4 = t090C: Temperature [ITS-90, deg C]` |
| `cnv_mapping.json` | Reviewed scientific names and attributes for CNV columns | `t090C -> temperature`, `standard_name=sea_water_temperature` |
| Converter | Facts calculated from this cast and fixed structural declarations | `time_coverage_start`, `history`, `featureType=Profile` |
| `dataset_profile.json` → `netcdf_global_attributes` | Human/provider-owned facts shared by all files in the selected dataset | `institution`, `license`, `publisher_name` |

ERDDAP has a fifth, separate layer:
`erddap.global_add_attributes` affects generated ERDDAP XML only. It does not
write attributes into NetCDF. Publication metadata that should exist in both
NetCDF and ERDDAP belongs in `netcdf_global_attributes`; ERDDAP will read it
from the NetCDF source attributes.

## Structural differences

### Dimensions and coordinates

The reference file has dimensions `(profile=1, z=713)`. Its coordinate model
is split:

| Reference variable | Dimensions | Meaning |
| --- | --- | --- |
| `time` | `(profile)` | One cast timestamp |
| `time_elapsed` | `(profile, z)` | Elapsed time for each sample |
| `latitude`, `longitude` | `(profile)` | One position for the cast |
| science variables | `(profile, z)` | Measurements through the cast |

The confirmed WS24258 contract intentionally uses one uniform sample model:

| Generated variable | Dimensions | Source |
| --- | --- | --- |
| `time` | `(profile, z)` | Every CNV `timeS` value plus `# start_time` |
| `latitude`, `longitude` | `(profile, z)` | Every CNV scan value |
| `depth` | `(profile, z)` | Mapped vertical field, currently `depSM` |
| `station`, `cruiseID` | `(profile, z)` | Repeated filename-derived identifiers |
| science variables | `(profile, z)` | Mapped CNV columns |

This is a deliberate contract difference, not missing data. The new file does
not need a second `time_elapsed` variable because its single `time` variable
already preserves sample-level time.

### Identity

The new converter obtains identity from the actual basename, not the CNV's
embedded `FileName` field:

```text
WS24258_Stn.054b.cnv -> cruiseID=WS24258, station=54b
                     -> WS24258/WS24258_54b.nc
```

Repeated identical cruise/station identities become `-2`, `-3`, and so on.
The old reference uses a different station-stem convention
(`station_name=WS0603_WS0603_02`). The new value is exactly the output stem,
for example `station_name=WS24258_2`.

### Science variables

The reference file uses its historical publication names, such as
`sea_water_temperature` and `sea_water_temperature_2`. The new file uses the
reviewed destination names in `cnv_mapping.json`. For example:

```text
t090C -> temperature
t190C -> temperature_2
```

If the same source name occurs twice, suffixing still happens after mapping:

```text
t090C occurrence 1 -> temperature
t090C occurrence 2 -> temperature_2
```

The source column name, occurrence, and description remain in variable
attributes, so the renamed value is traceable to the CNV.

The generated file also has scalar `instrument`, `instrument1`,
`instrument2`, and later variables built from the CNV sensor XML. The reference
uses older human-readable sensor-variable names and includes a scalar
`platform` variable. The new converter does not invent a platform variable
because the WS24258 CNV's `** Ship:` line is empty and no authoritative
platform identifier is present.

### Missing values

The CNV declares `# bad_flag = -9.990e-29`. The converter treats that sentinel
as missing and writes missing numeric values as NetCDF/IEEE `NaN`. The supplied
reference often retains `-9.99e-29` in `_FillValue` or `missing_value`
attributes. This difference is intentional: the new valid ranges are computed
from finite observations and do not mistake the source sentinel for a real
minimum.

### Units and standard-name URLs

NetCDF can store attribute strings containing `/`; unit normalization is not a
NetCDF character restriction. It is used to make units clearer and more
CF/UDUNITS-compatible. For example, the reviewed mapping can write:

```text
units = "degree_Celsius"
source_units = "deg C"
```

or:

```text
units = "S m-1"
source_units = "S/m"
```

The values are not scaled or offset. The mapping merely records an equivalent
spelling. Unit review occurs in `cnv_mapping.json`; there is no separate unit
mapping file.

The old reference commonly points `standard_name_url` at MMISW URLs and claims
CF Standard Name Table v72. The new converter derives an official,
version-pinned CF URL from each reviewed `standard_name` and claims table v94,
for example:

```text
https://cfconventions.org/Data/cf-standard-names/94/build/
cf-standard-name-table.html#sea_water_temperature
```

## Global attributes derived by the converter

These fields do not require manual entry because the converter can establish
them for each output file.

| Attribute | Source/rule | WS24258 station 2 example |
| --- | --- | --- |
| `title` | Filename cruise/station plus CNV start date | `CTD data from cruise WS24258, station 2, 2024-09-14` |
| `id` | Filename cruise ID | `WS24258` |
| `station_name` | Complete output filename stem | `WS24258_2` |
| `instrument` | First Sea-Bird header line | `CTD Sea-Bird SBE 9` |
| `source` | Converter declaration | `Sea-Bird CNV processed ASCII` |
| `source_file` | Actual CNV basename | `WS24258_Stn.002.cnv` |
| `source_format` | Converter declaration | `Sea-Bird CNV` |
| `processing_level` | Converter declaration | `Geophysical units from processed CNV data` |
| `featureType`, `cdm_data_type` | Output data model | `Profile` |
| `cdm_altitude_proxy` | Output data model | `depth` |
| `cdm_profile_variables` | Variables actually written | `profile, depth, ...` |
| `time_coverage_start/end` | `# start_time` plus min/max `timeS` | `2024-09-14T10:09:56Z` to `2024-09-14T10:12:38.750000Z` |
| `time_coverage_duration` | Difference between sample times | `PT162.75S` |
| `time_coverage_resolution` | CNV `# interval` | `PT0.0416667S` |
| latitude/longitude min/max | Finite scan coordinates | `25.64148` to `25.64172`; `-80.10420` to `-80.10398` |
| vertical min/max | Finite mapped depth values | `2.560` to `6.593` m |
| coordinate units/direction | Coordinate definitions | `degrees_north`, `degrees_east`, `positive=down` |
| `geospatial_bounds_crs` | Fixed horizontal CRS used by the converter | `EPSG:4326` |
| `standard_name_vocabulary` | Pinned converter vocabulary | `CF Standard Name Table v94` |
| `history` | UTC conversion event plus source and target | `... converted WS24258_Stn.002.cnv to WS24258/WS24258_2.nc by CTD_QARTOD` |

These names are protected. Putting one of them in
`netcdf_global_attributes` produces a conversion failure instead of silently
replacing cast-derived truth. 


### Where `comment` comes from

The generated global `comment` is not a hardcoded institutional comment. It is
built from the actual Sea-Bird processing settings in each CNV. Date-only
records and Windows input paths are discarded; useful settings remain in
source order. For WS24258 station 2, the result includes:

```text
datcnv_skipover = 0;
datcnv_ox_hysteresis_correction = yes;
filter_low_pass_tc_A = 0.150;
filter_low_pass_tc_B = 0.030;
alignctd_adv = c1S/m 0.073, sbeox0V 3.500;
celltm_alpha = 0.0300, 0.0300;
celltm_tau = 7.0000, 7.0000;
loopedit_minVelocity = 0.200;
loopedit_surfaceSoak: do not remove;
derive_ox_tau_correction = yes
```

Thus two casts can correctly receive different comments if their processing
settings differ.

## Global attributes that need extra manual input

The reference NetCDF contains publication facts that do not appear in the CNV. The table below explains what each group means and gives an actual reference example. 

| Attribute(s) | What the field means | Supplied WS0603 example | Why CNV cannot provide it |
| --- | --- | --- | --- |
| `summary` | Human-readable description of the dataset/product | `Hydrographic Measurements in the Gulf of Mexico` | CNV describes one cast and has no dataset abstract |
| `institution` | Organization responsible for the observations or product | `NOAA's Atlantic Oceanographic and Meteorological Laboratory/Ocean Chemistry and Ecosystems Division` | No related imformation |
| `license` | Legal conditions for reuse and redistribution | `These data may be redistributed and used without restriction.` | No related imformation |
| `Conventions` | Standards the finished product claims to follow | `CF-1.6, ACDD-1.3, IOOS-1.2` | No related imformation |
| `ncei_template_version` | Specific NCEI archival template the product conforms to | `NCEI_NetCDF_Profile_Template_v2.0` | No related imformation |
| `platform`, `platform_name`, `platform_id`, `platform_vocabulary` | Official vessel/platform label, registered identifier, and vocabulary | `RV_FG_Walton_Smith`; ID is `unknown` in the reference | CNV all has an empty `** Ship:` line |
| `instrument_vocabulary` | Vocabulary used to classify instruments | `GCMD Science Keywords Version 9.1.5` | CNV XML names sensors but does not declare a publication vocabulary |
| `program`, `project` | Programmatic and project context under which data were collected | `Florida Keys Monitoring program`; `Marine Biodiversity Observation Network` | No related imformation |
| `sea_name`, `Country` | Named water body and responsible/origin country used for discovery | `Gulf of Mexico`; `USA` | No related imformation |
| `keywords`, `keywords_vocabulary` | Search/discovery terms and the controlled vocabulary used | GCMD ocean-pressure, salinity, temperature, optics, and oxygen terms | No related imformation |
| `naming_authority` | Organization controlling dataset identifiers | `aoml.noaa.gov` | No related imformation |
| `creator_*` | Person or organization that created or scientifically owns the product, including role/contact | `creator_name=Christopher R. Kelble`, `creator_role=Investigator`, `creator_email=chris.kelble@noaa.gov` | No related imformation |
| `contributor_*` | Additional people/organizations and their roles | Most WS0603 contributor fields contain `unknown`; the role vocabulary points to NERC G04 | No related imformation |
| `publisher_*` | Organization distributing the final product and publication contact | `Gulf of Mexico Coastal Ocean Observing System (GCOOS)`, `data@gcoos.org`, `https://gcoos.org` | No related imformation |
| `references`, `infoUrl` | Scientific/project documentation and a dataset information page | `https://ocean.floridamarine.org/FKNMS_WQPP/`; AOML SFP page | No related imformation |
| `acknowledgment` | Required credit/funding statement |  `unknown` | `unkown` |
| `date_created`, `date_issued`, `date_modified`, `date_metadata_modified` | Product and metadata lifecycle dates | `2023-03-06T14:21:59Z` | No related imformation |
| `product_version` | Version assigned to the published data product | `v1.0` | No related imformation |
| `geospatial_bounds` | Dataset/program coverage geometry, which may be broader than one cast | A Gulf-region polygon is stored | No related imformation |
| `geospatial_bounds_vertical_crs` | Vertical reference-system identifier | `EPSG:5831` | No related imformation |

`ncei_name` is different from `ncei_template_version`. `ncei_name` is a
per-variable archival/product term such as `TEMPERATURE` or
`WATER PRESSURE`; it is reviewed in `cnv_mapping.json`.
`ncei_template_version` is a global claim about the structure of the complete
file and belongs in `netcdf_global_attributes` only when that conformance is
confirmed.

| Attributes | Result |
|---|---|
| `platform`, `platform_name` | 8 files: `RV_FG_Walton_Smith`; 12 files: `RV_Weatherbird_II` |
| `platform_id` | 11 files: `unknown`; 9 files: `other` |
| Six `contributor_*` identity/contact fields | Same `unknown` versus `other` split |
| `acknowledgment` | 11 files: `unknown`; 9 files: `other` |
| Four `date_*` fields | Every sampled file had a different timestamp |

## Reference-only attributes that should not be copied automatically

Some fields appear in the supplied file but are workflow artifacts, obsolete
history, placeholders, or per-file outputs rather than reusable human facts.

| Attribute | Reference behavior | New handling |
| --- | --- | --- |
| `NCO` | Records the installed NetCDF Operators version | Omitted unless a future step actually uses NCO; software should record its own provenance |
| `history` | Includes a 2026 `ncatted` command that repaired the old `time` calendar | Newly generated from the current conversion event; old repair commands are not copied |
| `comment1` | flags: 1 = good; 3 =  mean ± 3*standard deviation; 4 = mean ± 5*standard deviation; 9 = missing | use QARTOD definition |
| `comment2` | Literal `unknown` | Omitted |
| contributor_country and contributor_role_vocabulary | Placeholder text rather than knowledge | Keep the same |
| `metadata_link` | A file-specific ERDDAP tabledap URL for WS0603 station 2 | ? |
| vertical resolution | A value calculated for the old cast | No related imformation, needto calculate for the newer cast?  |

The converter uses `source=Sea-Bird CNV processed ASCII` instead of
the old `source=Rolling Deck to Repository (R2R) Program`. In the new file,
`source` describes the data actually converted. R2R may still be an appropriate
reference or publisher fact if the provider confirms it, but it is not stated
by the WS24258 CNV.

## How `netcdf_global_attributes` works

The section is top-level in the dataset profile:

```json
{
  "data_root": "output/WS24258_CNV",
  "netcdf_global_attributes": {
    "Conventions": null,
    "summary": null,
    "institution": null,
    "license": null,
    "platform_name": null,
    "program": null,
    "project": null,
    "creator_name": null,
    "creator_email": null,
    "publisher_name": null,
    "publisher_email": null,
    "references": null,
    "infoUrl": "https://www.aoml.noaa.gov/phod/sfp/index.php"
  }
}
```

Rules:

1. Values are shared by every NetCDF produced with that profile.
2. `null` is an explicit review placeholder and is omitted from NetCDF.
3. Non-null values must be a non-empty string or finite number. Nested JSON
   objects, arrays, booleans, blank strings, `NaN`, and infinity are rejected.
4. ??CORRECT?) manual input values cannot override converter-derived fields. For example,
   configuring `history`, `title`, or `time_coverage_start` fails conversion.
5. The section accepts additional scalar attribute names even if they are not
   listed in the template, but cast-specific values do not belong here.

`infoUrl` moved from `erddap.global_add_attributes` into this section. It is therefore written once into NetCDF and inherited by ERDDAP.
`sourceUrl=(local files)` remains ERDDAP-only because it describes how ERDDAP
accesses the files, not the scientific dataset.

## What still requires for cnv derived netcdf

The profile currently requires these globals before ERDDAP XML generation:

- `title` — already derived by the converter;
- `summary` — provider must fill;
- `institution` — provider must fill;
- `publisher_name` — provider must fill;
- `publisher_email` — provider must fill;
- `license` — provider must fill.

`infoUrl` is already populated from the existing WS24258 ERDDAP configuration.
All other placeholders are optional at the code level but should be reviewed
against the intended CF/ACDD/IOOS/NCEI publication target. In particular, do
not copy the WS0603 people, dates, project, platform identifier, or version
merely because both cruises may involve Walton Smith operations.

After filling or changing human attributes, rerun conversion and QC so the QC
NetCDF copies contain the updated globals. Then generate ERDDAP XML:

```bash
PYTHONPATH=. uv run python main.py convert-cnv \
  '/Volumes/Crucial X9/CTD_QARTOD/cnv_data/WS24258' \
  --profile config/ws24258/dataset_profile.json \
  --overwrite

PYTHONPATH=. uv run python main.py qc \
  --profile config/ws24258/dataset_profile.json

PYTHONPATH=. uv run python main.py erddap-xml \
  --profile config/ws24258/dataset_profile.json
```

