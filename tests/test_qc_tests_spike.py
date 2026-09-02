from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS
from qc_tests import spike_test


def test_spike_test_endpoints_not_evaluated():
    x = np.array([20.0, 20.0, 20.0], dtype=float)
    f = spike_test(x, suspect_threshold=0.1, fail_threshold=1.0)
    assert f[0] == QC_FLAGS["NOT_EVALUATED"]
    assert f[-1] == QC_FLAGS["NOT_EVALUATED"]


def test_spike_test_detects_spike():
    x = np.array([20.0, 30.0, 20.0], dtype=float)
    f = spike_test(x, suspect_threshold=1.0, fail_threshold=10.0)
    assert f[1] == QC_FLAGS["SUSPECT"]
