from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS
from qc_result_viz import _flag_summary


def test_flag_summary_counts():
    flags = np.array([QC_FLAGS["PASS"], QC_FLAGS["PASS"], QC_FLAGS["SUSPECT"]])
    s = _flag_summary(flags)
    assert s["PASS"] == 2
    assert s["SUSPECT"] == 1
