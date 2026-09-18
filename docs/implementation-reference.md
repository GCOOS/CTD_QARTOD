# Implementation reference

Module descriptions, QC configuration details, Python API examples, and output
conventions moved from the project README. For the overall design, see the
[technical architecture](technical-architecture.md). For installation and daily
use, see the [README](../README.md). The [automatic CNV workflow](automatic-cnv-workflow.md)
describes catalog-assisted conversion and independent dataset-owned QC settings.

### Components

- `cnv_mapping.py`: Recursively inventories CNV headers and derives source-keyed mappings from stable comments and units.
- `cnv_catalog.py`, `cnv_coordinates.py`, `cnv_metadata.py`, `cnv_workflow.py`: Shared definitions, source-specific positions, metadata ownership and automatic dataset setup.
- `qc_limit_generation.py`, `qc_template_limits.py`, `qc_time.py`: Independent template-derived QC settings and correct CF-time decoding.
- `cnv_converter.py`: Derives cruise/station identity from actual filenames, writes QC-compatible NetCDF profiles atomically, and emits `conversion_report.json`.
- `cnv_sensor_config.py`: Builds null-limit `sensor_specs.json` and `variable_sensor_map.json` drafts from converted NetCDF instrument links and units.
- `main.py`: CLI entry point with subcommands `inspect-cnv`, `convert-cnv`, `generate-sensor-config`, `generate-limits`, `qc`, `erddap-xml`, and `viz`.
- `xml_generator/`: Builds a complete `EDDTableFromNcCFFiles` `datasets.xml` from NetCDF metadata and the selected dataset profile. It does not read or modify an existing XML/template.
- `qc_config.py`: Centralized configuration including:
  - `QC_FLAGS`: QARTOD quality flag definitions
  - `ALL_CATEGORIES`: All variable categories from the mapping
  - `TEST_CATEGORIES`: Maps each test to the categories it applies to
  - Strict loader helpers for profile-selected configuration
- `qc_data_loader.py`: Loads NetCDF, loads the profile-selected QC variable mapping, finds variables needing QC, and locates lon/lat fields.
- `qc_tests/`: Package of QC test modules (each `*_test` name matches the string written as `{var}_qc_{name}` in NetCDF, e.g. `location_test`, `gross_range_test`, `climatology_test`, `gap_test`, `syntax_test`, `decreasing_radiance_test`, `spike_test`, `rate_of_change_test`, `flat_line_test`) wrapping `ioos_qc` where applicable.
- `qc_writer.py`: Writes QC arrays with QARTOD-like attributes, applied configuration provenance, and appended processing history.
- `qc_runner.py`: Orchestrates QC for a file, directory, or the full dataset tree. Batch runs preflight the complete configuration and write `qc_run_manifest.json`. It also provides individual test execution with `run_single_test()`.
- `qc_result_viz.py`: Contains `QCTestResult` dataclass for storing test results and provides visualization via `plot_profile()` with a 3-panel layout.
- `station_resolver.py`: Looks up expected station coordinates from `Station_Mean_Coords.csv` using station ID from the NetCDF file.
- `instrument_resolver.py`: Extracts instrument metadata from each NetCDF, reads variable units, verifies sensor presence, and builds dynamic gross range spans using `sensor_specs.json` + `variable_sensor_map.json`.

### Developer tests

Run the full unit suite from the repository root (after `pip install -r requirements.txt`):

```bash
python -m pytest tests/ -q
```

### Dataset configuration (`config/`)

Profiles own input/output paths, metadata, CNV interpretation, and QC category
membership. New profiles without QC paths copy Walton Smith-based settings from `config_template/` once after conversion
into per-test folders directly under `config/<dataset>/` and save local paths. There is no `qc_limits` switch. Subsequent
QC runs use only those dataset-owned files and preserve edits. The configuration
examples below describe the reference files; see the
[automatic workflow](automatic-cnv-workflow.md) for per-file source mappings,
automatic initialization, and `generate-limits` for additional independent snapshots.

For a **new dataset**, start from [config_template/](../config_template/README.md):
let `convert-cnv` create the profile and adapt template defaults after conversion.
For existing NetCDF, create a dataset profile with `paths: {}` and run
`generate-limits --profile ...`. Do not run QC using template paths directly.
Review the copied metadata, station references and scientific thresholds.

```
config/
├── walton_smith/
│   ├── dataset_profile.json
│   ├── variable_mapping/
│   ├── location_test/
│   ├── gross_range_test/
│   ├── climatology_test/
│   ├── spike_test/
│   ├── rate_of_change_test/
│   └── flat_line_test/
└── <new_dataset>/
    ├── dataset_profile.json
    ├── cnv_mapping.json
    ├── qc_variable_mapping.json
    ├── variable_mapping/source_variable_mapping.json
    └── <per-test QC configuration>/
```

