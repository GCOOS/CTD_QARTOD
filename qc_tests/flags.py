"""Shared helpers for converting ioos_qc masked flag arrays to plain int QC flags."""

from __future__ import annotations

import numpy as np

from qc_config import QC_FLAGS


def to_int_flags(masked: np.ma.MaskedArray) -> np.ndarray:
    """Convert a masked array of flags to a plain numpy int array; masked → MISSING."""
    return np.ma.filled(masked, QC_FLAGS["MISSING"]).astype(int)
