"""Regression contracts for the automatic CNV -> QC -> XML workflow."""
import json
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from lxml import etree

from cnv_catalog import catalog, match_entry, normalize_unit, qc_entry
from cnv_converter import convert_cnv, filename_identity
from cnv_mapping import discover_cnv_files, inspect_cnv
from cnv_workflow import default_mapping_path, prepare_profile
from cnv_metadata import publication_metadata
from dataset_profile import load_dataset_profile, resolve_config_path
from qc_limit_generation import generate_limits, prepare_dataset_qc
from qc_runner import run_qc_for_all
from qc_template_limits import template_gross_ranges, template_thresholds
from xml_generator.generate import generate_erddap_xml_for_profile
from test_cnv_converter import _cnv


@pytest.mark.parametrize('filename,identity', [
    ('WS0612_001.cnv', ('WS0612', '1')),
    ('SAV1803-12.cnv', ('SAV1803', '12')),
    ('WS15152Sta59.cnv', ('WS15152', '59')),
    ('WS24139.Stn002.cnv', ('WS24139', '2')),
    ('HG26193_Stn.TB1.cnv', ('HG26193', 'TB1')),
    ('HG26193_Stn.TB1_003.cnv', ('HG26193', 'TB1')),
    ('HG26193_Stn.009_081.cnv', ('HG26193', '9')),
    ('HG26193_Stn.009.5_080.cnv', ('HG26193', '9_5')),
    ('HG26193_Stn.057.1_068.cnv', ('HG26193', '57_1')),
    ('HG26193_Stn.21LK_088.cnv', ('HG26193', '21LK')),
    ('HG12_Stv.057-2_069.cnv', ('HG12', '57_2')),
    ('HG26193_Stn.TB1_7.cnv', ('HG26193', 'TB1')),
    ('HG26193_Stn.TB1_1234.cnv', ('HG26193', 'TB1')),
    ('WB22215_Stn.TB1_003.cnv', ('WB22215', 'TB1')),
    ('WS24258_Stn.009.5.cnv', ('WS24258', '9_5')),
    ('WS24258_Stn.009.5_2.cnv', ('WS24258', '9_5')),
    ('WS24258_Stn.057.1_068.cnv', ('WS24258', '57_1')),
    ('WS20278_Stn.9_5.cnv', ('WS20278', '9')),
    ('WS24258_Stn.054b.cnv', ('WS24258', '54-2')),
    ('WS24258_Stn.021.cnv', ('WS24258', '21LK')),
    ('WS24258_Stn.021-5.cnv', ('WS24258', '21_5')),
    ('WS24258_Stn.MRv2.cnv', ('WS24258', 'MR-2')),
    ('WS24258_Stn.021LK.cnv', ('WS24258', '21LK')),
    ('WS2425A_Stn.001.cnv', ('WS2425A', '1')),
])
def test_identity_patterns_preserve_station_suffix(filename, identity):
    assert filename_identity(filename) == identity


def test_conflicting_cruise_and_test_cast_need_review():
    with pytest.raises(ValueError, match='identity review'):
        filename_identity('WS20279_cnv/WS20278_Stn.002.cnv')
    with pytest.raises(ValueError, match='review'):
        filename_identity('WS0612_decktest.cnv')


def test_catalog_matches_beam_aliases_and_preserves_par_units():
    for code, description in [('xmiss', 'Beam Transmission, Chelsea/Seatech [%]'),
                              ('CStarTr0', 'Beam Transmission, WET Labs C-Star [%]')]:
        assert match_entry(code, [description], ['%'])['target'] == 'beam_transmission'
    assert normalize_unit('Watts/m^2') == 'W m-2'
    assert normalize_unit('umol photons/m^2/sec') == 'umol m-2 s-1'
    assert normalize_unit(None) is None
    assert match_entry('t090C', ['Potential Temperature [deg C]'], ['deg C']) is None


