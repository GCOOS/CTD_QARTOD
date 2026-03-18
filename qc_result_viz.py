"""
Utilities for QC test results and visualization.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import matplotlib.pyplot as plt
import numpy as np

from qc_config import QC_FLAGS

_FLAG_TO_COLOR = {
    QC_FLAGS["PASS"]: "tab:green",
    QC_FLAGS["NOT_EVALUATED"]: "lightgray",
    QC_FLAGS["SUSPECT"]: "tab:orange",
    QC_FLAGS["FAIL"]: "tab:red",
    QC_FLAGS["MISSING"]: "tab:blue",
}


def _flag_summary(flags: np.ndarray) -> Dict[str, int]:
    """
    Count occurrences of each QC flag value.
    """
    counts: Dict[str, int] = {name: 0 for name in QC_FLAGS}
    unique, freq = np.unique(flags, return_counts=True)
    inv_map = {v: k for k, v in QC_FLAGS.items()}
    for val, c in zip(unique, freq, strict=False):
        name = inv_map.get(int(val), f"UNKNOWN_{val}")
        counts[name] = int(c)
    return counts


@dataclass
class QCTestResult:
    """
    Container for a single QC test result.
    """

    test_name: str
    variable_name: str
    file_path: str
    data: np.ndarray
    flags: np.ndarray
    depth: Optional[np.ndarray]
    time: Optional[np.ndarray]
    flag_summary: Dict[str, int]

    def print_summary(self) -> None:
        """
        Print a concise summary of the QC result.
        """
        total = int(self.flags.size)
        summary_parts = [f"{k}:{v}" for k, v in self.flag_summary.items() if v > 0]
        summary_str = ", ".join(summary_parts) if summary_parts else "no flags recorded"
        print(
            f"[QC] file={Path(self.file_path).name} var={self.variable_name} "
            f"test={self.test_name} total={total} flags -> {summary_str}"
        )

    def plot_profile(
        self, save_path: str | Path | None = None, show: bool = True
    ) -> plt.Figure:
        """
        Combined visualization with 3 subplots:
          Top:    Twin Y-axes plot (depth left, variable right) vs index
          Bottom: Side-by-side Depth vs Index and Variable vs Index
        All colored by QC flag.
        """
        arr = np.asarray(self.data).ravel()
        flags = np.asarray(self.flags).ravel()
        n = arr.size

        # Depth array
        if self.depth is not None:
            depth_arr = np.asarray(self.depth).ravel()
        else:
            depth_arr = np.arange(n, dtype=float)

        # Index array
        idx = np.arange(n)

        inv_map = {v: k for k, v in QC_FLAGS.items()}

        # Create figure with GridSpec: top row spans full width, bottom row has 2 columns
        fig = plt.figure(figsize=(14, 10))
        gs = fig.add_gridspec(2, 2, height_ratios=[1, 1], hspace=0.3, wspace=0.25)

        # --- Top panel: Twin Y-axes (depth left, variable right) ---
        ax_top = fig.add_subplot(gs[0, :])
        ax_top_twin = ax_top.twinx()

        ax_top.set_xlabel("index")
        ax_top.set_ylabel("depth" if self.depth is not None else "index value", color="tab:blue")
        ax_top_twin.set_ylabel(self.variable_name, color="tab:red")

        if self.depth is not None:
            ax_top.invert_yaxis()  # Surface (smaller depth) on top

        for flag_val in np.unique(flags):
            mask = flags == flag_val
            color = _FLAG_TO_COLOR.get(int(flag_val), "k")
            label = inv_map.get(int(flag_val), str(flag_val))
            # Depth on left axis (circles)
            ax_top.scatter(idx[mask], depth_arr[mask], s=20, color=color, label=label, alpha=0.8, marker="o")
            # Variable on right axis (squares)
            ax_top_twin.scatter(idx[mask], arr[mask], s=20, color=color, alpha=0.8, marker="s")

        ax_top.set_title("Depth & Variable vs Index (Twin Axes)")
        ax_top.grid(True, linestyle="--", alpha=0.4)

        # --- Bottom-left panel: Depth vs Index ---
        ax_bl = fig.add_subplot(gs[1, 0])
        for flag_val in np.unique(flags):
            mask = flags == flag_val
            color = _FLAG_TO_COLOR.get(int(flag_val), "k")
            label = inv_map.get(int(flag_val), str(flag_val))
            ax_bl.scatter(idx[mask], depth_arr[mask], s=18, color=color, label=label, alpha=0.8)

        ax_bl.set_xlabel("index")
        ax_bl.set_ylabel("depth" if self.depth is not None else "index value")
        if self.depth is not None:
            ax_bl.invert_yaxis()
        ax_bl.set_title("Depth vs Index")
        ax_bl.grid(True, linestyle="--", alpha=0.4)

        # --- Bottom-right panel: Variable vs Index ---
        ax_br = fig.add_subplot(gs[1, 1])
        for flag_val in np.unique(flags):
            mask = flags == flag_val
            color = _FLAG_TO_COLOR.get(int(flag_val), "k")
            label = inv_map.get(int(flag_val), str(flag_val))
            ax_br.scatter(idx[mask], arr[mask], s=18, color=color, label=label, alpha=0.8)

        ax_br.set_xlabel("index")
        ax_br.set_ylabel(self.variable_name)
        ax_br.set_title(f"{self.variable_name} vs Index")
        ax_br.grid(True, linestyle="--", alpha=0.4)

        # Shared legend
        handles, labels = ax_top.get_legend_handles_labels()
        fig.legend(handles, labels, title="QC flags", loc="center right", bbox_to_anchor=(1.02, 0.5))

        fig.suptitle(f"{self.variable_name} - {self.test_name}", fontsize=14, y=0.98)

        if save_path:
            fig.savefig(Path(save_path), dpi=150, bbox_inches="tight")
        if show:
            plt.show()
        return fig
