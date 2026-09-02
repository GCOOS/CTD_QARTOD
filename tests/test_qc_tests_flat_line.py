from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS
from qc_tests import flat_line_test


def test_flat_line_missing_nan():
    x = np.array([1.0, np.nan, 3.0])
    f = flat_line_test(x, rep_cnt_suspect=2, rep_cnt_fail=3, eps=0.01)
    assert f[1] == QC_FLAGS["MISSING"]


def test_flat_line_suspect_repeated():
    x = np.array([5.0, 5.0, 5.0, 5.0, 1.0])
    f = flat_line_test(x, rep_cnt_suspect=2, rep_cnt_fail=3, eps=0.01)
    assert f[2] == QC_FLAGS["SUSPECT"] or f[3] == QC_FLAGS["SUSPECT"]


def test_flat_line_eps_zero_flags_exact_repeats():
    x = np.array([5.0, 5.0, 5.0, 5.0, 1.0])
    f = flat_line_test(x, rep_cnt_suspect=2, rep_cnt_fail=3, eps=0.0)
    assert f[2] == QC_FLAGS["SUSPECT"]
    assert f[3] == QC_FLAGS["FAIL"]


def test_flat_line_eps_zero_allows_near_repeats():
    x = np.array([5.0, 5.0001, 5.0, 5.0001])
    f = flat_line_test(x, rep_cnt_suspect=2, rep_cnt_fail=3, eps=0.0)
    assert np.all(f == QC_FLAGS["PASS"])
