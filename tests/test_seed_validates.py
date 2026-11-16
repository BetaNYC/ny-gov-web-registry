"""Offline tests: the migrated (v2) seed conforms to the schema, and invariants hold.

Run: python -m pytest    (needs `jsonschema`; schema test skips cleanly if absent)
"""
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "data" / "registry.seed.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))
ENTITIES = SEED["entities"]
_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def test_every_entity_matches_schema():
    jsonschema = pytest.importorskip("jsonschema")
    for e in ENTITIES:
        jsonschema.validate(e, SCHEMA)


def test_ids_are_unique_and_well_formed():
    ids = [e["id"] for e in ENTITIES]
    assert all(_ID_RE.match(i) for i in ids), "malformed id"
    assert len(ids) == len(set(ids)), "duplicate id"


def test_ids_are_steward_neutral():
    # De-branding invariant: no BNYC-ORG-style prefix leaks into the native id.
    for e in ENTITIES:
        assert not e["id"].startswith("bnyc"), f"{e['id']} still carries a steward prefix"


def test_parent_and_child_links_resolve():
    ids = {e["id"] for e in ENTITIES}
    for e in ENTITIES:
        if e.get("parent_id"):
            assert e["parent_id"] in ids, f"{e['id']} parent_id dangles"
        for c in e.get("child_ids", []):
            assert c in ids, f"{e['id']} child_id {c} dangles"


def test_relation_targets_resolve():
    ids = {e["id"] for e in ENTITIES}
    for e in ENTITIES:
        for rel in e.get("relations", []):
            assert rel["target_id"] in ids, f"{e['id']} relation target {rel['target_id']} dangles"


def test_no_fabricated_verified_identifiers_in_seed():
    # The seed is provisional: the only populated scheme is the legacy id carry-over.
    # No verified-only scheme (nyc_goid, wikidata, us_irs_ein, ...) may appear.
    for e in ENTITIES:
        for ident in e["identifiers"]:
            assert ident["scheme"] == "betanyc_org_legacy", (
                f"{e['id']} carries an unverified identifier {ident} — seed must not invent ids")


def test_suny_exists_once_as_the_system_not_its_campuses():
    # Noel's decision on epic #58: public universities link to the master list as the system.
    suny = [e for e in ENTITIES if e["id"] == "suny"]
    assert len(suny) == 1
    e = suny[0]
    assert (e["name"], e["short_name"]) == ("State University of New York", "SUNY")
    assert [(w["domain"], w["role"]) for w in e["web_properties"]] == [("suny.edu", "primary")]
    # Modeled like cuny (the closest existing pattern; see the PR for #25).
    cuny = next(x for x in ENTITIES if x["id"] == "cuny")
    assert (e["government_level"], e["classification"]) == (cuny["government_level"], cuny["classification"])
    registry = json.loads((ROOT / "data" / "registry.json").read_text(encoding="utf-8"))["entities"]
    names = [x["name"] for x in registry if "state university of new york" in x["name"].lower()
             or x["id"].startswith("suny")]
    assert names == ["State University of New York"], names
