# CTD_QARTOD

Pipeline contains:

1. Convert CNV files
2. Review/Remove surface soak
3. run QC
4. Inspect the results(interactive dash view)
5. generate ERDDAP XML

This walkthrough uses **WS24258**. Run commands from the repository root and
replace the source folder and dataset name for your own data.

## 1. Set up Python

Use Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

In later terminal sessions, activate the existing environment with
`source .venv/bin/activate`.

## 2. Convert the CNV files

To review metadata and variable mapping before conversion, first run:

```bash
python main.py convert-cnv cnv_data/WS24258 --prepare-profile
```

This creates missing `config/WS24258/dataset_profile.json` and `cnv_mapping.json`,
preserves existing configuration, and exits without writing NetCDF files or
initializing dataset-owned QC settings. Edit the profile and review the mapping,
then convert:

```bash
python main.py convert-cnv cnv_data/WS24258
```

This creates configuration in `config/WS24258/` and NetCDF files in
`output/WS24258_CNV/`.

Check `output/WS24258_CNV/conversion_report.json` for failures. Review the
variable mapping, dataset metadata, and QC settings in `config/WS24258/` before
continuing. If conversion reports unresolved mappings, correct them using the
[configuration guide](config_template/README.md), then rerun conversion.
Add `--overwrite` to replace existing converted files after changing metadata.

## 3. Remove and review surface soak (optional)

Preview the cuts without writing files:

```bash
python main.py remove-soak --profile config/WS24258/dataset_profile.json --dry-run
```

Write the trimmed files:

```bash
python main.py remove-soak --profile config/WS24258/dataset_profile.json
```

The default output is `output/WS24258_SOAK_REMOVED/`. Open the review dashboard:

```bash
python main.py review-soak --profile config/WS24258/dataset_profile.json
```

Open [http://127.0.0.1:8050](http://127.0.0.1:8050). Select a cast, inspect the
retained range, and use **Accept & Save** to save adjustments. Choose **After
soak removal** to view saved trimmed files. Press **Ctrl+C** to stop the server.

Both soak commands accept `--input-root /path/to/netcdf` and
`--output-root /path/to/trimmed` when you need different folders. The review
input can be a dataset root or a cruise folder containing NetCDF files directly.

## 4. Run QC

### Inspect the test limits first

The first conversion copies QC limits from `config_template/` into the
dataset's own folder, `config/WS24258/`. Review them before running QC:

| Folder in `config/WS24258/` | Contents |
| --- | --- |
| `gross_range_test/` | Valid min/max per variable (`sensor_specs.json`) |
| `spike_test/` | Spike thresholds (`spike_thresholds.json`) |
| `rate_of_change_test/` | Rate-of-change thresholds (`rate_of_change_thresholds.json`) |
| `flat_line_test/` | Flat-line settings (`flat_line_config.json`) |
| `climatology_test/` | Station climatology and depth classes |
| `location_test/` | Station coordinates and location settings |

`config/WS24258/limit_report.json` lists variables that got no gross-range
limit because the template has none for their units. Edits made here are kept:
later conversions never overwrite existing QC settings.

**The rate-of-change test depends on the sample rate.** Its threshold is the
largest allowed change between two adjacent samples, not a change per second.
The values in `config_template/` are for **24 Hz** data. For data sampled more
slowly, there is more time between samples, so scale the thresholds up
linearly:

```text
threshold_new = threshold_24Hz × 24 / sample_rate_Hz
```

For example, 4 Hz data needs thresholds 6 times larger: temperature `0.4` →
`2.4`, salinity `0.053` → `0.318`. For bin-averaged data, use the bin rate
(for example, 1-m bins at a descent rate of 1 m/s is 1 Hz).

### Run QC

Choose one input. Both commands use the same profile and QC settings.

**Original converted data:**

```bash
python main.py qc --profile config/WS24258/dataset_profile.json
```

**Soak-removed data:**

```bash
python main.py qc --profile config/WS24258/dataset_profile.json --input-root output/WS24258_SOAK_REMOVED
```

We provide flag `--input-root`, which select the dataset root containing cruise folders of your choice.
With the generated profile, results go to `output/WS24258_QC/`. Running both
commands replaces matching QC outputs. Check `output/WS24258_QC/qc_run_manifest.json`
for the input used, completion status, and counts.

## 5. Inspect QC results

```bash
python main.py viz --profile config/WS24258/dataset_profile.json
```

Open [http://127.0.0.1:8050](http://127.0.0.1:8050). Select a cruise, file,
variable, and QC test to inspect the results. Press **Ctrl+C** when finished.

## 6. Generate ERDDAP XML

Confirm the profile's ERDDAP metadata and server paths, then run:

```bash
python main.py erddap-xml --profile config/WS24258/dataset_profile.json
```

The generated profile saves the XML to `output/erddap/ws24258_datasets.xml`.
Deploying the NetCDF files and XML to an ERDDAP server is a separate step.

## Help

```bash
python main.py --help
python main.py qc --help
```

For existing NetCDF data or custom settings, see the
[configuration guide](config_template/README.md).
