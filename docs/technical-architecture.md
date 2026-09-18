# CTD_QARTOD technical architecture

**Status:** current schema-5 and consolidated-template implementation, reviewed 2026-09-18.
**Scope:** the supported commands in `main.py` and their implementation modules. Performance proposals are identified separately and are not implemented features.

## 1. System purpose and boundaries

CTD_QARTOD is a local Python application for converting processed Sea-Bird CTD data into NetCDF profiles, applying configurable quality-control tests, inspecting their flags, and preparing ERDDAP dataset definitions. Existing compatible NetCDF archives can enter directly at the QC stage.

The application is organized around files and dataset profiles. It has no database, task queue, distributed scheduler, or hosted processing API in the main workflow. Batch processing runs synchronously in a single Python process. A separate Dash application provides a browser-based viewer.

Scientific policy remains human-owned: recognized variables are mapped automatically from the catalog, while users resolve unknowns and review copied limits, station references and publication metadata. The software applies those decisions and records results. It does not establish the scientific validity of a threshold or replace expert data review.

CNV conversion starts from processed ASCII measurements in geophysical units. It does not perform the manufacturer's raw instrument calibration, sensor alignment, or raw acquisition processing. ERDDAP XML generation prepares configuration; it does not copy files to a server, deploy ERDDAP, or verify remote publication.

## 2. Workflow and entry points

The stages are separate commands; running QC does not automatically start visualization or generate XML.

| Stage | CLI command | Inputs | Outputs |
|---|---|---|---|
| Inspect source | `inspect-cnv` | CNV file or directory tree | Source-keyed mapping JSON with inspection findings |
| Convert | `convert-cnv` | Processed CNV, reviewed mapping, dataset profile | Cruise-organized NetCDF and conversion report |
| Draft sensor configuration | `generate-sensor-config` | Converted NetCDF, CNV mapping, QC variable mapping | Sensor specification and variable-to-sensor JSON files |
| Initialize limits | `generate-limits` | NetCDF inventory and shared defaults | Independent dataset-owned QC settings |
| Apply QC | `qc` | NetCDF tree and dataset-owned QC configuration | QC-enriched NetCDF and run manifest |
| Review | `viz` | Effective QC output tree and profile | Dash/Plotly viewer and optional cached issue index |
| Prepare publication | `erddap-xml` | Effective output NetCDF and ERDDAP profile settings | Standalone `datasets.xml` |

The normal dependency sequence is:

```text
Processed CNV -> inspection -> catalog-resolved mapping -> conversion -> NetCDF
                                                                        |
Existing compatible NetCDF ---------------------------------------------+
                                                                        |
Human-reviewed QC configuration --------------------------------------> QC
                                                                        |
                                                       QC-enriched NetCDF
                                                           /         \
                                                Dash review       ERDDAP XML
```

`main.py` owns argument parsing, command dispatch, logging, and command-level error reporting. QC, visualization and XML default to the existing Walton Smith profile. CNV conversion without `--profile` creates or reuses a profile named from the input scope. The QC CLI operates on the profile's entire data root; file-level and directory-level QC are exposed as Python functions rather than separate CLI options.

## 3. Technology stack

Versions below are direct dependency pins in `requirements.txt`, not a guarantee that every local environment has those versions installed.

| Technology | Pinned version | Responsibility |
|---|---|---|
| Python | Not pinned in requirements | CLI, configuration, filesystem operations, orchestration |
| NumPy | 2.4.4 | Numerical arrays, comparisons, flag generation and aggregation |
| xarray | 2026.4.0 | Labeled datasets, dimension handling, NetCDF reads/writes |
| netCDF4 | 1.7.4 | NetCDF backend; conversion explicitly selects this engine |
| ioos_qc | 2.3.0 | Gross-range, climatology, and spike test implementations |
| lxml | 6.1.0 | ERDDAP XML construction and serialization |
| Dash | 4.1.0 | Local browser application and callbacks |
| Plotly | 6.7.0 | Interactive QC plots |
| Matplotlib | 3.10.9 | Standalone profile-result plotting |
| pytest | 9.0.3 | Unit and integration test execution |

The current QC implementation does not use Dask, a process pool, GPU kernels, or Numba. Direct versions are pinned, but `requirements.txt` is not a complete transitive environment lockfile.

## 4. Module responsibilities

