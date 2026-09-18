"""Decode only the QC time coordinate without changing stored NetCDF data."""
import numpy as np
import xarray as xr


def decode_qc_time(time: xr.DataArray) -> xr.DataArray:
    """Respect CF units, origin, calendar, and missing values; never assume Unix time."""
    if np.issubdtype(time.dtype, np.datetime64):
        return time
    units = time.attrs.get("units")
    if not isinstance(units, str) or " since " not in units:
        raise ValueError("QC time requires CF units such as 'seconds since 2024-09-15 00:11:14'")
    calendar = time.attrs.get("calendar", "standard")
    if calendar not in {"standard", "gregorian", "proleptic_gregorian"}:
        raise ValueError(f"QC does not support calendar {calendar!r}; refusing to reinterpret dates")
    raw = xr.Dataset({"qc_time": xr.Variable(time.dims, time.values, attrs=dict(time.attrs))})
    try:
        decoded = xr.decode_cf(raw, decode_timedelta=False)["qc_time"]
    except (ValueError, OverflowError, TypeError) as exc:
        raise ValueError(f"Cannot decode QC time with units {units!r}: {exc}") from exc
    if not np.issubdtype(decoded.dtype, np.datetime64):
        raise ValueError("QC requires dates representable as numpy datetime64")
    return decoded
