from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS
from qc_tests import gap_test, syntax_test


def test_gap_test_all_not_evaluated():
    x = np.array([1.0, 2.0, np.nan])
    f = gap_test(x)
    assert np.all(f == QC_FLAGS["NOT_EVALUATED"])


def test_syntax_test_all_not_evaluated():
    f = syntax_test(np.zeros((2, 3)))
    assert f.shape == (2, 3)
    assert np.all(f == QC_FLAGS["NOT_EVALUATED"])