| Module | Responsibility |
|---|---|
| `main.py` | Seven-command CLI and logging |
| `dataset_profile.py` | Profile dataclasses, path resolution, metadata names, sample-axis alignment |
| `cnv_mapping.py` | CNV discovery, header/sensor inventory, mapping generation and validation |
| `cnv_catalog.py`, `cnv_workflow.py` | Shared catalog resolution and automatic profile creation |
| `cnv_coordinates.py`, `cnv_metadata.py` | Source-specific coordinates and global metadata ownership |
| `qc_limit_generation.py`, `qc_template_limits.py` | Initialize local QC settings from `config_template/` |
| `qc_time.py` | Decode actual CF time origin/calendar for QC without changing stored time |
| `cnv_converter.py` | Parse processed measurements, resolve cast identity, construct and validate NetCDF |
| `cnv_sensor_config.py` | Discover sensor/unit combinations and draft null-limit configuration |
| `qc_validation.py` | Batch preflight of required QC configuration |
| `qc_config.py` | Flag constants, test/category applicability, configuration loaders |
| `qc_data_loader.py` | NetCDF opening, variable selection, category and coordinate lookup |
| `station_resolver.py` | Normalize station identifiers and resolve expected coordinates |
| `instrument_resolver.py` | Resolve sensor presence, units, and per-variable gross ranges |
| `qc_tests/` | Individual numerical test functions and flag conversion |
| `qc_runner.py` | Per-file, directory, batch, and single-test orchestration |
| `qc_writer.py` | QC variables, aggregation, ancillary links, history, NetCDF serialization |
| `qc_manifest.py` | Run summary, configuration hashes, runtime metadata, atomic manifest writes |
| `qc_dashboard.py` | File/test discovery, issue indexing, plot loading, Dash layout/callbacks |
| `qc_result_viz.py` | `QCTestResult` and Matplotlib profile visualization |
| `xml_generator/` | ERDDAP policy model and standalone XML generation |

Additional archive-preparation and maintenance utilities exist, including `cnv_identity.py`, `erddap_xml_sync.py`, and scripts under `scripts/`. They are not stages dispatched by the seven-command CLI. In particular, the current `erddap-xml` command invokes `xml_generator.generate`, not the older XML synchronization module.

## 5. Configuration architecture

`DatasetProfile` connects a dataset's storage layout, metadata naming, QC inputs, and ERDDAP policy. The default profile file belongs to `config/walton_smith/`; Hogarth and other dataset configurations have their own directories. `config_template/` is the single reusable source for populated metadata, the CNV catalog, QC categories and Walton Smith-based limits. New datasets automatically receive independent copied/adapted settings; those defaults still need scientific review. Existing profiles and edits are preserved. The historical Hogarth schema-1 mapping is not supported for fresh conversion.

| Profile field | Meaning |
|---|---|
| `data_root` | Existing NetCDF input root; also the destination of CNV conversion |
| `output.mode` | `duplicate` or `in_place` QC output |
| `output.directory` | Destination root in duplicate mode |
| `metadata` | Station, cruise, longitude, latitude, depth, time, and sample-dimension names |
| `paths` | Explicit paths to mappings, station tables, thresholds, and sensor specifications |
| `qc_test_modes` | Explicit placeholder behavior for gap and syntax tests |
| `netcdf_global_attributes` | Editable populated human defaults; null omits a value |
| `netcdf_fixed_global_attributes` | Configured institutional, publication and convention defaults |
| `netcdf_derived_global_attributes` | Computed metadata descriptions and title/summary templates |
| `erddap` | XML destination, server-side file prefix, required globals, and attribute overrides |

Relative paths resolve against the repository root, including relative paths inside a profile. They do not resolve against the profile's parent directory. This matters when copying configuration to another checkout.

Three definitions have different responsibilities:

- `config_template/cnv_catalog.json` owns source aliases, metadata, sensor associations and permitted units.
- Dataset `cnv_mapping.json` records observations and `mapped_to` catalog keys. Null targets require resolution; routine attributes and actions are not duplicated.
- `config_template/qc_variable_mapping.json` defines QC categories. Initialization saves an adapted local copy for actual output names.

Successful conversion or first QC initializes settings only when the profile has no QC paths. Existing paths are authoritative: no `qc_limits` switch, shared-runtime fallback, nested QC folder or per-cast override file. Gross ranges and thresholds are matched by measurement and unit; missing/incompatible units never justify guessing.