def test_walton_measurements_remain_distinct_from_qc_categories():
    from cnv_catalog import source_entry, template_categories
    temperature = source_entry('t090C')
    seapoint = source_entry('flSP')
    fluorescence = source_entry('flECO-AFL')
    concentration = source_entry('wetStar')
    assert temperature['target'] == 'sea_water_temperature'
    assert temperature['qc_category'] == 'temperature'
    assert seapoint['target'] == 'seapoint_fluorescence'
    assert seapoint['qc_category'] is None
    assert fluorescence['target'] == 'chlorophyll_fluorescence'
    assert concentration['target'] == 'chlorophyll_concentration'
    assert fluorescence['qc_category'] == concentration['qc_category'] == 'chlorophyll'
    assert fluorescence['target'] in template_categories()['chlorophyll']
    assert concentration['target'] in template_categories()['chlorophyll']


def test_inspection_supports_header_inventory_without_coordinates(tmp_path):
    source = _cnv(tmp_path / 'WS0612_001.cnv')
    # Historical-format columns with valid declarations but no coordinate fields.
    source.write_text(source.read_text().replace('latitude: Latitude [deg]', 'lat_raw: raw latitude').replace('longitude: Longitude [deg]', 'lon_raw: raw longitude'))
    mapping = inspect_cnv(source, tmp_path / 'mapping.json')
    assert mapping['inspection']['inspected_file_count'] == 1
    issues = mapping['inspection']['conversion_issues']
    assert len([issue for issue in issues if 'source' in issue]) == 1
    assert {issue['source_name'] for issue in issues if 'source_name' in issue} == {'lat_raw', 'lon_raw'}
    assert mapping['science_variables']['lat_raw']['mapped_to'] is None


def test_final_processing_stage_selected_when_scanning_parent(tmp_path):
    early = _cnv(tmp_path / 'WS24258' / 'cnv' / '01-cnv' / 'WS24258_Stn.1.cnv')
    final = _cnv(tmp_path / 'WS24258' / 'cnv' / '06-drv' / 'WS24258_Stn.1.cnv')
    assert discover_cnv_files(tmp_path) == (final,)
    assert discover_cnv_files(early.parent) == (early,)


def test_incremental_repeat_names_remain_stable(tmp_path):
    early = _cnv(tmp_path / 'first' / 'WS24258_Stn.1.cnv')
    late = _cnv(tmp_path / 'second' / 'WS24258_Stn.1.cnv', start_time='Sep 19 2024 12:21:12')
    mapping = tmp_path / 'mapping.json'
    inspect_cnv(early, mapping)
    first = convert_cnv(early, tmp_path / 'out', mapping)
    second = convert_cnv(late, tmp_path / 'out', mapping)
    assert first['records'][0]['output_stem'] == 'WS24258_1'
    assert second['records'][0]['output_stem'] == 'WS24258_1-2'
    rerun = convert_cnv(late, tmp_path / 'out', mapping, overwrite=True)
    assert rerun['records'][0]['output_stem'] == 'WS24258_1-2'
    assert rerun['records'][0]['repeat'] == 2


def test_incompatible_units_cannot_be_relabelled(tmp_path):
    source = _cnv(tmp_path / 'WS24258_Stn.1.cnv')
    mapping = tmp_path / 'mapping.json'
    payload = inspect_cnv(source, mapping)
    source.write_text(source.read_text().replace('deg C', 'K'))
    report = convert_cnv(source, tmp_path / 'out', mapping)
    assert report['counts']['failed'] == 1
    assert 'unit conversion is required' in report['records'][0]['failure_reason']
    assert not list((tmp_path / 'out').rglob('*.nc'))


def test_template_limits_match_units_and_do_not_guess_missing_units():
    dataset = xr.Dataset({
        'temperature_2': ('z', [10.0], {'source_name': 't190C', 'units': 'degree_Celsius'}),
        'par_energy': ('z', [10.0], {'source_name': 'par', 'units': 'W m-2'}),
        'par_photons': ('z', [10.0], {'source_name': 'par', 'units': 'umol m-2 s-1'}),
        'par_unknown': ('z', [10.0], {'source_name': 'par'}),
        'oxygen_mass': ('z', [1.0], {'source_name': 'sbeox0Mg/L', 'units': 'mg l-1'}),
    })
    limits = template_gross_ranges(dataset)
    assert limits['temperature_2']['fail_span'] == (0, 35)
    assert limits['par_energy']['fail_span'] == (0, 1100)
    assert limits['par_photons']['fail_span'] == (0, 5000)
    assert 'par_unknown' not in limits
    assert 'oxygen_mass' not in limits
    assert template_thresholds(dataset, {'sea_water_temperature': {'threshold': 0.4}}) == {'temperature_2': {'threshold': 0.4}}


