"""Offline tests for sync_moda: the v2 record mappings from the actual t3jq-9nkf export
shape (record_id -> nyc_goid verbatim, organization_type -> classification, current url ->
web_properties, acronym + alternate/former names & acronyms -> short_name/other_names[],
operational_status active-only gate). No fetch."""
import json
import pathlib

import pytest

from sync_moda import _is_active, _split_multi, to_record

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))

# Mirrors the actual export columns (verified 2026-07-15; see sync_moda docstring).
# record_id carries the NYC_GOID_XXXXXX form; multi-value cells are ;-delimited.
ROW = {
    "record_id": "NYC_GOID_000382",
    "operational_status": "Active",
    "organization_type": "Mayoral Office",
    "name": "Office of Technology and Innovation",
    "acronym": "OTI",
    "url": "https://www.nyc.gov/content/oti/pages/",
    "alternate_or_former_names": "Department of Information Technology and Telecommunications",
    "alternate_or_former_acronyms": "DoITT",
}


def test_record_id_kept_verbatim_as_nyc_goid_identifier():
    # The NYC_GOID_ prefix must NOT be stripped or normalized — stored verbatim as a string.
    rec = to_record(ROW)
    assert {"scheme": "nyc_goid", "identifier": "NYC_GOID_000382"} in rec["identifiers"]
    assert "crosswalk" not in rec, "v1 crosswalk block must be gone"


def test_org_type_becomes_classification():
    assert to_record(ROW)["classification"] == "Mayoral Office"


def test_url_becomes_web_property_host():
    wp = to_record(ROW)["web_properties"]
    assert wp and wp[0]["domain"] == "www.nyc.gov" and wp[0]["role"] == "primary"


def test_current_acronym_becomes_short_name_and_other_name():
    rec = to_record(ROW)
    assert rec["short_name"] == "OTI"
    acr = [o for o in rec["other_names"] if o["name"] == "OTI"]
    assert acr and "acronym" in acr[0]["note"], "current acronym must appear in other_names, noted as an acronym"


def test_former_names_and_acronyms_become_distinguished_other_names():
    rec = to_record(ROW)
    by_name = {o["name"]: o["note"] for o in rec["other_names"]}
    assert "alternate or former name" in by_name["Department of Information Technology and Telecommunications"]
    assert "alternate or former acronym" in by_name["DoITT"]


def test_multivalue_alt_fields_split_on_semicolon():
    # e.g. "Department of Sanitation;NYC Sanitation" and "Law;Law Dept." in the real export.
    assert _split_multi("Department of Sanitation;NYC Sanitation") == ["Department of Sanitation", "NYC Sanitation"]
    assert _split_multi("Law;Law Dept.") == ["Law", "Law Dept."]
    assert _split_multi("") == []
    assert _split_multi(None) == []
    rec = to_record({**ROW, "alternate_or_former_names": "Department of Sanitation;NYC Sanitation"})
    names = {o["name"] for o in rec["other_names"]}
    assert {"Department of Sanitation", "NYC Sanitation"} <= names


def test_active_only_gate():
    assert _is_active({"operational_status": "Active"}) is True
    assert _is_active({"operational_status": "active"}) is True  # case-insensitive
    assert _is_active({"operational_status": "Inactive"}) is False
    assert _is_active({"operational_status": ""}) is False
    assert _is_active({}) is False


def test_blank_record_id_yields_no_identifier():
    rec = to_record({"record_id": "", "name": "X", "url": "", "organization_type": "",
                     "acronym": "", "operational_status": "Active"})
    assert rec["identifiers"] == []
    assert rec["classification"] is None
    assert rec["short_name"] is None
    assert rec["other_names"] == []


def test_emitted_record_validates_once_id_assigned():
    jsonschema = pytest.importorskip("jsonschema")
    rec = {**to_record(ROW), "id": "oti"}  # build_registry assigns id during merge
    jsonschema.validate(rec, SCHEMA)
