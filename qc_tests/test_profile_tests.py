import numpy as np

from qc_config import QC_FLAGS
from qc_tests.rate_of_change_test import rate_of_change_test
from qc_tests.spike_test import spike_test


def test_spike_and_rate_of_change_are_enabled() -> None:
    spike_flags = spike_test(np.array([0.0, 0.0, 1.0, 0.0, 0.0]), 0.1, 0.5)
    assert spike_flags.tolist() == [2, 3, 4, 3, 2]

    roc_flags = rate_of_change_test(np.array([1.0, 1.2, 1.7, np.nan]), 0.4)
    assert roc_flags.tolist() == [
        QC_FLAGS["NOT_EVALUATED"],
        QC_FLAGS["PASS"],
        QC_FLAGS["SUSPECT"],
        QC_FLAGS["MISSING"],
    ]