Test applicability is defined by the Python `TEST_CATEGORIES` table; the profile is not a general-purpose plugin registry. Station coordinates are CSV; most other policy inputs are JSON. The default profile object is cached, but the per-file QC path rereads its supporting configuration files.

Batch validation checks required files, schema relationships, numerical limits, station classifications, and threshold consistency before processing NetCDF files. A sensor range with both bounds null is permitted as unconfigured; partially specified ranges are rejected. Passing configuration validation does not mean every selected variable has enough information to be evaluated.

## 6. CNV ingestion and NetCDF contract

Inspection recursively inventories CNV headers and embedded sensor XML. Stable descriptions and units support automatic mapping; ambiguous entries require review. Conversion loads the reviewed mapping and validates the source header, column declarations, numerical row count, required fields, timing, and filename identity.

The actual filename supplies cruise and station identity. Numeric station leading zeros are normalized while alphanumeric distinctions are retained. Repeated cruise/station identities are ordered by cast start time and source path, then receive `-2`, `-3`, and subsequent filename suffixes. Duplicate science channels receive suffixes after mapping their destination names.

Each converted cast is written under `<data_root>/<cruise>/<cast>.nc` with one profile and a `z` sample dimension. Time and depth have dimensions `(profile, z)`. Complete latitude/longitude columns retain `(profile, z)`; when both are absent, valid NMEA headers supply cast-level `(profile)` coordinates. Partial/invalid column pairs do not fall back to headers. Science measurements follow the cast's sample structure. `z` represents sample order; depth is a measurement variable, not an assumption that sample spacing is uniform.

Source bad-flag values are converted to NaN. Time is stored numerically with units relative to the parsed cast start time. Science values are not rescaled by unit conversion; equivalent unit spelling changes retain the original spelling in `source_units`. Instrument variables and science-variable links preserve sensor metadata. Generated identity, coverage, and processing attributes cannot be overridden by supplied publication attributes; null publication values are omitted.

Conversion parses casts before writing, retains successful parsed casts in memory, groups duplicate identities, and writes outputs serially. It records individual failures and continues with other casts. A temporary NetCDF is reopened to verify dimensions and identity before replacement of the destination. Existing targets require explicit overwrite authorization through the CLI flag. The conversion report contains counts and per-source status, warnings, mappings, output identity, and failures; the CLI exits unsuccessfully if any conversion failed.

The normal workflow copies/adapts populated defaults using `generate-limits` internally. The optional `generate-sensor-config` skeleton utility separately scans converted NetCDF instrument links and selected QC variables. It drafts sensor/unit bounds as `min: null` and `max: null`; it does not infer scientifically appropriate limits.

## 7. QC execution model

### Batch lifecycle

`run_qc_for_all()` performs the following sequence:

1. Initialize independent local settings if the profile has no QC paths, then discover and sort `data_root/*/*.nc`, exactly one directory level below the root.
2. Build a run manifest containing configuration hashes and write its initial `running` state.
3. Validate all required QC configuration; reject an empty input set.
4. Process files sequentially through `run_qc_for_file()`.
5. Update processed/written counters and rewrite the manifest after each successful file.
6. Mark the manifest `complete`, or record `failed` and re-raise an exception.

The initial manifest construction/write occurs outside the guarded processing block. Errors there are not guaranteed to produce a failed manifest. Later failures stop the batch; already-written files remain. There is no rollback of the batch, per-file retry queue, or automatic resume/skip mechanism.

`run_qc_for_directory()` processes `*.nc` non-recursively. Calling it or `run_qc_for_file()` directly does not execute the full batch preflight/manifest lifecycle.

### Per-file lifecycle

The loader opens NetCDF with `decode_cf=False` and `mask_and_scale=True`. This is not a promise of fully CF-decoded values: the runner explicitly handles nonfinite values and encoded `_FillValue`/`missing_value` sentinels.

Climatology decodes CF time using its stated origin and supported calendar, masks missing dates, and leaves stored numeric time unchanged. Unsupported calendars fail rather than being reinterpreted.

For each file, the runner loads mappings and policy, extracts station/cruise identifiers, resolves station coordinates, derives sensor-aware gross ranges, and chooses station climatology. It selects variables present in both the dataset and QC mapping, then executes applicable tests in a fixed order.

