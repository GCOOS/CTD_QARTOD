# Generated versus supplied NetCDF

This is the current schema-5 comparison. The supplied files are evidence and a
source of approved defaults, not files that the converter edits or clones.

## Main differences

| Area | Supplied NetCDF examples | Current CNV-generated NetCDF |
| --- | --- | --- |
| Scientific data | May include earlier filtering, binning or soak removal | Preserves the selected processed CNV samples; no new scientific rescaling/filtering |
| Names | Existing archive names | Catalog measurement names; duplicates receive `_2`, `_3` after mapping |
| Time | Some files use one profile-level timestamp | Original elapsed `timeS` at `(profile, z)` with CNV start-time origin |
| Coordinates | Checked WS/SV examples use `(profile)` | NMEA headers use `(profile)`; measured columns retain `(profile, z)` |
| Units | Archive labels | Equivalent spelling normalization only; original spelling retained when changed |
| Missing metadata | May be present from a prior publication workflow | Comes from the selected profile, CNV or derivation; it is not recovered from another NetCDF at runtime |
| QC flags | May contain legacy flags | New per-test and aggregate flags from the dataset-owned configuration |
| XML | Historically synchronized with an existing XML file | Built directly from generated/QC NetCDF plus ERDDAP profile settings |

Equal filenames or station identifiers do not prove equal processing histories.
For example, the checked supplied SV station-12 file had 3,019 samples while the
SAV1803 CNV contains 4,471; matching cast coordinates do not establish sample equality.

## Where information comes from

`config_template/cnv_catalog.json` defines scientific names, known standard
attributes, sensor associations and accepted units. Dataset `cnv_mapping.json`
records observed headers and catalog keys, not editable copies of those attributes.
For example, `t090C` maps to `sea_water_temperature` with long name
`Sea Water Temperature`, IOOS category `Temperature`, and NCEI label
`WATER TEMPERATURE`. The standard-name URL is generated from the CF name.

A source unit `deg C` is written as `degree_Celsius`, with `source_units = "deg C"`.
NetCDF can store slashes in attribute text; normalization is for consistent unit
vocabulary, not because `/` is forbidden. No scale/offset conversion occurs.
Missing units, such as SAV1803 PAR, remain missing rather than being filled from
a catalog guess. Incompatible units are rejected or left without applicable QC
limits, as appropriate to the stage.

The source CNV supplies measurements, elapsed time, cast start time, depth,
coordinates, instrument descriptions and processing settings. It generally does
not supply publication contacts, institutional ownership, publisher, license,
project, product version or program-wide geographic polygon.

## Global metadata ownership

The dataset profile has three separate sections:

- **Human:** `netcdf_global_attributes`, populated from approved WS24258 defaults.
  Examples absent from CNV include creator `Christopher R. Kelble`, contact email
  `chris.kelble@noaa.gov`, product version `v1.0`, and the configured geographic
  polygon. Some approved contributor fields contain the literal `unknown`.
  These are editable defaults, not facts inferred for every cruise. Null omits
  an inapplicable value.
- **Fixed:** `netcdf_fixed_global_attributes`, including NOAA institution, GCOOS
  publisher, license, conventions, program and project. These are visible in JSON
  and used during conversion; they are not recovered from the original file.
- **Derived:** `netcdf_derived_global_attributes`, describing IDs, platform,
  temporal/spatial coverage, title, summary, processing comments and timestamps.
  Title/summary are templates. Other descriptions are not copied as literal
  output values. `date_issued` is omitted until an actual publication event.

The implementation also has built-in structural defaults for low-level conversion;
the standard CLI supplies the template-derived profile. Distinct sections cannot
claim the same attribute, and human fields cannot override generated identity or
coverage. Review SFER-specific defaults before applying them to another program.

## QC and publication

The reusable per-test files in `config_template/` contain Walton Smith values.
New datasets get independent local copies adapted to actual names/units; existing
profiles use their saved paths. Editing a local threshold affects subsequent QC
runs for that dataset, not other datasets or the shared defaults. Rate-of-change
thresholds remain per adjacent sample, not per second.

Climatology decodes actual CF time origins/calendars before testing; raw elapsed
numbers are not misinterpreted as dates near January 1970. Original time values
and encoding attributes remain in the written NetCDF.

XML generation obtains variable/global metadata from the resulting NetCDF and
applies `erddap.global_add_attributes` from the selected profile. It does not
modify a supplied XML file. Changing profile metadata requires reconversion and
QC before XML reflects those new NetCDF attributes; XML-only overrides are separate.

## Verified SAV1803 rerun

All 31 casts converted and received QC; XML contains 31 entries. Compared with
the prior outputs, 1,147 original variable arrays and 3,410 QC flag arrays were
unchanged. All 23 configured human globals were present in every rebuilt file.
PAR gross range remains NOT_EVALUATED without source units; station references
are absent for `21`, `21-5`, `57-1`, `57-2`, `57-3`, `9_5`, and `MRv2`.
These checks establish preservation for this rerun, not scientific certification
or equivalence to every supplied historical NetCDF.

See [current conversion details](cnv-conversion-process.md),
[automatic workflow](automatic-cnv-workflow.md), and
[template guide](../config_template/README.md).
