"""Default paths and first-run configuration for single files or cruise collections."""
import json
from pathlib import Path
import re
import shutil

from cnv_mapping import _atomic_json, discover_cnv_files, inspect_cnv
from dataset_profile import CONFIG_LAYOUT, REPO_ROOT, load_dataset_profile, reference_file
from station_identity import IdentityError, load_station_identity, resolve_identity

COPIED_SETTINGS = ("station_identity", "soak_config")


def dataset_scope(input_path, identity):
    source = Path(input_path)
    cruises = set()
    for path in discover_cnv_files(source):
        try:
            cruises.add(resolve_identity(path, identity).cruise_id)
        except IdentityError:
            pass
    if len(cruises) == 1:
        return next(iter(cruises))
    name = source.name if source.is_dir() else source.parent.name
    name = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    if not name:
        raise ValueError("input has no usable dataset name; supply an explicit profile")
    return name


def _preset_scope(input_path, preset):
    return dataset_scope(input_path, load_station_identity(reference_file(CONFIG_LAYOUT["station_identity"], preset)))


def default_mapping_path(input_path, *, preset=None, root=REPO_ROOT):
    return Path(root) / "config" / _preset_scope(input_path, preset) / "cnv_mapping.json"


def prepare_profile(input_path, *, preset=None, root=REPO_ROOT, profile_path=None):
    """Create missing files only. Existing review decisions are never overwritten."""
    root = Path(root)
    scope = _preset_scope(input_path, preset)
    if profile_path is None:
        profile_path = root / "config" / scope / "dataset_profile.json"
    else:
        profile_path = Path(profile_path).expanduser()
        if not profile_path.is_absolute():
            profile_path = root / profile_path
    config = profile_path.parent
    if profile_path.exists():
        try:
            load_dataset_profile(profile_path)
        except (OSError, ValueError, json.JSONDecodeError):
            profile_path.unlink()
        else:
            return profile_path
    paths = {}
    for key in COPIED_SETTINGS:
        target = config / CONFIG_LAYOUT[key]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(reference_file(CONFIG_LAYOUT[key], preset), target)
        paths[key] = str(target)
    mapping_path = config / "cnv_mapping.json"
    if not mapping_path.exists():
        inspect_cnv(input_path, mapping_path)
    profile = json.loads(reference_file("dataset_profile.json", preset).read_text(encoding="utf-8"))
    profile.update({
        "data_root": str(root / "output" / f"{scope}_CNV"),
        "preset": preset,
        "output": {"mode": "duplicate", "directory": str(root / "output" / f"{scope}_QC")},
        "paths": {"cnv_mapping": str(mapping_path), **paths},
    })
    profile["erddap"].update({"output_xml": str(root / "output" / "erddap" / f"{scope.lower()}_datasets.xml"),
                              "filedir_prefix": f"/data/erddap/{scope}_QC"})
    _atomic_json(profile_path, profile)
    return profile_path
