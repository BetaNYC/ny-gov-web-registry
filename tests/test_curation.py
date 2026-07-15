"""Offline tests for operator-confirmed curation (scripts/build_registry.py curation seam).

Exercises the pure curation functions with in-memory fixtures AND asserts the outcomes on the
committed data/registry.json: exactly one EDC entity carrying NYC_GOID_000177, the ESD variant
guards present, and the life-sciences advisory council kept distinct.
"""
import json
import pathlib

import pytest

from build_registry import (
    apply_entity_curation,
    apply_greenbook_enrichment,
    apply_seed_curation,
    build_entities,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))


def _seed_edc():
    return {
        "id": "nycedc", "name": "New York City Economic Development Corporation",
        "short_name": "NYCEDC", "government_level": "authority", "other_names": [],
        "identifiers": [{"scheme": "betanyc_org_legacy", "identifier": "BNYC-ORG-000007"}],
        "web_properties": [{"domain": "edc.nyc", "role": "primary"}], "status": "active",
        "provenance": {"sources": ["manual"], "last_verified": "2026-07-11"},
    }


CURATION = {
    "confirmed_merges": [{
        "seed_id": "nycedc",
        "match": {"scheme": "nyc_goid", "identifier": "NYC_GOID_000177"},
        "add_other_names": [{"name": "Economic Development Corporation", "note": "MODA name"}],
        "add_provenance_source": "moda",
    }],
    "other_names_additions": [{
        "entity_id": "esd",
        "names": [{"name": "Empire State Development Corporation", "note": "guard"}],
    }],
}


def test_confirmed_merge_collapses_to_one_edc_entity():
    seed = apply_seed_curation([_seed_edc()], CURATION)
    # The upstream city record for EDC, keyed only by nyc_goid (as sync_moda emits it).
    moda_edc = {"name": "Economic Development Corporation", "government_level": "nyc",
                "identifiers": [{"scheme": "nyc_goid", "identifier": "NYC_GOID_000177"}],
                "web_properties": [{"domain": "edc.nyc", "role": "primary"}], "status": "active",
                "provenance": {"sources": ["moda"], "last_verified": None}}
    entities, proposals = build_entities(seed, [[moda_edc]])
    assert len(entities) == 1, "EDC must not be minted as a second entity"
    edc = entities[0]
    assert edc["id"] == "nycedc"  # seed keeps its id
    pairs = {(i["scheme"], i["identifier"]) for i in edc["identifiers"]}
    assert ("nyc_goid", "NYC_GOID_000177") in pairs
    assert "Economic Development Corporation" in {o["name"] for o in edc["other_names"]}
    # No mint proposal for the collapsed record.
    assert not any(p["action"] == "add" for p in proposals)


def test_seed_curation_is_pure():
    original = [_seed_edc()]
    apply_seed_curation(original, CURATION)
    assert original[0]["identifiers"] == [{"scheme": "betanyc_org_legacy", "identifier": "BNYC-ORG-000007"}]
    assert original[0]["other_names"] == []


def test_entity_curation_adds_variants():
    esd = {"id": "esd", "name": "Empire State Development", "government_level": "authority",
           "other_names": [], "web_properties": [], "status": "active", "identifiers": []}
    apply_entity_curation([esd], CURATION)
    assert "Empire State Development Corporation" in {o["name"] for o in esd["other_names"]}


def test_greenbook_enrichment_is_idempotent_and_deduped():
    ent = {"id": "x", "name": "X", "government_level": "nyc", "web_properties": [],
           "status": "active", "identifiers": [], "provenance": {"sources": ["moda"]}}
    enr = {"enrichments": [{
        "entity_id": "x", "add_provenance_source": "greenbook",
        "contact_details": [{"type": "voice", "value": "(212) 555-0000", "note": "Greenbook 2023-12"}],
        "web_candidates": [{"domain": "legacy.example", "role": "legacy"}],
    }]}
    apply_greenbook_enrichment([ent], enr)
    apply_greenbook_enrichment([ent], enr)  # second pass must not duplicate
    assert len(ent["contact_details"]) == 1
    assert len(ent["web_properties"]) == 1
    assert ent["provenance"]["sources"].count("greenbook") == 1


# --- Outcome assertions on the committed built registry -------------------------------------

@pytest.fixture(scope="module")
def registry():
    return json.loads((ROOT / "data" / "registry.json").read_text(encoding="utf-8"))["entities"]


def test_registry_has_single_edc(registry):
    with_goid = [e for e in registry
                 if any(i["scheme"] == "nyc_goid" and i["identifier"] == "NYC_GOID_000177"
                        for i in e.get("identifiers", []))]
    assert [e["id"] for e in with_goid] == ["nycedc"]
    assert not any(e["id"] == "economic-development-corporation" for e in registry)


def test_registry_edc_has_merged_name_and_provenance(registry):
    edc = next(e for e in registry if e["id"] == "nycedc")
    assert "Economic Development Corporation" in {o["name"] for o in edc["other_names"]}
    assert "moda" in edc["provenance"]["sources"]


def test_registry_esd_variants_present_and_distinct(registry):
    esd = next(e for e in registry if e["id"] == "esd")
    names = {o["name"] for o in esd["other_names"]}
    assert "Empire State Development Corporation" in names
    assert "New York State Urban Development Corporation" in names
    assert esd["government_level"] == "authority"
    # Life-sciences advisory council stays a separate entity (substring trap, must not merge).
    assert any(e["id"] == "economic-development-corporation-life-sciences-advisory-council"
               for e in registry)


def test_registry_entity_count_is_317(registry):
    assert len(registry) == 317


def test_registry_all_valid(registry):
    jsonschema = pytest.importorskip("jsonschema")
    for e in registry:
        jsonschema.validate(e, SCHEMA)
