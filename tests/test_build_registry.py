"""Offline tests at the built-registry seam: exercise build_entities() with in-memory
fixtures (no file IO, no network) and assert the merge invariants from issue #1:
identifier-scheme-first matching, id stability, mint-once new ids, proposals-not-deletes.
"""
import json
import pathlib

import pytest

from build_registry import build_entities

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))


def _entity(id_, name, identifiers=None):
    return {
        "id": id_,
        "name": name,
        "government_level": "nyc",
        "identifiers": identifiers or [],
        "web_properties": [],
        "status": "active",
        "provenance": {"sources": ["manual"], "last_verified": None},
    }


def _source(name, identifiers=None):
    rec = {
        "name": name,
        "government_level": "nyc",
        "identifiers": identifiers or [],
        "web_properties": [],
        "status": "active",
        "provenance": {"sources": ["moda"], "last_verified": None},
    }
    return rec


SEED = [
    _entity("oti", "Office of Technology and Innovation",
            [{"scheme": "nyc_goid", "identifier": "100200"}]),
    _entity("nyc-doe", "New York City Department of Education"),
]


def test_id_stability_no_sources():
    entities, proposals = build_entities(SEED, [])
    assert [e["id"] for e in entities] == ["oti", "nyc-doe"]
    assert proposals == []


def test_scheme_first_match_enriches_not_adds():
    # Source shares OTI's nyc_goid but under a DIFFERENT name; must match by identifier,
    # enrich with the new wikidata pair, and NOT create a duplicate record.
    src = _source("Office of Tech & Innovation (DoITT successor)",
                  [{"scheme": "nyc_goid", "identifier": "100200"},
                   {"scheme": "wikidata", "identifier": "Q42"}])
    entities, proposals = build_entities(SEED, [[src]])
    assert len(entities) == len(SEED), "scheme match must not add a record"
    oti = next(e for e in entities if e["id"] == "oti")
    pairs = {(i["scheme"], i["identifier"]) for i in oti["identifiers"]}
    assert ("wikidata", "Q42") in pairs, "new identifier not enriched onto matched record"
    assert {"action": "enrich", "id": "oti",
            "identifier": {"scheme": "wikidata", "identifier": "Q42"}} in proposals


def test_name_fallback_match():
    src = _source("New York City Department of Education",
                  [{"scheme": "nyc_goid", "identifier": "555"}])
    entities, proposals = build_entities(SEED, [[src]])
    assert len(entities) == len(SEED), "name match must not add a record"
    doe = next(e for e in entities if e["id"] == "nyc-doe")
    assert ("nyc_goid", "555") in {(i["scheme"], i["identifier"]) for i in doe["identifiers"]}


def test_unmatched_source_mints_new_id_once():
    src = _source("Department of Sanitation",
                  [{"scheme": "nyc_goid", "identifier": "900"}])
    entities, proposals = build_entities(SEED, [[src]])
    assert len(entities) == len(SEED) + 1
    added = entities[-1]
    assert added["id"] == "department-of-sanitation"
    assert added["name"] == "Department of Sanitation"
    assert {"action": "add", "id": "department-of-sanitation",
            "name": "Department of Sanitation"} in proposals


def test_minted_id_avoids_collision_with_existing_id():
    # A minted slug that would collide with an existing entity id gets suffixed.
    # Seed 'sanitation' has a different name, so an incoming 'Sanitation' does not
    # match by name or identifier -> it is added, and its slug must dodge the taken id.
    seed = SEED + [_entity("sanitation", "Department of Sanitation and Cleaning")]
    src = _source("Sanitation", [{"scheme": "nyc_goid", "identifier": "900"}])
    entities, proposals = build_entities(seed, [[src]])
    added = next(e for e in entities if e["name"] == "Sanitation")
    assert added["id"] == "sanitation-2", "minted id collided with an existing id"
    assert {"action": "add", "id": "sanitation-2", "name": "Sanitation"} in proposals


def test_no_silent_deletes():
    # A seed entity absent from every source batch survives untouched.
    src = _source("Department of Sanitation", [{"scheme": "nyc_goid", "identifier": "900"}])
    entities, _ = build_entities(SEED, [[src]])
    assert "nyc-doe" in {e["id"] for e in entities}
    assert "oti" in {e["id"] for e in entities}


def test_enrich_never_overwrites_existing_pair():
    # Source re-asserts OTI's existing nyc_goid; no duplicate identifier, no proposal.
    src = _source("Office of Technology and Innovation",
                  [{"scheme": "nyc_goid", "identifier": "100200"}])
    entities, proposals = build_entities(SEED, [[src]])
    oti = next(e for e in entities if e["id"] == "oti")
    goids = [i for i in oti["identifiers"] if i["scheme"] == "nyc_goid"]
    assert len(goids) == 1, "existing identifier duplicated"
    assert proposals == []


def test_built_output_is_schema_valid():
    jsonschema = pytest.importorskip("jsonschema")
    src = _source("Department of Sanitation", [{"scheme": "nyc_goid", "identifier": "900"}])
    entities, _ = build_entities(SEED, [[src]])
    for e in entities:
        jsonschema.validate(e, SCHEMA)
