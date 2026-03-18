## NetCDF QC Pipeline

### Components

- `qc_config.py`: Centralized configuration including:
  - `DATASET_DIR`: Default dataset directory path
  - `QC_FLAGS`: QARTOD quality flag definitions
  - `GROSS_RANGE_CONFIG`: Static fallback gross range limits per variable
  - `LOCATION_TOLERANCE`: Tolerance (degrees) for location test
  - `ALL_CATEGORIES`: All variable categories from the mapping
  - `TEST_CATEGORIES`: Maps each test to the categories it applies to
  - `CLIMATOLOGY_CONFIG`: Placeholder climatology bins (monthly)
  - Config file paths (`VARIABLE_MAPPING_JSON`, `STATION_COORDS_CSV`, `STATION_CLIMATOLOGY_JSON`, `SENSOR_SPECS_JSON`, `VARIABLE_SENSOR_MAP_JSON`)
- `qc_data_loader.py`: Loads NetCDF, loads `walton_mapping.json`, finds variables needing QC, and locates lon/lat fields.
- `qc_tests.py`: Test implementations wrapping `ioos_qc` (gross_range, climatology) plus custom tests (gap, syntax, location, decreasing_radiance).
- `qc_writer.py`: Writes QC arrays back to the dataset with standard QARTOD-like attributes.
- `qc_runner.py`: Orchestrates QC for a file, directory, or the full dataset tree. Also provides individual test execution with `run_single_test()`.
- `qc_result_viz.py`: Contains `QCTestResult` dataclass for storing test results and provides visualization via `plot_profile()` with a 3-panel layout.
- `station_resolver.py`: Looks up expected station coordinates from `Station_Mean_Coords.csv` using station ID from the NetCDF file.
- `instrument_resolver.py`: Extracts instrument metadata from each NetCDF, reads variable units, verifies sensor presence, and builds dynamic gross range spans using `sensor_specs.json` + `variable_sensor_map.json`.

### Config files (`config/`)

- `walton_mapping.json`: Variable categories used to decide which tests to run.
- `sensor_specs.json`: Sensor definitions with identifiers (long_name, make_model, serials) and unit-aware ranges.
- `variable_sensor_map.json`: Variable → sensor mapping used for gross range resolution.
- `Station_Mean_Coords.csv`: Expected lat/lon for each station (used by location test).
- `station_climatology_config.json`: Station-specific climatology limits by station type (deep_cast, shallow_cast, shallow_stable).

### QC Flags

| Flag | Meaning |
|------|---------|
| 1 | PASS |
| 2 | NOT_EVALUATED |
| 3 | SUSPECT |
| 4 | FAIL |
| 9 | MISSING |

### Tests
All tests are configured via `TEST_CATEGORIES` in `qc_config.py`. Each test maps to the set of variable categories it applies to.

#### Required Test
| Test | Description | Categories |
|------|-------------|------------|
| `gap_test` | Placeholder (all NOT_EVALUATED) | All |
| `syntax_test` | Placeholder (all NOT_EVALUATED) | All |
| `location_test` | Compares lon/lat to expected station coordinates | All |
| `gross_range_test` | Uses `ioos_qc.qartod.gross_range_test` with sensor-aware limits | All |
| `decreasing_radiance_test` | Checks that values decrease with increasing depth | PAR, in_water_radiance_irradiance |
| `climatology_test` | Uses `ioos_qc.qartod.climatology_test` with station-type limits | temperature, practical_salinity, oxygen_dissolved_oxygen |

#### Strongly recommended tests
| Test | Description | Categories |
|------|-------------|------------|
| `photic_zone_limit_test` | ... | PAR, in_water_radiance_irradiance |
| `spike_test` | ... | All |
| `rate_of_change_test` | ... | All |
| `flat_line_test` | ... | All |
| `climatology_test` | ... |     "in_water_radiance_irradiance","above_water_radiance_irradiance","beam_attenuation","turbidity","PAR","chlorophyll","CDOM","FDOM","backscattering_volume_scattering", |





