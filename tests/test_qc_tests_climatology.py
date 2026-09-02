from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS
from qc_tests import climatology_test


def test_climatology_test_none_config_returns_not_evaluated():
    x = np.array([20.0])
    t = np.array(["2024-06-15"], dtype="datetime64[ns]")
    z = np.array([100.0])
    f = climatology_test(x, time=t, depth=z, config=None)
    assert np.all(f == QC_FLAGS["NOT_EVALUATED"])


def test_climatology_test_dict_rules():
    """Single monthly bin covering June; value inside vspan → PASS."""
    x = np.array([22.0])
    t = np.array(["2024-06-15"], dtype="datetime64[ns]")
    z = np.array([50.0])
    cfg = [{"tspan": (1, 12), "vspan": (15.0, 35.0), "period": "month"}]
    f = climatology_test(x, time=t, depth=z, config=cfg)
    assert f[0] == QC_FLAGS["PASS"]
