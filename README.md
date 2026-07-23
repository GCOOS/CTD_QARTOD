## NetCDF QC Pipeline

### Components

- `main.py`: CLI entry point with subcommands `qc` (NetCDF QC) and `erddap-xml` (sync ERDDAP `datasets.xml` blocks).
- `erddap_xml_sync.py`: Updates `EDDTableFromNcCFFiles` dataset blocks so `fileDir` / `fileNameRegex` and `dataVariable` lists match NetCDF contents (including `*_qc_*` variables). By default creates missing XML dataset blocks from `datasets/GenerateDatasetsXml.xml` and removes blocks with no matching `.nc` under `--data-root`. Can be run standalone or via `main.py erddap-xml`.
- `qc_config.py`: Centralized configuration including:
  - `QC_FLAGS`: QARTOD quality flag definitions
  - `ALL_CATEGORIES`: All variable categories from the mapping
  - `TEST_CATEGORIES`: Maps each test to the categories it applies to
  - Fallback config paths and small loader helpers used when a profile omits a path
- `qc_data_loader.py`: Loads NetCDF, loads `walton_mapping.json`, finds variables needing QC, and locates lon/lat fields.
- `qc_tests/`: Package of QC test modules (each `*_test` name matches the string written as `{var}_qc_{name}` in NetCDF, e.g. `location_test`, `gross_range_test`, `climatology_test`, `gap_test`, `syntax_test`, `decreasing_radiance_test`, `spike_test`, `rate_of_change_test`, `flat_line_test`) wrapping `ioos_qc` where applicable.
- `qc_writer.py`: Writes QC arrays back to the dataset with standard QARTOD-like attributes.
- `qc_runner.py`: Orchestrates QC for a file, directory, or the full dataset tree. Also provides individual test execution with `run_single_test()`.
- `qc_result_viz.py`: Contains `QCTestResult` dataclass for storing test results and provides visualization via `plot_profile()` with a 3-panel layout.
- `station_resolver.py`: Looks up expected station coordinates from `Station_Mean_Coords.csv` using station ID from the NetCDF file.
- `instrument_resolver.py`: Extracts instrument metadata from each NetCDF, reads variable units, verifies sensor presence, and builds dynamic gross range spans using `sensor_specs.json` + `variable_sensor_map.json`.

### Tests

Run the full unit suite from the repository root (after `pip install -r requirements.txt`):

```bash
python -m pytest tests/ -q
```

### Config files (`config/`)

Config is grouped by QC test (or `variable_mapping` for the Walton category → dataset-variable map). Dataset-level layout and config file paths are defined in `config/dataset_profile.json`; per-test folders own test-specific thresholds/settings.

For a **new dataset**, start from `[config_template/](config_template/README.md)`: copy the folder to `config/`, then edit each file (see the template README for a checklist).

```
config/
├── dataset_profile.json              # data_root, output mode, metadata names, sample_dimension
├── variable_mapping/
│   └── walton_mapping.json          # Walton categories → dataset variable names
├── location_test/
│   ├── Station_Mean_Coords.csv      # Expected lat/lon per station
│   └── location_config.json         # Location-test tolerance
├── gross_range_test/
│   ├── sensor_specs.json            # Sensor IDs + unit-aware ranges
│   └── variable_sensor_map.json     # Variable → sensor mapping
├── climatology_test/
│   ├── station_climatology_config.json   # deep_cast_limits / shallow_cast_limits
│   └── station_depth_classification.json # Station lists: deep_cast vs shallow_cast
├── spike_test/
│   └── spike_thresholds.json
├── rate_of_change_test/
│   └── rate_of_change_thresholds.json
└── flat_line_test/
    └── flat_line_config.json
```

The SFER profile uses `sample_dimension: "z"` because science variables are shaped `(profile, z)`, with `profile` size 1 and cast order along `z`. The `metadata.depth` field names the NetCDF variable containing depth values, not the dimension name.

Output mode is set in the profile:

- `in_place`: write QC variables back to the source NetCDF files.
- `duplicate`: write a mirrored dataset tree under `output.directory` (default `output/`) without modifying source files.

