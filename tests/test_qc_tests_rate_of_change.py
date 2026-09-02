from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS
from qc_tests import rate_of_change_test


def test_rate_of_change_invalid_threshold():
    x = np.array([1.0, 2.0])
    assert np.all(rate_of_change_test(x, threshold=0) == QC_FLAGS["NOT_EVALUATED"])
    assert np.all(rate_of_change_test(x, threshold=-1) == QC_FLAGS["NOT_EVALUATED"])


def test_rate_of_change_first_index_not_evaluated():
    x = np.array([1.0, 2.0, 3.0])
    f = rate_of_change_test(x, threshold=0.5)
    assert f[0] == QC_FLAGS["NOT_EVALUATED"]


def test_rate_of_change_suspect_large_step():
    x = np.array([0.0, 10.0])
    f = rate_of_change_test(x, threshold=1.0)
    assert f[1] == QC_FLAGS["SUSPECT"]


def test_rate_of_change_2d_last_axis():
    x = np.array([[0.0, 1.0, 100.0], [0.0, 0.5, 0.6]], dtype=float)
    f = rate_of_change_test(x, threshold=5.0)
    assert f.shape == x.shape
    assert f[0, 0] == QC_FLAGS["NOT_EVALUATED"]
    assert f[0, 2] == QC_FLAGS["SUSPECT"]