The Walton Smith profile uses `sample_dimension: "z"` because science variables
are shaped `(profile, z)`, with `profile` size 1 and cast order along `z`. The
`metadata.depth` field names the NetCDF variable containing depth values, not
the dimension name.

Output mode is set in the profile:

- `in_place`: write QC variables back to the source NetCDF files.
- `duplicate`: write a mirrored dataset tree under `output.directory` (default `output/`) without modifying source files.

The same profile explicitly controls `gap_test` and `syntax_test` through
`qc_test_modes`, and owns the ERDDAP output path, server path, metadata
requirements, and deliberate XML overrides through `erddap`. Every listed QC path is required; there are no hidden
CLI path overrides. There is no shared-limit mode: new settings are copied from
`config_template/`, and runtime QC uses the selected dataset files.

### QC Flags


| Flag | Meaning       |
| ---- | ------------- |
| 1    | PASS          |
| 2    | NOT_EVALUATED |
| 3    | SUSPECT       |
| 4    | FAIL          |
| 9    | MISSING       |


### QC tests

All tests are configured via `TEST_CATEGORIES` in `qc_config.py`. Each test maps to the set of variable categories it applies to.

#### Required Test


| Test                       | Description                                                     | Categories                                               |
| -------------------------- | --------------------------------------------------------------- | -------------------------------------------------------- |
| `gap_test`                 | Profile-controlled placeholder; current profiles use `not_evaluated` | All                                                   |
| `syntax_test`              | Profile-controlled placeholder; current profiles use `not_evaluated` | All                                                   |
| `location_test`            | Compares lon/lat to expected station coordinates                | All                                                      |
| `gross_range_test`         | Uses `ioos_qc.qartod.gross_range_test` with sensor-aware limits | All                                                      |
| `decreasing_radiance_test` | Checks that values decrease with increasing depth               | PAR, in_water_radiance_irradiance                        |
| `climatology_test`         | Uses `ioos_qc.qartod.climatology_test` with station-type limits | temperature, practical_salinity, oxygen_dissolved_oxygen |


#### Strongly recommended tests


| Test                  | Description                                                                     | Categories |
| --------------------- | ------------------------------------------------------------------------------- | ---------- |
| `spike_test`          | QARTOD average / neighbor-midpoint spike test                                    | Configured |
| `rate_of_change_test` | Adjacent-sample absolute-change test                                             | Configured |
| `flat_line_test`      | Count-based repeated-value check using `flat_line_config.json`                  | All        |


### Running via Python

Process a single file:

```python
from qc_runner import run_qc_for_file
run_qc_for_file("datasets/SFER_CTD/WS24139/WS24139_Stn_002.nc")
```

Process a directory (non-recursive):

```python
from qc_runner import run_qc_for_directory
run_qc_for_directory("datasets/SFER_CTD/WS24139")
```

Process all cruise directories:

```python
from qc_runner import run_qc_for_all
run_qc_for_all()  # Uses config/walton_smith/dataset_profile.json by default
```

Generate ERDDAP XML programmatically (same as `main.py erddap-xml`):

```python
from xml_generator.generate import generate_erddap_xml_for_profile

generate_erddap_xml_for_profile()  # reads profile; writes output/erddap/datasets.xml
```

### Individual Test Execution and Visualization

Run individual QC tests for debugging and analysis:

```python
from qc_runner import run_single_test, list_available_tests, list_file_variables

# List available tests (full names)
tests = list_available_tests()
# ['gap_test', 'syntax_test', 'location_test', 'gross_range_test',
#  'decreasing_radiance_test', 'climatology_test', 'spike_test',
#  'rate_of_change_test', 'flat_line_test']

# List QC-able variables in a file
vars = list_file_variables("datasets/SFER_CTD/WS24139/WS24139_Stn_002.nc")

# Run a specific test on a variable
results = run_single_test(
    "datasets/SFER_CTD/WS24139/WS24139_Stn_002.nc",
    test_name="gross_range_test",
    variable="sea_water_temperature"
)

# Visualize results
results[0].plot_profile()
```

Individual tests return `QCTestResult` objects with:

- Original data and QC flag arrays
- Flag summary counts
- Visualization method: `plot_profile()` with 3-panel layout
- Optional file saving with `save_path` parameter

### Configuration knobs

- **Dataset-owned gross ranges** (resolution order):
  1. `gross_range_overrides` (call-time)
  2. Dynamic (instrument + unit from `instrument_resolver.py` using `config/<dataset>/gross_range_test/sensor_specs.json` + `variable_sensor_map.json`)
- **Variable-to-sensor mapping**: edit `config/<dataset>/gross_range_test/variable_sensor_map.json`
- **Sensor identifiers and limits**: edit `config/<dataset>/gross_range_test/sensor_specs.json` (`identifiers.long_names` is used for sensor matching)
- **Location tolerance**: edit `config/<dataset>/location_test/location_config.json`
- **Test-to-category mapping**: edit `TEST_CATEGORIES` in `qc_config.py`
- **Climatology limits**: edit `config/<dataset>/climatology_test/station_climatology_config.json`. **Station deep vs shallow**: edit `config/<dataset>/climatology_test/station_depth_classification.json`. Programmatic runners can still pass `climatology_overrides`.
- **Gap/syntax mode and ERDDAP generation**: edit `qc_test_modes` and `erddap` in the selected dataset profile. Selecting `run` for a placeholder currently fails preflight until a real implementation is added, preventing false evaluation.

