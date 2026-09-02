from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS
from qc_tests import gross_range_test


def test_gross_range_test_pass_inside_span():
    x = np.array([20.0, 22.0])
    f = gross_range_test(x, fail_span=(0.0, 40.0), suspect_span=(10.0, 30.0))
    assert f.shape == x.shape
    assert f[0] == QC_FLAGS["PASS"]


def test_gross_range_test_fail_outside():
    x = np.array([50.0])
    f = gross_range_test(x, fail_span=(0.0, 40.0))
    assert f[0] == QC_FLAGS["FAIL"]
