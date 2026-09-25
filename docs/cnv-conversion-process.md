# Current CNV conversion process

This guide describes the schema-5 workflow. For commands and full examples, see
[automatic CNV workflow](automatic-cnv-workflow.md) and
[shared defaults](../config_template/README.md).

## 1. Input and inspection

`python main.py convert-cnv /path/to/cnv` accepts a single CNV, a cruise folder,
or a tree containing multiple cruises. The actual filename supplies cruise and
station, not the embedded `FileName`. Numeric leading zeros are removed and
source station aliases are applied: `054b` becomes `54-2`, and `057-2` becomes
`57_2`. The aliases belong to filename conversion, not QC.
A final underscore plus a running number of any length is discarded for any
cruise; `HG26193_Stn.TB1_003.cnv` is station `TB1`, and
`WS20278_Stn.9_5.cnv` is station `9`. Dots within station numbers become
underscores, so `HG26193_Stn.009.5_080.cnv` is station `9_5`.
Supported vessel prefixes include WS, WB, SV/SAV and HG; `HG12_Stv.057-2_069.cnv`
is also accepted and becomes station `57_2`. Unresolved identities
fail instead of being guessed. A processing tree prefers a sibling `06-drv`;
other ambiguous processing stages require an explicit input selection.

`inspect-cnv` can inventory the complete input first. It creates a source-keyed
`cnv_mapping.json` with observed descriptions, units, occurrence/file counts,
sensor inventory, coordinate sources and errors. Each scientific entry has
`observed` and `mapped_to`, a key in `config_template/cnv_catalog.json`.
Definitions are not duplicated into the dataset mapping. Unknown/conflicting
sources have a null target and block conversion. Intentional exclusion uses
`ignore: true` and `mapped_to: null`.

## 2. Shared definitions and dataset ownership

`config_template/` contains the single profile template, CNV catalog, QC category
mapping, and populated Walton Smith-based per-test defaults. Conversion without
an explicit profile creates `config/<dataset>/dataset_profile.json` and the
observed mapping. Existing profiles and mappings are preserved.

The catalog owns canonical measurement names, long/standard names, IOOS and NCEI
metadata where defined, accepted units, spelling normalization and sensor tags.
Conversion resolves those definitions at runtime. Standard-name URLs are derived
from the pinned CF table. Actual per-file descriptions and units are validated.
Missing source units remain absent; numerical values are not rescaled.

Different source names or repeated columns may map to the same measurement.
Suffixes are added afterward: `sea_water_temperature`,
`sea_water_temperature_2`, etc. Chlorophyll fluorescence and concentration remain
different measurements even though both belong to QC category `chlorophyll`.

## 3. NetCDF construction

Every cast has one `profile` and a `z` sample dimension. Output paths are
`<data_root>/<cruise>/<cruise>_<station>[-<repeat-number>].nc`.
Repeated cruise/station identities use start time and source-path ordering;
`conversion_index.json` retains naming across incremental runs.

| Field | Source | Dimensions |
| --- | --- | --- |
| time | Original `timeS`, with seconds-since origin from CNV `start_time` | `(profile, z)` |
| depth | Selected vertical column, normally `depSM` | `(profile, z)` |
| latitude/longitude | Complete CNV coordinate-column pair | `(profile, z)` |
| latitude/longitude when both columns are absent | Valid NMEA headers | `(profile)` |
| science | Original mapped columns | `(profile, z)` |

Header degrees/minutes/hemispheres are converted to signed decimal degrees.
A partial or invalid coordinate-column pair is rejected, not replaced by headers.
The elapsed time's nonzero starting value is preserved. QC decodes CF time using
its actual origin/calendar rather than treating elapsed numbers as Unix time.

The converter checks declared rows/columns, required fields, finite coordinates,
interval and monotonic elapsed time. The source bad flag becomes NaN. Instrument
variables preserve parsed sensor type/channel, serial and calibration date;
science variables keep source name, occurrence, description and changed unit
spelling. Not every calibration coefficient becomes an output attribute.

## 4. Global metadata

All three sections are in each dataset profile:

- `netcdf_global_attributes`: editable creator/contributor, acknowledgment,
  product version and polygon defaults, copied from the approved WS24258 profile.
  These values are configured, not inferred from the CNV; null omits a field.
- `netcdf_fixed_global_attributes`: configured publisher, institution,
  conventions and other SFER defaults.
- `netcdf_derived_global_attributes`: identifies computed fields such as identity,
  platform, coverage, timestamps and processing history. Title/summary values
  are format templates; other entries describe derivations, not expressions.

Ownership conflicts are errors. Source processing comments are derived from CNV.
No publication date is invented. Review copied creator and geographic defaults
before publishing a different dataset.

## 5. QC and XML

After conversion, new profiles without QC paths get independent settings copied
and adapted from `config_template/` to their actual NetCDF names and units.
Settings live directly under `config/<dataset>/`: no nested QC folder,
`qc_limits` selector or per-cast override file. Existing local settings are not
regenerated. Missing thresholds or station references yield NOT_EVALUATED;
successful execution is not the same as every sample passing QC.
For both location and climatology lookups, QC treats `_` in a stored station ID
as `.` in the reference files without changing the stored ID.

```bash
python main.py qc --profile config/sav1803/dataset_profile.json
python main.py erddap-xml --profile config/sav1803/dataset_profile.json
```

QC preserves scientific arrays and adds flags/provenance. The XML generator reads
NetCDF globals and variables, applies explicit ERDDAP overrides, and creates a
complete XML file without reading an existing XML template. Deployment is separate.

## 6. Output safety and verification

The converter writes a temporary NetCDF, reopens it for validation, then installs
it atomically. Existing outputs require `--overwrite`; failures remain in
`conversion_report.json` and cause a nonzero CLI exit. QC records terminal status
and counts in `qc_run_manifest.json`. XML has one dataset entry per discovered
NetCDF file; generation alone does not verify server acceptance.

SAV1803 has been rerun through all stages: 31 inputs, 31 converted files, 31 QC
files and 31 XML entries. PAR's missing units and seven missing station-reference
identifiers remain documented limitations. See the automatic workflow for details.
