"""Cast-level header positions must not be expanded into fabricated samples."""
import json
import re

import numpy as np
import pytest
import xarray as xr
from lxml import etree

from cnv_coordinates import resolve_header_coordinates
from cnv_converter import convert_cnv
from cnv_mapping import inspect_cnv, read_cnv_header
from cnv_workflow import prepare_profile
from dataset_profile import load_dataset_profile
from qc_limit_generation import prepare_dataset_qc
from qc_runner import run_qc_for_all
from xml_generator.generate import generate_erddap_xml_for_profile
from test_cnv_converter import _cnv


def _header_cnv(path, latitude='24 42.75 N', longitude='080 49.95 W'):
    path = _cnv(path)
    header, rows = path.read_text().split('*END*\n')
    lines = []
    for line in header.splitlines():
        match = re.match(r'# name (\d+) = ', line)
        if match:
            index = int(match[1])
            if index in (1, 2):
                continue
            line = line.replace(f'# name {index} = ', f'# name {index if index == 0 else index - 2} = ')
        lines.append(line.replace('# nquan = 7', '# nquan = 5'))
    if latitude is not None:
        lines.append(f'* NMEA Latitude = {latitude}')
    if longitude is not None:
        lines.append(f'* NMEA Longitude = {longitude}')
    data = []
    for row in rows.splitlines():
        values = row.split()
        values[0] = str(float(values[0]) + 5)  # retain a nonzero elapsed-time start
        data.append(' '.join([values[0], *values[3:]]))
    path.write_text('\n'.join([*lines, '*END*', *data]) + '\n')
    return path


@pytest.mark.parametrize('lat,lon,expected', [
    ('24 42.75 N', '080 49.95 W', (24.7125, -80.8325)),
    ('24 42.75 S', '080 49.95 E', (-24.7125, 80.8325)),
    ('90 0 N', '180 0 W', (90, -180)),
    ('00 00.0 s', '000 00.0 e', (0, 0)),
])
def test_nmea_degrees_minutes_and_hemispheres(lat, lon, expected):
    result = resolve_header_coordinates(['timeS', 'depSM'], [
        f'* NMEA Latitude = {lat}', f'* NMEA Longitude = {lon}'])
    assert (result['latitude'], result['longitude']) == pytest.approx(expected)


@pytest.mark.parametrize('lat,lon', [
    (None, '080 49.95 W'), ('24 42.75 N', None),
    ('24 60 N', '080 49.95 W'), ('90 0.1 N', '080 49.95 W'),
    ('24 42.75 N', '181 0 W'), ('24 42.75 E', '080 49.95 W'),
    ('-24 42.75 N', '080 49.95 W'), ('NaN', '080 49.95 W'),
])
def test_invalid_header_coordinates_report_issue_and_block_conversion(tmp_path, lat, lon):
    source = _header_cnv(tmp_path / 'SAV1803-12.cnv', lat, lon)
    mapping = tmp_path / 'mapping.json'
    payload = inspect_cnv(source, mapping)
    assert len(payload['inspection']['conversion_issues']) == 1
    assert not payload['inspection']['coordinate_sources']
    with pytest.raises(ValueError, match='NMEA'):
        read_cnv_header(source)
    report = convert_cnv(source, tmp_path / 'out', mapping)
    assert report['counts']['failed'] == 1
    assert not list((tmp_path / 'out').rglob('*.nc'))


def test_ambiguous_headers_or_partial_columns_are_rejected():
    headers = ['* NMEA Latitude = 24 42.75 N', '* NMEA Longitude = 080 49.95 W']
    with pytest.raises(ValueError, match='exactly once'):
        resolve_header_coordinates([], [*headers, headers[0]])
    for columns in [['latitude'], ['longitude'], ['latitude', 'latitude', 'longitude']]:
        with pytest.raises(ValueError, match='both occur exactly once'):
            resolve_header_coordinates(columns, headers)