def test_automatic_end_to_end_and_editable_limit_folder(tmp_path):
    source = _cnv(tmp_path / 'input' / 'WB24258_Stn.001.cnv')
    profile_path = prepare_profile(source, root=tmp_path)
    assert prepare_profile(source, root=tmp_path) == profile_path
    profile = load_dataset_profile(profile_path)
    assert profile.netcdf_global_attributes['creator_name'] == 'Christopher R. Kelble'
    assert 'qc_limits' not in json.loads(profile.profile_path.read_text())
    assert profile.paths.spike_thresholds is None
    report = convert_cnv(source, profile.data_root, profile.paths.cnv_mapping,
                         netcdf_global_attributes=profile.netcdf_global_attributes,
                         netcdf_fixed_global_attributes=profile.netcdf_fixed_global_attributes,
                         netcdf_derived_global_attributes=profile.netcdf_derived_global_attributes)
    assert report['counts']['converted'] == 1
    target = profile.data_root / 'WB24258' / 'WB24258_1.nc'
    with xr.open_dataset(target, decode_cf=False) as ds:
        assert ds.attrs['platform'] == 'RV_Weatherbird_II'
        assert ds.attrs['id'] == 'SFER_CTD_WB24258_1'
        assert '2024-09-18' in ds.attrs['summary']
        assert ds.attrs['creator_name'] == profile.netcdf_global_attributes['creator_name']
        assert 'date_issued' not in ds.attrs
        assert ds.attrs['publisher_name'] == profile.netcdf_fixed_global_attributes['publisher_name']
        np.testing.assert_array_equal(ds['sea_water_temperature'].values, [[10, 12]])
    run_qc_for_all(profile=profile, base_dir=profile.data_root)
    profile = load_dataset_profile(profile_path)
    assert 'qc_limits' not in json.loads(profile.profile_path.read_text())
    assert profile.paths.spike_thresholds.is_relative_to(profile_path.parent)
    with xr.open_dataset(profile.output.directory / 'WB24258' / 'WB24258_1.nc', decode_cf=False) as ds:
        assert set(ds['sea_water_temperature_qc_gross_range'].values.ravel()) == {1}
        assert 'date_metadata_modified' in ds.attrs
    generate_erddap_xml_for_profile(profile)
    root = etree.parse(str(profile.erddap.output_xml))
    assert root.xpath('//dataset/@datasetID') == ['SFER_CTD_WB24258_1']
    assert root.xpath('//dataVariable[sourceName="sea_water_temperature"]')
    limits = generate_limits(profile, tmp_path / 'another_qc_snapshot')
    custom = load_dataset_profile(limits['profile'])
    assert 'qc_limits' not in json.loads(profile.profile_path.read_text())
    specs = json.loads(custom.paths.sensor_specs.read_text())
    assert specs['sensors']['sea_water_temperature']['ranges']['degree_Celsius'] == {'min': 0, 'max': 35}
    run_qc_for_all(profile=custom, base_dir=custom.data_root)
    with pytest.raises(FileExistsError):
        generate_limits(profile)


def test_metadata_ownership_and_literal_cast_examples_rejected(tmp_path):
    source = _cnv(tmp_path / 'WS24258_Stn.1.cnv')
    profile_path = prepare_profile(source, root=tmp_path)
    payload = json.loads(profile_path.read_text())
    payload['netcdf_global_attributes']['institution'] = 'another owner'
    profile_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='multiple owners'):
        load_dataset_profile(profile_path)
    with pytest.raises(ValueError, match='template'):
        publication_metadata('WS24258', 'WS24258_1', '2024-09-18', 25, -81,
                             {}, {'title': 'SFER CTD WS24139_41'})


