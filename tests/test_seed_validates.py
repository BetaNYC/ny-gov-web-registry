"""Offline tests: the seed conforms to the schema, and invariants hold.

Run: python -m pytest    (needs `jsonschema`; skips cleanly if absent)
"""
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "data" / "registry.seed.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))
ENTITIES = SEED["entities"]
_ID_RE = re.compile(r"^BNYC-ORG-\d{6}$")


def test_every_entity_matches_schema():
    jsonschema = pytest.importorskip("jsonschema")
    for e in ENTITIES:
        jsonschema.validate(e, SCHEMA)


def test_ids_are_unique_and_well_formed():
    ids = [e["betanyc_id"] for e in ENTITIES]
    assert all(_ID_RE.match(i) for i in ids), "malformed betanyc_id"
    assert len(ids) == len(set(ids)), "duplicate betanyc_id"


def test_parent_and_child_links_resolve():
    ids = {e["betanyc_id"] for e in ENTITIES}
    for e in ENTITIES:
        if e.get("parent_id"):
            assert e["parent_id"] in ids, f"{e['betanyc_id']} parent_id dangles"
        for c in e.get("child_ids", []):
            assert c in ids, f"{e['betanyc_id']} child_id {c} dangles"


def test_no_fabricated_crosswalk_ids_in_seed():
    # The seed is provisional: per the no-invention rule, every crosswalk id must be null.
    for e in ENTITIES:
        for field, val in e["crosswalk"].items():
            assert val is None, f"{e['betanyc_id']} crosswalk.{field} should be null in the seed, got {val!r}"