def test_header_coordinates_convert_qc_and_xml_without_human_input(tmp_path):
    source = _header_cnv(tmp_path / 'input' / 'SAV1803-12.cnv')
    profile = load_dataset_profile(prepare_profile(source, root=tmp_path))
    inventory = json.loads(profile.paths.cnv_mapping.read_text())['inspection']
    assert inventory['coordinate_sources'] == {'nmea_header': 1}
    assert not inventory['conversion_issues']
    report = convert_cnv(source, profile.data_root, profile.paths.cnv_mapping,
                         netcdf_global_attributes=profile.netcdf_global_attributes,
                         netcdf_fixed_global_attributes=profile.netcdf_fixed_global_attributes,
                         netcdf_derived_global_attributes=profile.netcdf_derived_global_attributes)
    assert report['counts']['converted'] == 1
    assert report['records'][0]['coordinate_source'] == 'nmea_header'
    profile = prepare_dataset_qc(profile)
    run_qc_for_all(profile=profile)
    relative = 'SAV1803/SAV1803_12.nc'
    with xr.open_dataset(profile.data_root / relative, decode_cf=False) as raw, xr.open_dataset(profile.output.directory / relative, decode_cf=False) as qc:
        for name, value, units in [('latitude', 24.7125, 'degrees_north'), ('longitude', -80.8325, 'degrees_east')]:
            assert raw[name].dims == ('profile',)
            assert raw[name].shape == (1,)
            assert raw[name].item() == pytest.approx(value)
            assert raw[name].attrs['units'] == units
            assert raw[name].attrs['standard_name'] == name
            assert raw[name].attrs['source_header'] == f'NMEA {name.title()}'
            xr.testing.assert_identical(raw[name], qc[name])
        assert raw.time.dims == raw.depth.dims == raw.sea_water_temperature.dims == ('profile', 'z')
        np.testing.assert_array_equal(raw.time.values, [[5, 6]])
        np.testing.assert_array_equal(raw.sea_water_temperature.values, [[10, 12]])
        np.testing.assert_array_equal(raw.sea_water_temperature_2.values, [[11, 13]])
        assert raw.attrs['geospatial_lat_min'] == raw.attrs['geospatial_lat_max'] == pytest.approx(24.7125)
        assert raw.attrs['geospatial_lon_min'] == raw.attrs['geospatial_lon_max'] == pytest.approx(-80.8325)
        assert raw.attrs['time_coverage_start'] == '2024-09-18T12:21:17Z'
        assert qc.sea_water_temperature_qc_location.shape == (1, 2)
        np.testing.assert_array_equal(qc.sea_water_temperature_qc_location.values, [[1, 1]])
    generate_erddap_xml_for_profile(profile)
    xml = etree.parse(str(profile.erddap.output_xml))
    assert xml.xpath('//dataVariable[sourceName="latitude"]')
    assert xml.xpath('//dataVariable[sourceName="longitude"]')


def test_mixed_scope_preserves_sample_coordinates_and_uses_headers_per_cast(tmp_path):
    root = tmp_path / 'input'
    _header_cnv(root / 'SAV1803-12.cnv')
    sample = _cnv(root / 'WS24258_Stn.1.cnv')
    # Even differing header coordinates do not replace available measured columns.
    sample.write_text(sample.read_text().replace('*END*', '* NMEA Latitude = 0 0 N\n* NMEA Longitude = 0 0 E\n*END*'))
    mapping = tmp_path / 'mapping.json'
    payload = inspect_cnv(root, mapping)
    assert payload['inspection']['coordinate_sources'] == {'data_columns': 1, 'nmea_header': 1}
    report = convert_cnv(root, tmp_path / 'out', mapping)
    assert report['counts']['converted'] == 2
    with xr.open_dataset(tmp_path / 'out/WS24258/WS24258_1.nc', decode_cf=False) as ds:
        assert ds.latitude.dims == ds.longitude.dims == ('profile', 'z')
        np.testing.assert_array_equal(ds.latitude.values, [[27.1, 27.2]])
        np.testing.assert_array_equal(ds.longitude.values, [[-82.1, -82.2]])
        assert 'source_header' not in ds.latitude.attrs


def test_invalid_sample_coordinate_is_not_replaced_by_valid_header(tmp_path):
    source = _cnv(tmp_path / 'WS24258_Stn.1.cnv')
    source.write_text(source.read_text().replace('27.1', '99.0').replace('*END*', '* NMEA Latitude = 0 0 N\n* NMEA Longitude = 0 0 E\n*END*'))
    mapping = tmp_path / 'mapping.json'
    inspect_cnv(source, mapping)
    report = convert_cnv(source, tmp_path / 'out', mapping)
    assert report['counts']['failed'] == 1
    assert 'latitude or longitude samples are invalid' in report['records'][0]['failure_reason']
