"""Offline test: sync_moda.to_record emits a valid v2 record (record_id -> nyc_goid
identifier, organization_type -> classification, current url -> web_properties). No fetch."""
import json
import pathlib

import pytest

from sync_moda import to_record

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))

# Mirrors MODA Phase II published-schema columns (verified 2026-07-11; see sync_moda docstring).
ROW = {
    "record_id": "100200",
    "name": "Office of Technology and Innovation",
    "url": "https://www.nyc.gov/oti",
    "organization_type": "Mayoral Agency",
}


def test_record_id_becomes_nyc_goid_identifier():
    rec = to_record(ROW)
    assert {"scheme": "nyc_goid", "identifier": "100200"} in rec["identifiers"]
    assert "crosswalk" not in rec, "v1 crosswalk block must be gone"


def test_org_type_becomes_classification():
    assert to_record(ROW)["classification"] == "Mayoral Agency"


def test_url_becomes_web_property_host():
    wp = to_record(ROW)["web_properties"]
    assert wp and wp[0]["domain"] == "www.nyc.gov" and wp[0]["role"] == "primary"


def test_blank_record_id_yields_no_identifier():
    rec = to_record({"record_id": "", "name": "X", "url": "", "organization_type": ""})
    assert rec["identifiers"] == []
    assert rec["classification"] is None


def test_emitted_record_validates_once_id_assigned():
    jsonschema = pytest.importorskip("jsonschema")
    rec = {**to_record(ROW), "id": "oti"}  # build_registry assigns id during merge
    jsonschema.validate(rec, SCHEMA)
