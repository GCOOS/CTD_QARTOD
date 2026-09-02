## CNV Conversion and NetCDF QC Pipeline

### Components

- `cnv_mapping.py`: Recursively inventories CNV headers and derives source-keyed mappings from stable comments and units.
- `cnv_converter.py`: Derives cruise/station identity from actual filenames, writes QC-compatible NetCDF profiles atomically, and emits `conversion_report.json`.
- `main.py`: CLI entry point with subcommands `inspect-cnv`, `convert-cnv`, `qc`, `erddap-xml`, and `viz`.
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

### Tests

Run the full unit suite from the repository root (after `pip install -r requirements.txt`):

```bash
python -m pytest tests/ -q
```

### Dataset configuration (`config/`)

Configuration is owned by dataset family. The Walton Smith folder contains its
profile and per-test QC settings. The Hogarth CNV folder independently owns
CNV interpretation, QC category membership, instruments, units, and thresholds.

For a **new dataset**, start from [config_template/](config_template/README.md):
copy it to `config/<dataset_name>/`, then validate every mapping,
station file, threshold, and metadata value.

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
└── hogarth_cnv/
    ├── dataset_profile.json
    ├── cnv_mapping.json
    ├── qc_variable_mapping.json
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
fallback files or CLI path overrides.

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


### Running via CLI

The top-level command requires a **subcommand**: `inspect-cnv`, `convert-cnv`, `qc`, `erddap-xml`, or `viz`.

```bash
python main.py --help
python main.py inspect-cnv --help
python main.py convert-cnv --help
python main.py qc --help
python main.py erddap-xml --help
```

#### CNV inspection and conversion

```bash
python main.py inspect-cnv /path/to/cnv/input \
  --output config/YOUR_DATASET/cnv_mapping.json

# Review cnv_mapping.json, then:
python main.py convert-cnv \
  /path/to/cnv/input \
  --mapping config/YOUR_DATASET/cnv_mapping.json \
  --output output/SFER_CNV
```

Both commands accept one CNV file, one cruise folder, or a root containing
cruise folders. Inspection reads headers only and auto-maps comments that have
one stable meaning and unit; ambiguous entries remain `review`. Conversion requires exact `timeS`, `longitude`, and `latitude` columns,
uses the mapped vertical field, preserves source values/units, and writes all
four structural variables as `(profile, z)` arrays.

Identity comes from the actual basename `<cruiseID>_Stn.<station>.cnv`, never
the embedded `FileName`. Numeric leading zeroes are removed, so station `054b`
becomes `54b` and remains distinct from station `54`. Repeated identical
cruise/station identities receive `-2`, `-3`, and later filename suffixes.

Science mappings provide a base destination. If a mapped source occurs more
than once, suffixing happens after mapping: `t090C -> temperature` produces
`temperature`, `temperature_2`, and so on. Existing NetCDF targets are not
replaced unless `--overwrite` is supplied.

Conversion does not create QARTOD flags. Pass the resulting cruise tree to the
existing QC command, then use the QC output for visualization or publication:

```bash
python main.py qc \
  --profile config/hogarth_cnv/dataset_profile.json
python main.py viz --profile config/hogarth_cnv/dataset_profile.json
python main.py erddap-xml --profile config/hogarth_cnv/dataset_profile.json
```

`cnv_mapping.json` maps CNV source names to the QC-facing NetCDF contract;
`qc_variable_mapping.json` independently selects which resulting NetCDF
variables enter each QC category.
See the step-by-step
[`docs/cnv-conversion-process.md`](docs/cnv-conversion-process.md) guide for how
each configuration and CNV section is processed, how NetCDF is constructed,
and where the existing QC pipeline takes over.

#### `qc` — run QC on NetCDF trees

```
--profile                   Dataset profile JSON (default: config/walton_smith/dataset_profile.json)
--verbose, -v               Debug logging
--log-file                  Optional log file path
```

Run with defaults:

```bash
python main.py qc
```

The command reads its input root, output mode, test modes, and every QC path
from the profile. It writes `qc_run_manifest.json` under the effective output
root and does not run ERDDAP generation implicitly.

#### `erddap-xml` — generate ERDDAP `datasets.xml` from NetCDF files

After QC adds `*_qc_*` variables, this explicit step builds a new standalone
`<erddapDatasets>` document. One `<dataset>` is generated per NetCDF file.

```
--profile                   Dataset profile JSON (default: config/walton_smith/dataset_profile.json)
--verbose, -v               Debug logging
```

Examples:

```bash
python main.py erddap-xml
python main.py erddap-xml --profile config/hogarth_cnv/dataset_profile.json
```

The profile's `erddap` object supplies `output_xml`, the server `filedir_prefix`,
an optional `dataset_id_prefix`, `required_global_attributes`, and
`global_add_attributes`. The NetCDF tree is the profile's QC output when
duplicate mode is selected. Universal ERDDAP structure is generated in code;
there is no input XML or dataset template.

NetCDF global attributes remain source metadata. The generator validates them
together with configured global additions, but writes only deliberate
additions/overrides/removals to the dataset-level `<addAttributes>`. Variable
attributes come from NetCDF; QC variables receive `ioos_category=Quality` when
needed, and the sole published time receives the XML-only `time_precision`.
Requires `lxml` (see `requirements.txt`).

#### `viz` — inspect saved QC results in Dash

Launch a local read-only dashboard over the QC NetCDF output tree:

```bash
python main.py viz
```

By default, `viz` reads `profile.output.directory` when the profile uses duplicate output mode, otherwise it reads `profile.data_root`. Override only the server address as needed:

```bash
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

- **Gross ranges** (resolution order):
  1. `gross_range_overrides` (call-time)
  2. Dynamic (instrument + unit from `instrument_resolver.py` using `config/walton_smith/gross_range_test/sensor_specs.json` + `variable_sensor_map.json`)
- **Variable-to-sensor mapping**: edit `config/walton_smith/gross_range_test/variable_sensor_map.json`
- **Sensor identifiers and limits**: edit `config/walton_smith/gross_range_test/sensor_specs.json` (`identifiers.long_names` is used for sensor matching)
- **Location tolerance**: edit `config/walton_smith/location_test/location_config.json`
- **Test-to-category mapping**: edit `TEST_CATEGORIES` in `qc_config.py`
- **Climatology limits**: edit `config/walton_smith/climatology_test/station_climatology_config.json`. **Station deep vs shallow**: edit `config/walton_smith/climatology_test/station_depth_classification.json`. Programmatic runners can still pass `climatology_overrides`.
- **Gap/syntax mode and ERDDAP generation**: edit `qc_test_modes` and `erddap` in the selected dataset profile. Selecting `run` for a placeholder currently fails preflight until a real implementation is added, preventing false evaluation.

### Station resolution logic

- Station ID is read from the `station` variable inside each NetCDF file.
- Expected coordinates come from `config/walton_smith/location_test/Station_Mean_Coords.csv`.
- If no station mapping is found, the location test returns NOT_EVALUATED.

### Climatology resolution logic

- Station ID (same as above) is looked up in `config/walton_smith/climatology_test/station_depth_classification.json`: lists `deep_cast` and `shallow_cast` station IDs (case-insensitive match; trailing `.0` is stripped).
- **Deep** is checked before **shallow** if a station were ever listed twice (should not happen).
- The matching list selects either `deep_cast_limits` or `shallow_cast_limits` from `config/walton_smith/climatology_test/station_climatology_config.json` (variable name → list of rule dicts for `ioos_qc`).
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

From `config/walton_smith/variable_mapping/walton_mapping.json`:


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