### Station resolution logic

- Station ID is read from the `station` variable inside each NetCDF file.
- Expected coordinates come from `config/<dataset>/location_test/Station_Mean_Coords.csv`.
- If no station mapping is found, the location test returns NOT_EVALUATED.

### Climatology resolution logic

- Station ID (same as above) is looked up in `config/<dataset>/climatology_test/station_depth_classification.json`: lists `deep_cast` and `shallow_cast` station IDs (case-insensitive match; trailing `.0` is stripped).
- **Deep** is checked before **shallow** if a station were ever listed twice (should not happen).
- The matching list selects either `deep_cast_limits` or `shallow_cast_limits` from `config/<dataset>/climatology_test/station_climatology_config.json` (variable name → list of rule dicts for `ioos_qc`).
- If the station appears in neither list, or the chosen limits block is missing or empty, climatology is not applied (flags stay NOT_EVALUATED for that test).
- `shallow_stable` is no longer used; only deep vs shallow cast types are supported.

### Output naming

Pipeline-generated QC variables use CF/QARTOD-style names and attributes. Each mapped data variable receives an aggregate flag plus per-test flags, and its `ancillary_variables` attribute is overwritten to point to those pipeline-generated flags. Legacy source variables such as `{variable}_qc` are left in the file unchanged, but processed science variables no longer point to them via `ancillary_variables`.

All new QC variables include `flag_values = 1, 2, 3, 4, 9`, `flag_meanings = "PASS NOT_EVALUATED SUSPECT FAIL MISSING"`, `units = "1"`, a test-specific `standard_name`, and attributes naming the test module, target, applied JSON configuration, and configuration source. QC appends global `history`; the batch manifest records configuration hashes and runtime provenance.

### QC variable names

Each data variable that appears in the variable mapping and in the file gets QC tests based on its category.


| QC output                | QC variable suffix             | `standard_name`                         | Applied to                                               |
| ------------------------ | ------------------------------ | --------------------------------------- | -------------------------------------------------------- |
| Aggregate flag           | `_qc_agg`                      | `aggregate_quality_flag`                | All mapped variables with pipeline QC                    |
| Gap test                 | `_qc_gap`                      | `gap_test_quality_flag`                 | All mapped variables                                     |
| Syntax test              | `_qc_syntax`                   | `syntax_test_quality_flag`              | All mapped variables                                     |
| Location test            | `_qc_location`                 | `location_test_quality_flag`            | All mapped variables                                     |
| Gross range test         | `_qc_gross_range`              | `gross_range_test_quality_flag`         | All mapped variables                                     |
| Decreasing radiance test | `_qc_decreasing_radiance_test` | `decreasing_radiance_test_quality_flag` | PAR, in_water_radiance_irradiance                        |
| Climatology test         | `_qc_climatology`              | `climatology_test_quality_flag`         | temperature, practical_salinity, oxygen_dissolved_oxygen |
| Flat line test           | `_qc_flat_line`                | `flat_line_test_quality_flag`           | All mapped variables                                     |
| Spike test               | `_qc_spike`                    | `spike_test_quality_flag`               | Variables listed in `spike_thresholds.json`              |
| Rate of change test      | `_qc_rate_of_change`           | `rate_of_change_test_quality_flag`      | Variables listed in `rate_of_change_thresholds.json`     |


### Mapped data variables

Default categories from `config_template/qc_variable_mapping.json` (copied/adapted for new datasets):


| Category                        | Variables                                                                  |
| ------------------------------- | -------------------------------------------------------------------------- |
| temperature                     | `sea_water_temperature`, `sea_water_temperature_2`                         |
| practical_salinity              | `sea_water_salinity`, `sea_water_salinity_2`                               |
| conductivity                    | `sea_water_electrical_conductivity`, `sea_water_electrical_conductivity_2` |
| pressure                        | `sea_water_pressure`                                                       |
| oxygen_dissolved_oxygen         | `dissolved_oxygen`                                                         |
| oxygen_saturation               | `oxygen_saturation`, `oxygen_saturation_2`                                 |
| PAR                             | `photosynthetically_available_radiation`                                   |
| above_water_radiance_irradiance | `surface_photosynthetically_available_radiation`                           |
| beam_attenuation                | `beam_attenuation`                                                         |
| turbidity                       | `sea_water_turbidity`                                                      |
| chlorophyll                     | `chlorophyll_concentration`, `chlorophyll_fluorescence`                    |
| CDOM                            | `CDOM`                                                                     |