### QC Flags


| Flag | Meaning       |
| ---- | ------------- |
| 1    | PASS          |
| 2    | NOT_EVALUATED |
| 3    | SUSPECT       |
| 4    | FAIL          |
| 9    | MISSING       |


### Tests

All tests are configured via `TEST_CATEGORIES` in `qc_config.py`. Each test maps to the set of variable categories it applies to.

#### Required Test


| Test                       | Description                                                     | Categories                                               |
| -------------------------- | --------------------------------------------------------------- | -------------------------------------------------------- |
| `gap_test`                 | Placeholder (all NOT_EVALUATED)                                 | All                                                      |
| `syntax_test`              | Placeholder (all NOT_EVALUATED)                                 | All                                                      |
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


### Running via CLI

The top-level command requires a **subcommand**: `qc`, `erddap-xml`, or `viz`.

```bash
python main.py --help
python main.py qc --help
python main.py erddap-xml --help
```

#### `qc` — run QC on NetCDF trees

```
--profile                   Dataset profile JSON (default: config/dataset_profile.json)
--base-dir                  Base directory containing cruise directories
--verbose, -v               Debug logging
--log-file                  Optional log file path
--sync-erddap-xml / --no-sync-erddap-xml
                            After QC, write synced XML to output/erddap/ (default: on)
--erddap-input-xml          Input ERDDAP XML for post-QC sync (optional override)
--erddap-output-xml         Output ERDDAP XML path (optional override)
--erddap-filedir-prefix     fileDir prefix for synced blocks (default: /data/erddap/<dataset_name>)
```

Run with defaults:

```bash
python main.py qc
```

Run with a different input tree while keeping the selected profile/config files:

```bash
python main.py qc --base-dir /path/to/datasets
```

#### `erddap-xml` — sync ERDDAP `datasets.xml` from NetCDF files

After QC adds `*_qc_*` variables, this step refreshes each matching `<dataset>` block’s `<dataVariable>` list and can point `fileDir` at your ERDDAP server layout via `--filedir-prefix`.

```
--input-xml                 Input ERDDAP datasets XML (default: datasets/mod_CTD_datasets.xml)
--output-xml                Output path when not using --in-place (default: output/erddap/mod_CTD_datasets_qc.xml)
--data-root                 Root with cruise subdirs containing *.nc (default: profile output.directory when output.mode is duplicate, otherwise profile data_root)
--profile                   Dataset profile JSON (default: config/dataset_profile.json)
--no-preserve-erddap-ui     Do not keep ERDDAP color-bar attributes from the input XML
--filedir-prefix            Value written into each <fileDir> prefix (default: /data/erddap/<dataset_name> from --data-root)
--dataset-template-xml      Template <dataset> XML for missing datasets (default: datasets/GenerateDatasetsXml.xml)
--dataset-type              ERDDAP dataset type attribute to match (default: EDDTableFromNcCFFiles)
--no-create-missing-datasets
                            Do not append new <dataset> blocks for .nc files not already in the XML
--keep-orphan-datasets      Keep XML blocks whose cruise/filename is missing from --data-root (default: remove)
--in-place                  Overwrite --input-xml instead of writing --output-xml
--verbose, -v               Debug logging
```

Examples:

```bash
python main.py erddap-xml
python main.py erddap-xml --output-xml output/erddap/mod_CTD_datasets_qc.xml
python main.py erddap-xml --data-root output/SFER_QC
python main.py erddap-xml --data-root datasets/SFER_CTD_SOAK_REMOVED_NO_LEGACY_QC
```

Standalone (same behavior as `main.py erddap-xml`):

```bash
python erddap_xml_sync.py --help
```

**Note:** Matching uses the cruise folder name (last segment of the existing `<fileDir>` in the XML) plus `<fileNameRegex>` as the NetCDF filename. Your `--data-root` tree should follow `data-root/<cruise>/<file>.nc`. For each variable, `<addAttributes>` are rebuilt from NetCDF (including `standard_name`, `ancillary_variables`, and QC flag metadata). Missing NetCDF-backed datasets are created by default from `datasets/GenerateDatasetsXml.xml`. Non-dataset ERDDAP XML content from `datasets/mod_CTD_datasets.xml` is preserved, and the original file is not modified unless you pass `--in-place`. Requires `lxml` (see `requirements.txt`).