def test_multi_cruise_input_automatically_uses_batch_scope(tmp_path):
    root = tmp_path / 'batch'
    _cnv(root / 'WS24258' / 'WS24258_Stn.1.cnv')
    _cnv(root / 'HG26193' / 'HG26193_Stn.2.cnv')
    profile = load_dataset_profile(prepare_profile(root, root=tmp_path))
    assert profile.profile_path.parent.name == 'batch'
    report = convert_cnv(root, profile.data_root, profile.paths.cnv_mapping)
    assert report['counts']['converted'] == 2
    assert (profile.data_root / 'WS24258' / 'WS24258_1.nc').is_file()
    assert (profile.data_root / 'HG26193' / 'HG26193_2.nc').is_file()


def test_ambiguous_processing_stages_require_explicit_selection(tmp_path):
    _cnv(tmp_path / '01-cnv' / 'WS24258_Stn.1.cnv')
    _cnv(tmp_path / '03-aln' / 'WS24258_Stn.1.cnv')
    with pytest.raises(ValueError, match='select one final input folder'):
        discover_cnv_files(tmp_path)


def test_cli_default_paths_and_generate_limits():
    from main import _parse_args
    assert _parse_args(['convert-cnv', 'input']).profile is None
    assert _parse_args(['inspect-cnv', 'input']).output is None
    assert _parse_args(['generate-limits', '--profile', 'profile.json']).output is None


@pytest.mark.parametrize('name', ['temperature', 'temperature_1', 'temperature_2',
                                  'sea_water_temperature', 'sea_water_temperature_2',
                                  'seawater temperature', 'Sea Water Temperature'])
def test_catalog_canonical_name_and_existing_netcdf_aliases(name):
    assert isinstance(catalog()['variables'], dict)
    assert qc_entry(name, {})['target'] == 'sea_water_temperature'


def test_dataset_snapshot_records_reversible_file_and_channel_mapping(tmp_path):
    source = _cnv(tmp_path / 'input' / 'WS24258_Stn.001.cnv')
    profile = load_dataset_profile(prepare_profile(source, root=tmp_path))
    assert convert_cnv(source, profile.data_root, profile.paths.cnv_mapping)['counts']['converted'] == 1
    prepared = prepare_dataset_qc(profile)
    mapping = json.loads(prepared.paths.source_variable_mapping.read_text())
    channels = mapping['variables']['sea_water_temperature']['files']['WS24258/WS24258_1.nc']
    assert [channel['variable'] for channel in channels] == ['sea_water_temperature', 'sea_water_temperature_2']
    assert [channel['source_name'] for channel in channels] == ['t090C', 't090C']
    assert [channel['source_occurrence'] for channel in channels] == [1, 2]
    assert all(channel['source_file'] == str(source.resolve()) for channel in channels)
    assert all(channel['source_units'] == 'deg C' for channel in channels)
    assert all(channel['units'] == 'degree_Celsius' for channel in channels)


def test_original_netcdf_names_use_same_canonical_mapping(tmp_path):
    from dataset_profile import default_profile
    from cnv_converter import _atomic_json
    source = tmp_path / 'data' / 'WS0603' / 'WS0603_1.nc'
    source.parent.mkdir(parents=True)
    xr.Dataset({
        'sea_water_temperature': ('z', [10.0], {'units': 'degree_Celsius'}),
        'temperature_1': ('z', [11.0], {'units': 'degree_Celsius'}),
    }).to_netcdf(source)
    payload = json.loads(default_profile().profile_path.read_text())
    payload.update(data_root=str(tmp_path / 'data'), paths={})
    _atomic_json(tmp_path / 'config' / 'original_ws' / 'dataset_profile.json', payload)
    profile = prepare_dataset_qc(load_dataset_profile(tmp_path / 'config' / 'original_ws' / 'dataset_profile.json'))
    mapping = json.loads(profile.paths.source_variable_mapping.read_text())
    records = mapping['variables']['sea_water_temperature']['files']['WS0603/WS0603_1.nc']
    assert {record['variable'] for record in records} == {'sea_water_temperature', 'temperature_1'}
    assert {record['source_name'] for record in records} == {'sea_water_temperature', 'temperature_1'}
    categories = json.loads(profile.paths.variable_mapping.read_text())
    assert categories['temperature'] == ['sea_water_temperature', 'temperature_1']
    assert profile.paths.station_coords.is_relative_to(profile.profile_path.parent)


