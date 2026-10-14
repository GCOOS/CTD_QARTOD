"""SFER preset helpers for conversion tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cnv_converter import convert_cnv
from cnv_metadata import PublicationSettings
from cnv_workflow import prepare_profile
from dataset_profile import PRESETS_DIR, REPO_ROOT, load_dataset_profile
from station_identity import load_station_identity, resolve_identity

SFER_PRESET = "sfer_aoml"
SFER_IDENTITY_PATH = PRESETS_DIR / SFER_PRESET / "station_identity.json"
SFER_PROFILE_PATH = PRESETS_DIR / SFER_PRESET / "dataset_profile.json"

SFER_IDENTITY = load_station_identity(SFER_IDENTITY_PATH)
SFER_PUBLICATION = load_dataset_profile(SFER_PROFILE_PATH).publication


def filename_identity(path: Path | str) -> tuple[str, str]:
    identity = resolve_identity(path, SFER_IDENTITY)
    return identity.cruise_id, identity.station_id


def convert_sfer(*args, **kwargs):
    kwargs.setdefault("identity", SFER_IDENTITY)
    kwargs.setdefault("publication", SFER_PUBLICATION)
    return convert_cnv(*args, **kwargs)


def convert_with_profile(source, output, mapping, profile, **kwargs):
    identity = load_station_identity(profile.paths.station_identity)
    return convert_cnv(
        source,
        output,
        mapping,
        identity=identity,
        publication=profile.publication,
        **kwargs,
    )


def prepare_sfer_profile(input_path, **kwargs):
    kwargs.setdefault("preset", SFER_PRESET)
    kwargs.setdefault("root", REPO_ROOT)
    return prepare_profile(input_path, **kwargs)


def publication_with(**changes) -> PublicationSettings:
    return replace(SFER_PUBLICATION, **changes)
