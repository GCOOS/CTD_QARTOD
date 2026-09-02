"""qc_tests.flags"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma

from qc_config import QC_FLAGS
from qc_tests.flags import to_int_flags


def test_to_int_flags_fills_masked_with_missing():
    arr = ma.masked_array([1.0, 2.0], mask=[0, 1])
    out = to_int_flags(arr)
    assert out[0] == 1
    assert out[1] == QC_FLAGS["MISSING"]
