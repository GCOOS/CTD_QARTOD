# Hogarth CNV configuration

This directory configures conversion and downstream QC for the NOAA AOML
Hogarth CNV dataset. The workflow boundary is:

```text
Sea-Bird CNV + this dataset profile
    -> QC-compatible source NetCDF
    -> existing QARTOD pipeline
```

The converter interprets the CNV structure required by NetCDF and does not own
QARTOD policy or publication metadata. Mapped science values and their CNV
units are preserved unchanged.

## Configuration files

| File | Responsibility |
| --- | --- |
| `dataset_profile.json` | Selects this dataset's mappings, QC files, source NetCDF root, QC output root, and QC-facing metadata names |
| `cnv_mapping.json` | Defines the cruise/station filename pattern, structural fields and transforms, and CNV-to-NetCDF science names; science units come from each CNV column description |
| `qc_variable_mapping.json` | Groups resulting NetCDF variables into the existing QC categories |
| `gross_range_test/` | Records sensor identities and source-unit range templates |
| `spike_test/` | Records per-variable spike-threshold templates and units |
| `rate_of_change_test/` | Records per-variable adjacent-sample threshold templates and units |
| `location_test/` | Records location tolerance; the profile explicitly selects the shared SFER station-coordinate table |
| `climatology_test/` | Records station classification and climatology limits when available |
| `flat_line_test/` | Records the currently active count-based flat-line settings |

There is no `cnv_conversion.json`. Publication metadata that is not present in
CNV is intentionally deferred to a later publication boundary.

## Current conversion behavior

- All 22 fields observed in cruise `HG26193` are explicitly accounted for.
- Science arrays, including all three oxygen fields, retain their source values
  and units. No measurement-unit conversion is performed.
- `depSM` becomes `depth(profile, z)`.
- Every `timeQ` sample receives the configured `946684800`-second offset and
  becomes Gregorian epoch time in `time(profile, z)`.
- Per-scan latitude and longitude are retained; their medians become profile
  coordinates.
- Embedded sensor type, channel, serial number, calibration date, and
  calibration coefficients become scalar NetCDF instrument variables.
- Conversion creates no `*_qc_*` variables.

## Run the workflow

Run commands from the repository root.

### 1. Convert CNV to source NetCDF

```bash
PYTHONPATH=. uv run python main.py convert-cnv \
  --profile config/hogarth_cnv/dataset_profile.json \
  --input-dir cnv_data/2026_07_Hogarth_NOAA_CTD \
  --output-dir output/SFER_CNV
```

The converter creates one cruise directory under `output/SFER_CNV` and writes
`output/SFER_CNV/conversion_report.json`. Review that report before QC. Existing
NetCDF files are skipped unless `--overwrite` is supplied; use that option only
when intentionally replacing a previous conversion.

### 2. Run the existing QC pipeline

```bash
PYTHONPATH=. uv run python main.py qc \
  --profile config/hogarth_cnv/dataset_profile.json
```

The profile uses duplicate-output mode, so source NetCDF remains under
`output/SFER_CNV` and QC NetCDF is written under `output/SFER_QC`.

### 3. Inspect or publish QC output

```bash
PYTHONPATH=. uv run python main.py viz \
  --profile config/hogarth_cnv/dataset_profile.json

PYTHONPATH=. uv run python main.py erddap-xml \
  --profile config/hogarth_cnv/dataset_profile.json
```

## QC configuration status

Gross-range, spike, rate-of-change, and location files are templates. They
retain variable names and source units, but all unverified limits are `null`.
The climatology files are also intentionally empty. The pipeline therefore
emits `NOT_EVALUATED` for these tests instead of inheriting Walton limits.

The shared SFER station-coordinate table is explicitly selected in
`dataset_profile.json`, but location remains disabled for every station while
its tolerance is `null`. Flat-line settings remain populated and active.
`qc_test_modes` explicitly keeps gap and syntax at `not_evaluated`; selecting
`run` fails preflight until those tests have real implementations. ERDDAP output,
server path, required globals, and XML additions also live in the profile. QC and ERDDAP remain two
explicit commands, and QC writes `output/SFER_QC/qc_run_manifest.json`.

Add limits only after scientific review. Enter them in the units recorded by
the corresponding template; no converter change is required. Regenerate QC
NetCDF after changing any QC configuration.

## Further documentation

- [CNV conversion process](../../docs/cnv-conversion-process.md) follows the
  implementation step by step, including every configuration section, each CNV
  section, NetCDF construction, the conversion report, and the QC handoff.
- [NOAA AOML Sea-Bird CNV data findings](../../docs/cnv-data-findings.md)
  documents the supplied cruise schema, sensor inventory, available and missing
  metadata, variable mapping, processing evidence, and known limitations.

For a different CNV dataset, start from `config_template/` and provide a
complete dataset-owned `cnv_mapping.json`. Do not assume the Hogarth filename,
field schema, channel layout, or sensor inventory applies to another cruise.
