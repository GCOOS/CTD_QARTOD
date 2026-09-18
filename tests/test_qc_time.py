"""Elapsed time must be decoded using its own epoch before seasonal QC."""
import numpy as np
import pytest
import xarray as xr

from qc_runner import _get_time_for_var
from qc_tests import climatology_test
from qc_time import decode_qc_time


@pytest.mark.parametrize('units,values,expected', [
    ('seconds since 2024-09-15 00:11:14+00:00', [0, 0.042],
     ['2024-09-15T00:11:14', '2024-09-15T00:11:14.042']),
    ('days since 2024-09-01', [0, 14], ['2024-09-01', '2024-09-15']),
    ('hours since 2024-09-15 02:00:00+02:00', [0, 1],
     ['2024-09-15T00:00:00', '2024-09-15T01:00:00']),
    ('seconds since 1970-01-01', [0, 1726358400], ['1970-01-01', '2024-09-15']),
])
def test_cf_units_origin_and_fractional_seconds(units, values, expected):
    raw = xr.DataArray(values, dims='z', attrs={'units': units, 'calendar': 'gregorian'})
    original = raw.copy(deep=True)
    decoded = decode_qc_time(raw)
    np.testing.assert_array_equal(decoded.values, np.array(expected, dtype='datetime64[ns]'))
    xr.testing.assert_identical(raw, original)


def test_time_broadcasts_by_dimension_name():
    ds = xr.Dataset({
        'time': ('profile', [0, 86400], {'units': 'seconds since 2024-09-15'}),
        'sea_water_temperature': (('z', 'profile'), np.ones((3, 2))),
    })
    times = _get_time_for_var(ds, ds.sea_water_temperature)
    assert times.shape == (3, 2)
    np.testing.assert_array_equal(times[2], np.array(['2024-09-15', '2024-09-16'], dtype='datetime64[ns]'))


def test_missing_and_unsupported_time_metadata_are_not_guessed():
    with pytest.raises(ValueError, match='CF units'):
        decode_qc_time(xr.DataArray([0.0]))
    with pytest.raises(ValueError, match='calendar'):
        decode_qc_time(xr.DataArray([0.0], attrs={'units': 'days since 2024-09-01', 'calendar': '360_day'}))
    with pytest.raises(ValueError, match='decoded dates'):
        climatology_test(np.array([32.0]), np.array([0.0]), np.array([3.0]), [])


def test_september_cast_uses_september_limits_and_masks_missing_time():
    rules = [
        {'period': 'month', 'tspan': [1, 2], 'zspan': [0, 10], 'vspan': [13, 28.5]},
        {'period': 'month', 'tspan': [9, 10], 'zspan': [0, 10], 'vspan': [22, 34]},
    ]
    values = np.array([32.0, 32.0, np.nan])
    time = xr.DataArray([0.0, -9999.0, 1.0], dims='z',
                        attrs={'units': 'seconds since 2024-09-15', '_FillValue': -9999.0})
    flags = climatology_test(values, time, np.full(3, 3.0), rules)
    np.testing.assert_array_equal(flags, [1, 2, 9])
    january = np.array(['1970-01-01'], dtype='datetime64[ns]')
    np.testing.assert_array_equal(climatology_test([32.0], january, [3.0], rules), [3])
