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