After `python main.py qc`, ERDDAP XML sync runs by default (`--sync-erddap-xml`) using the QC output tree when the profile uses duplicate output mode. Skip with `--no-sync-erddap-xml`.

XML paths default from the ERDDAP sync module and can be overridden on the command line.

#### `viz` — inspect saved QC results in Dash

Launch a local read-only dashboard over the QC NetCDF output tree:

```bash
python main.py viz
```

By default, `viz` reads `profile.output.directory` when the profile uses duplicate output mode, otherwise it reads `profile.data_root`. Override the tree or server address as needed:

```bash
python main.py viz --data-root output/SFER_QC
python main.py viz --host 0.0.0.0 --port 8051
```

The dashboard lets you select cruise, file, variable, and QC test. It plots sample index vs depth above sample index vs variable value, preserving the original sample order so casts with irregular depth movement are easy to inspect. QC flags are colored by QARTOD value and can be filtered from the checklist.

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
run_qc_for_all()  # Uses config/dataset_profile.json by default
```

Sync ERDDAP XML programmatically (same as `main.py erddap-xml`):

```python
from erddap_xml_sync import run_erddap_xml_sync_for_profile

run_erddap_xml_sync_for_profile()  # reads profile; writes output/erddap/mod_CTD_datasets_qc.xml
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

- **Gross ranges** (resolution order):
  1. `gross_range_overrides` (call-time)
  2. Dynamic (instrument + unit from `instrument_resolver.py` using `config/gross_range_test/sensor_specs.json` + `variable_sensor_map.json`)
- **Variable-to-sensor mapping**: edit `config/gross_range_test/variable_sensor_map.json`
- **Sensor identifiers and limits**: edit `config/gross_range_test/sensor_specs.json` (`identifiers.long_names` is used for sensor matching)
- **Location tolerance**: edit `config/location_test/location_config.json`
- **Test-to-category mapping**: edit `TEST_CATEGORIES` in `qc_config.py`
- **Climatology limits**: edit `config/climatology_test/station_climatology_config.json`. **Station deep vs shallow**: edit `config/climatology_test/station_depth_classification.json`. Programmatic runners can still pass `climatology_overrides`.
- **ERDDAP XML sync paths**: pass flags to `main.py erddap-xml` / `run_erddap_xml_sync(...)`.

### Station resolution logic

- Station ID is read from the `station` variable inside each NetCDF file.
- Expected coordinates come from `config/location_test/Station_Mean_Coords.csv`.
- If no station mapping is found, the location test returns NOT_EVALUATED.

### Climatology resolution logic

- Station ID (same as above) is looked up in `config/climatology_test/station_depth_classification.json`: lists `deep_cast` and `shallow_cast` station IDs (case-insensitive match; trailing `.0` is stripped).
- **Deep** is checked before **shallow** if a station were ever listed twice (should not happen).
- The matching list selects either `deep_cast_limits` or `shallow_cast_limits` from `config/climatology_test/station_climatology_config.json` (variable name → list of rule dicts for `ioos_qc`).
- If the station appears in neither list, or the chosen limits block is missing or empty, climatology is not applied (flags stay NOT_EVALUATED for that test).
- `shallow_stable` is no longer used; only deep vs shallow cast types are supported.

### Output naming

Pipeline-generated QC variables use CF/QARTOD-style names and attributes. Each mapped data variable receives an aggregate flag plus per-test flags, and its `ancillary_variables` attribute is overwritten to point to those pipeline-generated flags. Legacy source variables such as `{variable}_qc` are left in the file unchanged, but processed science variables no longer point to them via `ancillary_variables`.

All new QC variables include `flag_values = 1, 2, 3, 4, 9`, `flag_meanings = "PASS NOT_EVALUATED SUSPECT FAIL MISSING"`, `units = "1"`, and a test-specific `standard_name`.

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

From `config/variable_mapping/walton_mapping.json` :


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

