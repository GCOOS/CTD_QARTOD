# CTD_QARTOD

Convert processed Sea-Bird CNV files to NetCDF, run QARTOD quality checks, review
results in a browser, and generate ERDDAP XML. You can also start with existing
NetCDF files.

## Install

Run these commands from the repository root using Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Activate the environment again when opening a new terminal.

## Convert CNV files

Pass a CNV file, a cruise folder, or a folder containing several cruises:

```bash
python main.py convert-cnv /path/to/cnv/files
```

The command creates missing configuration under `config/<dataset>/` and writes
NetCDF files under `output/<dataset>_CNV/`. It preserves existing configuration
and output files. To replace converted files deliberately, add `--overwrite`.

Latitude/longitude data columns are preserved per sample. When both columns are
absent, valid NMEA header coordinates are written once per cast instead.

Review the generated `cnv_mapping.json` and `dataset_profile.json`. If conversion
reports `mapped_to: null`, resolve the definition in the shared catalog and select
its key in the mapping before rerunning. Predefined attributes live only in
`config_template/cnv_catalog.json`; the dataset mapping records observations and destinations.
Confirm the dataset metadata and QC settings before continuing.

The examples below use `config/ws24258/dataset_profile.json`. Replace it with
your dataset's profile path. For a manually prepared profile, pass
`--profile config/YOUR_DATASET/dataset_profile.json` to the conversion command.

## Run quality checks

```bash
python main.py qc --profile config/ws24258/dataset_profile.json
```

The profile controls the input folder (`data_root`) and where results go:

- `output.mode: "duplicate"` writes QC files to `output.directory`, preserving inputs.
- `output.mode: "in_place"` updates the input NetCDF files.

Check `qc_run_manifest.json` in the QC output folder for the run status and counts.

New dataset profiles copy Walton Smith-based QC defaults from `config_template/` into per-test folders directly under `config/<dataset>/`
automatically after conversion or before the first QC run. Review those local
settings for your data; later runs preserve your edits. To create another snapshot:

```bash
python main.py generate-limits --profile config/ws24258/dataset_profile.json --output config/ws24258_alternative
```

Review that snapshot, fill any missing limits, and run QC with its profile:

```bash
python main.py qc --profile config/ws24258_alternative/dataset_profile.json
```

Use that same profile for visualization and XML generation.

**Starting with NetCDF?** Skip conversion. Choose or create a dataset profile,
set `data_root` to your NetCDF folder, and review its variable mapping and QC
settings. For the existing Walton Smith configuration:

```bash
python main.py qc --profile config/walton_smith/dataset_profile.json
```

See the [configuration guide](config_template/README.md) to set up another dataset.

## View results

```bash
python main.py viz --profile config/ws24258/dataset_profile.json
```

Open [http://127.0.0.1:8050](http://127.0.0.1:8050). Select a cruise, file,
variable, and QC test to inspect the plots. The viewer reads saved QC results.
Press `Ctrl+C` in the terminal to stop it.

Flags mean: **1** pass, **2** not evaluated, **3** suspect, **4** fail, **9** missing.

## Generate ERDDAP XML

After reviewing QC results, confirm the profile's `erddap` metadata and server
paths, then run:

```bash
python main.py erddap-xml --profile config/ws24258/dataset_profile.json
```

The XML is saved to the profile's `erddap.output_xml` path. This prepares the
configuration; deploying it to an ERDDAP server is a separate step.

## Help and documentation

```bash
python main.py --help
python main.py convert-cnv --help
```

- [Automatic CNV workflow](docs/automatic-cnv-workflow.md): canonical mappings, dataset-owned limits, and output paths.
- [Configuration guide](config_template/README.md): shared defaults and independent dataset settings.
- [Technical architecture](docs/technical-architecture.md): system design and execution flow.
- [Implementation reference](docs/implementation-reference.md): modules, Python API, QC tests, and output conventions.
- [CNV conversion details](docs/cnv-conversion-process.md): conversion rules and metadata handling.
