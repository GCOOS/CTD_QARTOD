"""Behavior tests for the detachable 02_CNV filename-identity preparation stage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cnv_identity import (
    load_identity_config,
    load_identity_manifest,
    prepare_identity_manifest,
    write_identity_manifest,
)


PRODUCTION_CONFIG = Path(__file__).parent.parent / "config/02_cnv/filename_identity.json"
VALID_RECORD = {
    "source": "a.cnv",
    "disposition": "include",
    "cruise_id": "WS1",
    "station": "1",
    "station_token": "1",
    "variant": None,
    "embedded_filename": None,
    "warnings": [],
    "reason": None,
}


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "filename_identity.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "station_patterns": [
                    {
                        "name": "station_marker",
                        "pattern": (
                            r"(?i)(?:stn|sta)\.?(?P<station>[a-z]*\d+(?:[._]\d+)?|[a-z]+)"
                            r"(?:[_-]?(?P<variant>do_up|do|up|surface|b|v2|\(1\)))?"
                        ),
                    },
                    {
                        "name": "delimited_numeric",
                        "pattern": r"(?i)(?:^|[_-])(?P<station>\d+(?:[._-]\d+)?)$",
                    },
                    {
                        "name": "compact_numeric",
                        "pattern": r"(?i)^(?:ws|sav)\d+(?P<station>\d{1,3})$",
                    },
                    {
                        "name": "named_suffix",
                        "pattern": r"(?i)[_-](?P<station>[a-z]*\d+(?:[._]\d+)?[a-z]*|[a-z]+)$",
                    },
                ],
                "overrides": {
                    "WS9999_cnv/ambiguous.cnv": {"station": "CAL6", "variant": "legacy"}
                },
                "exclusion_patterns": [r"(?i)(?:deck|dunk|wet|test)"],
                "nonstandard_extensions": {".2cnv": "not an exact .cnv source"},
            }
        ),
        encoding="utf-8",
    )
    return path


def _add_source(root: Path, relative_path: str, text: str = "") -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_manifest_resolves_section_four_examples_and_review_dispositions(tmp_path: Path):
    source_root = tmp_path / "02_CNV"
    _add_source(source_root, "WS0603_cnv/WS0603_029_5.cnv")
    _add_source(source_root, "WS1418_cnv/ws141803.cnv")
    _add_source(source_root, "SAV18173_cnv/STA02.cnv")
    _add_source(source_root, "WS19322_cnv/StnGP5.cnv")
    _add_source(source_root, "WS22215_cnv/2022_08_Weatherbird_Smith_CTD_AMI3.cnv")
    _add_source(
        source_root,
        "WS23010_cnv/WS23011_Kelble_Stn.057.2.cnv",
        "* FileName = C:\\\\filesrv\\Data\\ctd\\WS23011_Kelble_Stn.057.2.dat\n",
    )
    _add_source(source_root, "WS22022_cnv/WS22022_Stn.10_do_up.cnv")
    _add_source(source_root, "WS21093_cnv/STN.007ws21093.cnv")
    _add_source(source_root, "WS15103_cnv/WS15103sta54b.cnv")
    _add_source(source_root, "WS0802_cnv/WS0802_020A.cnv")
    _add_source(source_root, "WS15152_cnv/WS15152decktest.cnv")
    _add_source(source_root, "WS9999_cnv/ambiguous.cnv")
    _add_source(source_root, "WS9999_cnv/mystery.cnv")
    _add_source(source_root, "WS19119_cnv/WS19119_Stn Captiva Bluehole.2cnv")

    manifest = prepare_identity_manifest(source_root, load_identity_config(_write_config(tmp_path)))
    rows = {row["source"]: row for row in manifest["records"]}

    assert rows["WS0603_cnv/WS0603_029_5.cnv"] == {
        "source": "WS0603_cnv/WS0603_029_5.cnv",
        "disposition": "include",
        "cruise_id": "WS0603",
        "station": "29.5",
        "station_token": "29_5",
        "variant": None,
        "embedded_filename": None,
        "warnings": [],
        "reason": None,
    }
    assert (rows["WS1418_cnv/ws141803.cnv"]["station"], rows["WS1418_cnv/ws141803.cnv"]["station_token"]) == ("3", "3")
    assert (rows["SAV18173_cnv/STA02.cnv"]["cruise_id"], rows["SAV18173_cnv/STA02.cnv"]["station"]) == ("SAV18173", "2")
    assert rows["WS19322_cnv/StnGP5.cnv"]["station"] == "GP5"
    assert rows["WS22215_cnv/2022_08_Weatherbird_Smith_CTD_AMI3.cnv"]["station"] == "AMI3"
    assert (rows["WS23010_cnv/WS23011_Kelble_Stn.057.2.cnv"]["cruise_id"], rows["WS23010_cnv/WS23011_Kelble_Stn.057.2.cnv"]["station"]) == ("WS23010", "57.2")
    assert "WS23011" in " ".join(rows["WS23010_cnv/WS23011_Kelble_Stn.057.2.cnv"]["warnings"])
    assert (rows["WS22022_cnv/WS22022_Stn.10_do_up.cnv"]["station"], rows["WS22022_cnv/WS22022_Stn.10_do_up.cnv"]["variant"]) == ("10", "do_up")
    assert rows["WS21093_cnv/STN.007ws21093.cnv"]["station"] == "7"
    assert (rows["WS15103_cnv/WS15103sta54b.cnv"]["station"], rows["WS15103_cnv/WS15103sta54b.cnv"]["variant"]) == ("54", "b")
    assert rows["WS0802_cnv/WS0802_020A.cnv"]["station"] == "20A"
    assert rows["WS15152_cnv/WS15152decktest.cnv"]["disposition"] == "exclude"
    assert rows["WS9999_cnv/ambiguous.cnv"]["station"] == "CAL6"
    assert rows["WS9999_cnv/mystery.cnv"]["disposition"] == "unresolved"
    assert rows["WS19119_cnv/WS19119_Stn Captiva Bluehole.2cnv"] == {
        "source": "WS19119_cnv/WS19119_Stn Captiva Bluehole.2cnv",
        "disposition": "exclude",
        "cruise_id": "WS19119",
        "station": None,
        "station_token": None,
        "variant": None,
        "embedded_filename": None,
        "warnings": [],
        "reason": "not an exact .cnv source",
    }


def test_manifest_write_is_atomic_and_loads_the_complete_reviewable_records(tmp_path: Path):
    source_root = tmp_path / "02_CNV"
    _add_source(source_root, "WS0603_cnv/WS0603_029_5.cnv")
    manifest = prepare_identity_manifest(source_root, load_identity_config(_write_config(tmp_path)))
    manifest_path = tmp_path / "identity_manifest.json"

    write_identity_manifest(manifest_path, manifest)

    assert load_identity_manifest(manifest_path) == manifest
    assert not list(tmp_path.glob(".identity_manifest.*.tmp"))


def test_production_config_resolves_variants_and_cruise_appended_names(tmp_path: Path):
    source_root = tmp_path / "02_CNV"
    expected = {
        "WS1418_cnv/ws141854b.cnv": ("54", "54", "b"),
        "WS1418_cnv/ws141857.1b.cnv": ("57.1", "57_1", "b"),
        "SAV1803_cnv/SAV1803-MRv2.cnv": ("MR", "MR", "v2"),
        "WS15103_cnv/WS15103staMRb.cnv": ("MR", "MR", "b"),
        "WS0923_cnv/WS0923-22_5b.cnv": ("22.5", "22_5", "b"),
        "WS1015_cnv/ws1015-01b.cnv": ("1", "1", "b"),
        "WS15103_cnv/WS15103sta21LK.cnv": ("21LK", "21LK", None),
        "WS16319_cnv/WS16319Stn21LKb.cnv": ("21LK", "21LK", "b"),
        "WS23203_cnv/WS23203_Stn.021LK.cnv": ("21LK", "21LK", None),
        "WS15208_cnv/WS15208sta15.5surface.cnv": ("15.5", "15_5", "surface"),
        "WS22022_cnv/WS22022_Stn.10_do_up.cnv": ("10", "10", "do_up"),
        "WS22072_cnv/WS22072_stn.006.5(1).cnv": ("6.5", "6_5", "(1)"),
        "WS22072_cnv/WS22072_stn.057.3 (1).cnv": ("57.3", "57_3", "(1)"),
        "WS21093_cnv/STN.WSws21093.cnv": ("WS", "WS", None),
        "WS21093_cnv/STN.TB1-recastws21093.cnv": ("TB1", "TB1", "recast"),
        "WS9999_cnv/StnV2.cnv": ("V2", "V2", None),
        "WS19119_cnv/WS19119_Stn_Captiva_Bluehole.cnv": (
            "CAPTIVA BLUEHOLE",
            "CAPTIVA_BLUEHOLE",
            None,
        ),
    }
    for source in expected:
        _add_source(source_root, source)

    manifest = prepare_identity_manifest(source_root, load_identity_config(PRODUCTION_CONFIG))
    rows = {row["source"]: row for row in manifest["records"]}

    assert {
        source: (rows[source]["station"], rows[source]["station_token"], rows[source]["variant"])
        for source in expected
    } == expected


def test_discovery_matches_extensions_case_insensitively(tmp_path: Path):
    source_root = tmp_path / "02_CNV"
    _add_source(source_root, "WS0603_cnv/WS0603_029_5.CNV")
    _add_source(source_root, "WS19119_cnv/WS19119_Stn Captiva Bluehole.2CNV")

    rows = prepare_identity_manifest(
        source_root, load_identity_config(PRODUCTION_CONFIG)
    )["records"]

    assert [(row["source"], row["disposition"]) for row in rows] == [
        ("WS0603_cnv/WS0603_029_5.CNV", "include"),
        ("WS19119_cnv/WS19119_Stn Captiva Bluehole.2CNV", "exclude"),
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"schema_version": 2, "records": []}, "schema_version"),
        ({"schema_version": 1, "records": {}}, "records"),
        ({"schema_version": 1, "records": [{key: value for key, value in VALID_RECORD.items() if key != "disposition"}]}, "disposition"),
        ({"schema_version": 1, "records": [{**VALID_RECORD, "source": 1}]}, "source"),
        ({"schema_version": 1, "records": [{**VALID_RECORD, "disposition": "unknown"}]}, "disposition"),
        ({"schema_version": 1, "records": [{**VALID_RECORD, "station": None}]}, "station"),
        ({"schema_version": 1, "records": [{**VALID_RECORD, "disposition": "exclude", "station": None, "station_token": None, "reason": ""}]}, "reason"),
    ],
)
def test_load_identity_manifest_rejects_malformed_contracts(
    tmp_path: Path, payload: dict[str, object], message: str
):
    manifest_path = tmp_path / "identity_manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_identity_manifest(manifest_path)


def test_manifest_write_removes_temporary_file_after_serialization_error(tmp_path: Path):
    manifest_path = tmp_path / "identity_manifest.json"

    with pytest.raises(TypeError):
        write_identity_manifest(manifest_path, {"records": {object()}})

    assert not list(tmp_path.glob(".identity_manifest.*.tmp"))
