"""Offline tests: the v1 -> v2 seed migration is lossless and the output validates.

Losslessness bar (issue #1 testing decisions): every v1 crosswalk value, boundary
reference, coverage/boundary note, EO linkage, succession link, and the old betanyc_id
must be present in v2 form. The v1 snapshot lives in tests/fixtures/registry.seed.v1.json.
"""
import json
import pathlib

import pytest

from migrate_seed import build_id_map, migrate_seed

ROOT = pathlib.Path(__file__).resolve().parent.parent
V1 = json.loads((ROOT / "tests" / "fixtures" / "registry.seed.v1.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))
V1_ENTITIES = V1["entities"]
MIGRATED = migrate_seed(V1)["entities"]
ID_MAP = build_id_map(V1_ENTITIES)
BY_NEW_ID = {e["id"]: e for e in MIGRATED}


def _v2_for(v1_entity: dict) -> dict:
    return BY_NEW_ID[ID_MAP[v1_entity["betanyc_id"]]]


def test_migration_validates():
    jsonschema = pytest.importorskip("jsonschema")
    for e in MIGRATED:
        jsonschema.validate(e, SCHEMA)


def test_same_record_count():
    assert len(MIGRATED) == len(V1_ENTITIES)


def test_old_betanyc_id_preserved_as_legacy_identifier():
    for v1 in V1_ENTITIES:
        v2 = _v2_for(v1)
        legacy = [i for i in v2["identifiers"] if i["scheme"] == "betanyc_org_legacy"]
        assert {"scheme": "betanyc_org_legacy", "identifier": v1["betanyc_id"]} in legacy, (
            f"{v1['betanyc_id']} not carried into identifiers[]")


def test_crosswalk_values_become_identifiers():
    scheme_of = {"moda_govid": "nyc_goid", "abo_id": "nys_abo", "wikidata_qid": "wikidata",
                 "irs_ein": "us_irs_ein", "opendata_dataset": "socrata_dataset"}
    for v1 in V1_ENTITIES:
        v2 = _v2_for(v1)
        pairs = {(i["scheme"], i["identifier"]) for i in v2["identifiers"]}
        for field, val in (v1.get("crosswalk") or {}).items():
            if val:  # nulls carry nothing; non-null must appear under the mapped scheme
                assert (scheme_of[field], str(val)) in pairs, (
                    f"{v1['betanyc_id']} crosswalk.{field}={val!r} lost in migration")


def test_boundary_ref_becomes_area():
    for v1 in V1_ENTITIES:
        bref = (v1.get("jurisdiction") or {}).get("boundary_ref")
        if bref:
            v2 = _v2_for(v1)
            assert any(a["scheme"] == "nyc-boundaries" and a["layer"] == bref["layer"]
                       and a.get("id") == bref.get("id") for a in v2["areas"]), (
                f"{v1['betanyc_id']} boundary_ref lost in migration")


def test_coverage_and_boundary_note_preserved_in_area_note():
    for v1 in V1_ENTITIES:
        jur = v1.get("jurisdiction") or {}
        note = _v2_for(v1)["area_note"] or ""
        if jur.get("coverage"):
            assert jur["coverage"] in note, f"{v1['betanyc_id']} coverage lost"
        if jur.get("boundary_note"):
            assert jur["boundary_note"] in note, f"{v1['betanyc_id']} boundary_note lost"


def test_established_by_eo_becomes_mandate():
    for v1 in V1_ENTITIES:
        v2 = _v2_for(v1)
        for eo in v1.get("established_by_eo", []):
            assert any(m["authority_type"] == "executive_order" and m["citation"] == eo["eo_id"]
                       and m["url"] == eo.get("source_pdf_url") and m["role"] == "establishing"
                       for m in v2["mandates"]), f"{v1['betanyc_id']} EO linkage lost"


def test_succession_lists_become_relations():
    for v1 in V1_ENTITIES:
        v2 = _v2_for(v1)
        rels = {(r["type"], r["target_id"]) for r in v2["relations"]}
        for s in v1.get("succeeds_ids", []):
            assert ("successor_of", ID_MAP[s]) in rels, f"{v1['betanyc_id']} succeeds_id lost"
        for s in v1.get("succeeded_by_ids", []):
            assert ("predecessor_of", ID_MAP[s]) in rels, f"{v1['betanyc_id']} succeeded_by_id lost"


def test_scalar_fields_carried():
    for v1 in V1_ENTITIES:
        v2 = _v2_for(v1)
        assert v2["name"] == v1["name"]
        assert v2["government_level"] == v1["government_level"]
        assert v2["classification"] == v1.get("entity_type")
        assert v2["founding_date"] == v1.get("valid_from")
        assert v2["dissolution_date"] == v1.get("valid_to")
        assert v2["status"] == v1["status"]
        assert v2["web_properties"] == v1.get("web_properties", [])
        assert v2["provenance"] == v1["provenance"]


def test_parent_child_ids_remapped():
    for v1 in V1_ENTITIES:
        v2 = _v2_for(v1)
        if v1.get("parent_id"):
            assert v2["parent_id"] == ID_MAP[v1["parent_id"]]
        assert v2["child_ids"] == [ID_MAP[c] for c in v1.get("child_ids", [])]


def test_migration_is_deterministic():
    again = migrate_seed(V1)["entities"]
    assert again == MIGRATED