Tests that depend on sample adjacency use the profile's `sample_dimension`. Helpers move that axis to the last position and restore the original output shape. Coordinates are broadcast using named dimensions and checked for compatibility. This avoids assuming that all supplied datasets store samples on the same physical axis.

Each test computes a missing mask, prepares numerical input, evaluates its rule, and applies missing flags. The runner adds the test result to the xarray dataset immediately. After a variable's tests finish, it computes an aggregate flag and updates `ancillary_variables`. History is appended and the full dataset is serialized once per file.

### Test semantics

| Test | Current implementation and boundary |
|---|---|
| Gap / syntax | Explicit placeholders: `NOT_EVALUATED` for ordinary samples, with missing-sample handling; `run` mode is rejected |
| Location | Custom latitude/longitude tolerance against expected station coordinates; not a geodesic-distance calculation |
| Gross range | `ioos_qc` wrapper with sensor/unit-aware resolved spans |
| Climatology | `ioos_qc` wrapper using configured time/depth spans and station classification |
| Decreasing radiance | Custom adjacent value/depth directional check for applicable optical categories |
| Flat line | Custom count-based comparison of the current value with each preceding value in configured windows |
| Spike | `ioos_qc` average/neighbor-midpoint method with variable-specific thresholds |
| Rate of change | Custom absolute adjacent-sample difference; not normalized by elapsed time |

Missing required inputs or unavailable per-variable limits can yield `NOT_EVALUATED`. The presence of a QC array does not prove that a test actually evaluated every sample. The custom rules should not be described as interchangeable with every similarly named QARTOD implementation.

## 8. Output, flags, and provenance

| Flag | Meaning |
|---|---|
| 1 | PASS |
| 2 | NOT_EVALUATED |
| 3 | SUSPECT |
| 4 | FAIL |
| 9 | MISSING |

Per-test arrays use `{variable}_qc_{suffix}`, with suffixes defined in `QC_TEST_METADATA`: `gap`, `syntax`, `location`, `gross_range`, `climatology`, `flat_line`, `spike`, `rate_of_change`, and `decreasing_radiance_test`. Aggregate arrays use `{variable}_qc_agg`. Stored flags are `int8`. Aggregation is explicit rather than a numerical maximum: FAIL overrides SUSPECT, which overrides PASS. With none of those, the default is NOT_EVALUATED; all-missing test flags or NaN source data produce MISSING. The runner's per-test missing masks also cover nonfinite values and encoded sentinels.

Each test variable carries flag metadata, its target/test/module identifiers, serialized applied configuration, and configuration-source references. Science variables link to their QC arrays through `ancillary_variables`; processing appends to global history.

The run manifest records command arguments, Git revision, Python and selected package versions, input/output roots, configuration paths and SHA-256 hashes, timestamps, counters, and an error on failure. It does not currently contain source/output file hashes, a persisted per-file completion ledger, or a dirty-working-tree digest. The manifest is therefore useful provenance, but not a complete immutable reproduction bundle. Supporting configuration is reread during processing rather than frozen from the hashed content.

In duplicate mode, the writer mirrors source paths under the output root. In-place mode replaces the input file. The writer loads data, constructs a dataset with cleaned fill-value attributes, writes a sibling `.qctmp` file, and replaces the destination. Floating variables are explicitly encoded as float64; this is a semantic rewrite, not a byte-preserving copy of original NetCDF encoding, packing, or compression.

The QC writer does not explicitly close the original opened dataset in a `finally` block and does not implement the converter's temporary-file read-back validation. A failed write may leave a temporary file. Concurrent runs targeting the same destination are not coordinated and would share deterministic temporary/output names.

## 9. Visualization and publication preparation

The Dash viewer defaults to `127.0.0.1:8050` and reads the effective output root: the duplicate directory when configured, otherwise the data root. It discovers cruises, files, mapped variables, and available QC arrays, with interactive flag filtering, profile navigation, and contextual location/climatology display. It inspects stored results rather than serving as a batch-processing scheduler or editing source scientific values.

The optional issue index scans flag arrays and stores per-file/variable/test counts in `output/qc_dashboard_index.json`. It is a derived cache, not the authoritative QC record. The indexing loop currently skips files that raise exceptions; an apparently complete index is not evidence that every file was successfully inspected. This default cache path is shared across profiles.