def test_dataset_edits_are_used_without_reading_template_again(tmp_path, monkeypatch):
    import qc_template_limits
    profiles = []
    for cruise in ['WS24258', 'WB24258']:
        source = _cnv(tmp_path / 'input' / f'{cruise}_Stn.001.cnv')
        profile = load_dataset_profile(prepare_profile(source, root=tmp_path))
        convert_cnv(source, profile.data_root, profile.paths.cnv_mapping)
        profiles.append(prepare_dataset_qc(profile))
    first, second = profiles
    specs = json.loads(first.paths.sensor_specs.read_text())
    specs['sensors']['sea_water_temperature']['ranges']['degree_Celsius']['max'] = 11
    first.paths.sensor_specs.write_text(json.dumps(specs))
    monkeypatch.setattr(qc_template_limits, 'CONFIG_TEMPLATE_DIR', tmp_path / 'unavailable_template')
    assert prepare_dataset_qc(first) == first
    run_qc_for_all(profile=first)
    with xr.open_dataset(first.output.directory / 'WS24258' / 'WS24258_1.nc', decode_cf=False) as ds:
        np.testing.assert_array_equal(ds.sea_water_temperature_qc_gross_range.values, [[1, 4]])
    assert json.loads(first.paths.sensor_specs.read_text())['sensors']['sea_water_temperature']['ranges']['degree_Celsius']['max'] == 11
    assert json.loads(second.paths.sensor_specs.read_text())['sensors']['sea_water_temperature']['ranges']['degree_Celsius']['max'] == 35


def test_cli_conversion_automatically_activates_dataset_owned_qc(tmp_path):
    from main import main
    source = _cnv(tmp_path / 'input' / 'WS24258_Stn.1.cnv')
    profile_path = prepare_profile(source, root=tmp_path)
    main(['convert-cnv', str(source), '--profile', str(profile_path)])
    profile = load_dataset_profile(profile_path)
    assert 'qc_limits' not in json.loads(profile.profile_path.read_text())
    assert profile.paths.source_variable_mapping.is_file()
    assert profile.paths.sensor_specs.parent == profile_path.parent / 'gross_range_test'
    assert profile.paths.station_climatology.parent == profile_path.parent / 'climatology_test'
    assert profile.paths.spike_thresholds.parent == profile_path.parent / 'spike_test'
    assert not (profile_path.parent / 'qc').exists()


def test_partial_conversion_initializes_successful_files_but_still_reports_failure(tmp_path):
    from main import main
    source = _cnv(tmp_path / 'input' / 'WS24258_Stn.1.cnv')
    profile_path = prepare_profile(source, root=tmp_path)
    (source.parent / 'WS24258_Stn.2.cnv').write_text('invalid header\n')
    with pytest.raises(SystemExit) as exc:
        main(['convert-cnv', str(source.parent), '--profile', str(profile_path)])
    assert exc.value.code == 1
    profile = load_dataset_profile(profile_path)
    assert 'qc_limits' not in json.loads(profile.profile_path.read_text())
    report = json.loads((profile.profile_path.parent / 'limit_report.json').read_text())
    assert report['netcdf_file_count'] == 1