### Running via CLI

```bash
python main.py --help
```

Full CLI options:

```
--base-dir                  Base directory containing cruise directories
--mapping-path              Path to variable mapping JSON
--location-tolerance        Tolerance in degrees for location test
--station-coords            Path to station coordinates CSV
--station-climatology-config Path to station climatology config JSON
--sensor-specs              Path to sensor specs JSON
--variable-sensor-map       Path to variable-sensor map JSON
```

Run with defaults:

```bash
python main.py
```

Run with custom paths:

```bash
python main.py --base-dir /path/to/datasets --sensor-specs /path/to/sensor_specs.json
```

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
run_qc_for_all()  # Uses DATASET_DIR from qc_config.py
```

### Individual Test Execution and Visualization

Run individual QC tests for debugging and analysis:

```python
from qc_runner import run_single_test, list_available_tests, list_file_variables

# List available tests (full names)
tests = list_available_tests()
# ['gap_test', 'syntax_test', 'location_test', 'gross_range_test', 'decreasing_radiance_test', 'climatology_test']

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
  2. Dynamic (instrument + unit from `instrument_resolver.py` using `sensor_specs.json` + `variable_sensor_map.json`)
  3. Static defaults (`GROSS_RANGE_CONFIG` in `qc_config.py`)
- **Variable-to-sensor mapping**: edit `config/variable_sensor_map.json`
- **Sensor identifiers and limits**: edit `config/sensor_specs.json`
- **Location tolerance**: edit `LOCATION_TOLERANCE` in `qc_config.py`
- **Test-to-category mapping**: edit `TEST_CATEGORIES` in `qc_config.py`
- **Climatology limits**: edit `config/station_climatology_config.json` or pass `climatology_overrides` to runners

### Station resolution logic

- Station ID is read from the `station` variable inside each NetCDF file.
- Expected coordinates come from `config/Station_Mean_Coords.csv`.
- If no station mapping is found, the location test returns NOT_EVALUATED.

### Output naming

QC variables are named data as `{variable}_qc_{test_name}` (e.g., `sea_water_temperature_qc_gross_range_test`) with QARTOD metadata (`flag_values`, `flag_meanings`, `conventions`).(may need to change for better naming, but current one is easy to implement)

### QC variable names

Each data variable that appears in the variable mapping and in the file gets QC tests based on its category. The corresponding QC variable written to the file is `{variable}_qc_{test_name}`.

| QC test | QC variable suffix | Applied to |
|---------|-------------------|------------|
| Gap test | `_qc_gap_test` | All mapped variables |
| Syntax test | `_qc_syntax_test` | All mapped variables |
| Location test | `_qc_location_test` | All mapped variables |
| Gross range test | `_qc_gross_range_test` | All mapped variables |
| Decreasing radiance test | `_qc_decreasing_radiance_test` | PAR, in_water_radiance_irradiance |
| Climatology test | `_qc_climatology_test` | temperature, practical_salinity, conductivity, pressure, oxygen_dissolved_oxygen |

### Mapped data variables

From `config/walton_mapping.json` :

| Category | Variables |
|----------|-----------|
| temperature | `sea_water_temperature`, `sea_water_temperature_2` |
| practical_salinity | `sea_water_salinity`, `sea_water_salinity_2` |
| conductivity | `sea_water_electrical_conductivity`, `sea_water_electrical_conductivity_2` |
| pressure | `sea_water_pressure` |
| oxygen_dissolved_oxygen | `dissolved_oxygen`, `oxygen_saturation`, `oxygen_saturation_2` |
| PAR | `photosynthetically_available_radiation` |
| above_water_radiance_irradiance | `surface_photosynthetically_available_radiation` |
| beam_attenuation | `beam_attenuation` |
| turbidity | `sea_water_turbidity` |
| chlorophyll | `chlorophyll_concentration`, `chlorophyll_fluorescence` |
| CDOM | `CDOM` |
