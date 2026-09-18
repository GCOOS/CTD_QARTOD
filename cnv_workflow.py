"""Default paths and first-run configuration for single files or cruise collections."""
import json
from pathlib import Path
import re

from cnv_converter import filename_identity
from cnv_mapping import _atomic_json, discover_cnv_files, inspect_cnv
from dataset_profile import REPO_ROOT, TEMPLATE_PROFILE_PATH


def dataset_scope(input_path):
    source = Path(input_path)
    cruises = set()
    for path in discover_cnv_files(source):
        try:
            cruises.add(filename_identity(path)[0])
        except ValueError:
            pass
    if len(cruises) == 1:
        return next(iter(cruises))
    name = source.name if source.is_dir() else source.parent.name
    name = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    if not name:
        raise ValueError("input has no usable dataset name; supply an explicit profile")
    return name


def default_mapping_path(input_path, root=REPO_ROOT):
    return Path(root) / "config" / dataset_scope(input_path).lower() / "cnv_mapping.json"


def prepare_profile(input_path, *, root=REPO_ROOT):
    """Create missing files only. Existing review decisions are never overwritten."""
    root = Path(root)
    scope = dataset_scope(input_path)
    config = root / "config" / scope.lower()
    profile_path = config / "dataset_profile.json"
    if profile_path.exists():
        return profile_path
    mapping_path = config / "cnv_mapping.json"
    if not mapping_path.exists():
        inspect_cnv(input_path, mapping_path)
    profile = json.loads(TEMPLATE_PROFILE_PATH.read_text())
    profile.update({
        "data_root": str(root / "output" / f"{scope}_CNV"),
        "output": {"mode": "duplicate", "directory": str(root / "output" / f"{scope}_QC")},
        "paths": {"cnv_mapping": str(mapping_path)},
    })
    profile["erddap"].update({"output_xml": str(root / "output" / "erddap" / f"{scope.lower()}_datasets.xml"),
                              "filedir_prefix": f"/data/erddap/{scope}_QC"})
    _atomic_json(profile_path, profile)
    return profile_path