def test_dataset_limits_apply_to_all_casts_and_preserve_time(tmp_path):
    from qc_runner import run_single_test
    source = _cnv(tmp_path / 'input' / 'WS24258_Stn.1.cnv')
    _cnv(tmp_path / 'input' / 'WS24258_Stn.2.cnv')
    profile = load_dataset_profile(prepare_profile(source.parent, root=tmp_path))
    convert_cnv(source.parent, profile.data_root, profile.paths.cnv_mapping)
    profile = prepare_dataset_qc(profile)
    specs = json.loads(profile.paths.sensor_specs.read_text())
    specs['sensors']['sea_water_temperature']['ranges']['degree_Celsius']['max'] = 11
    profile.paths.sensor_specs.write_text(json.dumps(specs))
    assert not (profile.profile_path.parent / 'file_overrides.json').exists()
    run_qc_for_all(profile=profile)
    for station, expected in [(1, [[1, 4]]), (2, [[1, 4]])]:
        filename = f'WS24258/WS24258_{station}.nc'
        with xr.open_dataset(profile.output.directory / filename, decode_cf=False) as result:
            np.testing.assert_array_equal(result.sea_water_temperature_qc_gross_range.values, expected)
            with xr.open_dataset(profile.data_root / filename, decode_cf=False) as original:
                np.testing.assert_array_equal(result.time.values, original.time.values)
                xr.testing.assert_identical(result.time, original.time)
                np.testing.assert_array_equal(result.sea_water_temperature.values, original.sea_water_temperature.values)
    results = run_single_test(profile.data_root / 'WS24258/WS24258_1.nc', 'gross_range_test',
                              variable='sea_water_temperature', profile=profile, print_summary=False)
    np.testing.assert_array_equal(results[0].flags, [[1, 4]])
    manifest = json.loads((profile.output.directory / 'qc_run_manifest.json').read_text())
    assert 'file_overrides' not in manifest['configuration']
    assert json.loads(profile.paths.sensor_specs.read_text())['sensors']['sea_water_temperature']['ranges']['degree_Celsius']['max'] == 11


def test_initialization_preserves_existing_setting_before_any_writes(tmp_path):
    source = _cnv(tmp_path / 'input' / 'WS2425A_Stn.1.cnv')
    profile = load_dataset_profile(prepare_profile(source, root=tmp_path))
    assert profile.profile_path.parent.name == 'WS2425A'
    assert default_mapping_path(source, root=tmp_path) == profile.paths.cnv_mapping
    assert convert_cnv(source, profile.data_root, profile.paths.cnv_mapping)['counts']['converted'] == 1
    with xr.open_dataset(profile.data_root / 'WS2425A/WS2425A_1.nc') as ds:
        assert ds.attrs['platform'] == 'RV_FG_Walton_Smith'
    existing = profile.profile_path.parent / 'spike_test/spike_thresholds.json'
    existing.parent.mkdir()
    existing.write_text('{"reviewed": true}\n')
    with pytest.raises(FileExistsError, match='existing QC setting'):
        generate_limits(profile)
    assert existing.read_text() == '{"reviewed": true}\n'
    assert not (profile.profile_path.parent / 'gross_range_test').exists()
    assert not (profile.profile_path.parent / 'file_overrides.json').exists()
    assert 'qc_limits' not in json.loads(profile.profile_path.read_text())


def test_nonactivating_write_beside_profile_is_rejected_before_writes(tmp_path):
    source = _cnv(tmp_path / 'input' / 'WS24258_Stn.1.cnv')
    profile = load_dataset_profile(prepare_profile(source, root=tmp_path))
    convert_cnv(source, profile.data_root, profile.paths.cnv_mapping)
    with pytest.raises(ValueError, match='requires activation'):
        generate_limits(profile, activate=False)
    assert not (profile.profile_path.parent / 'variable_mapping').exists()


def test_profile_defaults_are_independent_and_user_edits_survive(tmp_path):
    first_source = _cnv(tmp_path / 'input/WS24258_Stn.1.cnv')
    second_source = _cnv(tmp_path / 'input/WB24258_Stn.1.cnv')
    first_path = prepare_profile(first_source, root=tmp_path)
    second_path = prepare_profile(second_source, root=tmp_path)
    first = json.loads(first_path.read_text())
    second = json.loads(second_path.read_text())
    assert first['netcdf_global_attributes'] == second['netcdf_global_attributes']
    assert all(value is not None for value in first['netcdf_global_attributes'].values())
    first['netcdf_global_attributes']['creator_name'] = 'Reviewed dataset creator'
    first['netcdf_global_attributes']['geospatial_bounds'] = None
    first_path.write_text(json.dumps(first))
    prepare_profile(first_source, root=tmp_path)
    assert json.loads(first_path.read_text()) == first
    assert json.loads(second_path.read_text()) == second