The XML generator recursively discovers NetCDF under the effective output root and creates one `EDDTableFromNcCFFiles` entry per file. Required global attributes are checked; variables and attributes come from NetCDF, with explicit profile overrides and supported legacy-time metadata handling. Duplicate sanitized dataset IDs receive a relative-path hash suffix. The complete XML document is written through a temporary file and replacement.

XML generation does not read or merge an existing `datasets.xml`. It also does not require a successful QC manifest before reading the output tree. A successful XML write verifies local generation, not ERDDAP server acceptance or public availability.

## 10. Performance characteristics and extension boundaries

The current batch architecture is serial, with nested work over files, selected variables, tests, and—in the flat-line implementation—samples. Its main known sources of overhead are repeated xarray variable insertion/alignment, Python flat-line loops, configuration parsing per file, repeated missing-data preparation per test, and NetCDF read/rewrite work. Debug flag summaries are calculated even when debug logging is disabled. The QC runner also imports the Matplotlib-backed result module, introducing visualization dependency initialization into batch startup.

A preliminary six-file cProfile investigation during this architecture review found substantial time in dataset updates and flat-line computation. Those observations identify candidates; they are not a representative archive throughput benchmark or a published speedup result. Temporary outputs used local storage, unlike the source archive on external storage.

The following are **proposed extension points, not current capabilities**:

| Candidate | Existing boundary to preserve |
|---|---|
| Collect flags and attach them in one dataset update | Dimensions, QC attributes, aggregation, ancillary links |
| Vectorize flat-line comparisons | Exact window, threshold, missing-value, and precedence semantics |
| Reuse validated configuration and preprocessing | Consistent provenance and per-variable missing handling |
| Process independent files in bounded worker processes | One owner per output; parent-owned manifest; explicit file closure |

Any future acceleration should compare identical inputs/configuration and check per-test flags, aggregate flags, science values, dimensions, and metadata. Process count, memory use, storage location, startup, and steady-state throughput need separate measurement. Changing thresholds or omitting tests is a scientific behavior change, not a performance-only optimization.

## 11. Operation and verification

Run from the repository root with the intended Python environment and installed requirements. Typical commands are:

```bash
python main.py convert-cnv /path/to/processed-cnv
# Use the dataset profile created automatically under config/<dataset>/.
# Review mappings, sensor limits, station policy, and metadata before QC.
python main.py qc --profile config/YOUR_DATASET/dataset_profile.json
python main.py viz --profile config/YOUR_DATASET/dataset_profile.json
python main.py erddap-xml --profile config/YOUR_DATASET/dataset_profile.json
```

The repository's documented test command is `python -m pytest tests/ -q`. Test modules cover conversion and mapping, profile/configuration behavior, individual QC rules, runner/writer integration, manifests, dashboard helpers, and XML generation. Test presence does not imply a passing suite or scientific validation. The consolidated-template verification passed 259 tests (12 skipped, one slow test deselected). A real SAV1803 cast also completed conversion, QC and XML from the consolidated template. The preceding full SAV1803 rerun produced 31 files at each stage without changing scientific arrays or QC flags. These results do not establish full-archive support or scientific validity of copied defaults.

For acceptance of a processing run, inspect conversion failure records, the QC manifest's terminal status and counts, output flag semantics and metadata, and—when publishing—the actual ERDDAP deployment separately.

## 12. Source map and related documentation

Implementation references:

- [CLI](../main.py), [dataset profiles](../dataset_profile.py), [dependencies](../requirements.txt)
- [CNV mapping](../cnv_mapping.py), [conversion](../cnv_converter.py), [sensor configuration](../cnv_sensor_config.py)
- [QC runner](../qc_runner.py), [configuration validation](../qc_validation.py), [test applicability](../qc_config.py)
- [QC writer](../qc_writer.py), [run manifest](../qc_manifest.py), [individual tests](../qc_tests/)
- [Dashboard](../qc_dashboard.py), [standalone plots](../qc_result_viz.py), [XML generation](../xml_generator/generate.py)

Related guides:

- [Project README](../README.md)
- [CNV conversion process](cnv-conversion-process.md)
- [CNV versus supplied NetCDF](cnv-vs-supplied-netcdf.md)
- [New-dataset configuration template](../config_template/README.md)