def test_stale_inventory_cannot_hide_changed_source_description(tmp_path):
    source = _cnv(tmp_path / 'WS24258_Stn.1.cnv')
    mapping = tmp_path / 'mapping.json'
    inspect_cnv(source, mapping)
    source.write_text(source.read_text().replace('Temperature [ITS-90', 'Potential Temperature [ITS-90'))
    report = convert_cnv(source, tmp_path / 'out', mapping)
    assert report['counts']['failed'] == 1
    assert 'source description conflicts' in report['records'][0]['failure_reason']


def test_missing_source_units_are_not_filled_from_catalog(tmp_path):
    source = _cnv(tmp_path / 'WS24258_Stn.1.cnv')
    source.write_text(source.read_text().replace('Temperature [ITS-90, deg C]', 'Temperature, ITS-90'))
    mapping = tmp_path / 'mapping.json'
    inspected = inspect_cnv(source, mapping)
    assert inspected['science_variables']['t090C']['observed']['missing_units'] is True
    assert convert_cnv(source, tmp_path / 'out', mapping)['counts']['converted'] == 1
    with xr.open_dataset(tmp_path / 'out/WS24258/WS24258_1.nc', decode_cf=False) as ds:
        assert 'units' not in ds.sea_water_temperature.attrs


def test_cli_inspection_writes_lean_mapping(tmp_path):
    from main import main
    source = _cnv(tmp_path / 'input/WS24258_Stn.1.cnv')
    destination = tmp_path / 'mapping.json'
    main(['inspect-cnv', str(source), '--output', str(destination)])
    payload = json.loads(destination.read_text())
    assert payload['schema_version'] == 5
    assert all(set(item) == {'observed', 'mapped_to'} for item in payload['science_variables'].values())


def test_new_dataset_limits_use_config_template_not_live_walton(tmp_path, monkeypatch):
    import shutil
    import qc_limit_generation
    import qc_template_limits
    from dataset_profile import CONFIG_TEMPLATE_DIR

    template = tmp_path / 'template'
    shutil.copytree(CONFIG_TEMPLATE_DIR, template)
    reference = json.loads((template / 'dataset_profile.json').read_text())
    for key, value in reference['paths'].items():
        reference['paths'][key] = str(template / Path(value).relative_to('config_template'))
    (template / 'dataset_profile.json').write_text(json.dumps(reference))
    links = json.loads((template / 'gross_range_test/variable_sensor_map.json').read_text())
    spec_path = template / 'gross_range_test/sensor_specs.json'
    specs = json.loads(spec_path.read_text())
    sensor = specs['sensors'][links['sea_water_temperature']]
    for span in sensor['ranges'].values():
        span['max'] = 29
    spec_path.write_text(json.dumps(specs))
    monkeypatch.setattr(qc_limit_generation, 'CONFIG_TEMPLATE_DIR', template)
    monkeypatch.setattr(qc_limit_generation, 'TEMPLATE_PROFILE_PATH', template / 'dataset_profile.json')
    monkeypatch.setattr(qc_template_limits, 'CONFIG_TEMPLATE_DIR', template)
    source = _cnv(tmp_path / 'input/WS24258_Stn.1.cnv')
    profile = load_dataset_profile(prepare_profile(source, root=tmp_path))
    convert_cnv(source, profile.data_root, profile.paths.cnv_mapping)
    profile = prepare_dataset_qc(profile)
    copied = json.loads(profile.paths.sensor_specs.read_text())
    assert copied['sensors']['sea_water_temperature']['ranges']['degree_Celsius']['max'] == 29
    assert profile.paths.variable_mapping == profile.profile_path.parent / 'qc_variable_mapping.json'
    report = json.loads((profile.profile_path.parent / 'limit_report.json').read_text())
    assert report['reference'] == str(template / 'dataset_profile.json')
    assert all(path.is_relative_to(profile.profile_path.parent) for key in reference['paths']
               if (path := profile.paths.get(key)) is not None)
