"""Offline tests for the boundaries-mapping build seam (build_registry.apply_boundaries_mapping).

Exercises the pure function on in-memory entities + mapping fixtures, plus outcome assertions on
the committed data/registry.json + data/boundaries_mapping.json. Covers: vocabulary validation
(bad layer id rejected), mapping application (NYPD->pp operates_layer), BP county GEOIDs, the
no-entity-minted invariant, idempotency, and provenance stamping.
"""
import json
import pathlib

import pytest

from build_registry import apply_boundaries_mapping

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))

VOCAB = [{"id": lid} for lid in ("pp", "ps", "dsny", "fb", "sd", "cc", "cd", "ed")]


def _entity(id_, name, sources=("moda",)):
    return {
        "id": id_, "name": name, "government_level": "nyc",
        "identifiers": [], "web_properties": [], "status": "active",
        "provenance": {"sources": list(sources), "last_verified": None},
    }


def _mapping(operates=None, jurisdictions=None):
    return {"operates_layer": operates or [], "jurisdictions": jurisdictions or []}


def test_operates_layer_attaches_whole_layer_reference():
    ents = [_entity("nypd", "NYPD")]
    mp = _mapping(operates=[
        {"entity_id": "nypd", "scheme": "nyc-boundaries", "layer": "pp",
         "justification": "NYPD administers precincts."}])
    out, skipped = apply_boundaries_mapping(ents, mp, VOCAB)
    assert skipped == []
    area = out[0]["areas"][0]
    assert area == {"scheme": "nyc-boundaries", "layer": "pp", "id": None,
                    "role": "operates_layer"}


def test_operates_layer_stamps_manual_provenance():
    ents = [_entity("nypd", "NYPD", sources=["moda"])]
    mp = _mapping(operates=[{"entity_id": "nypd", "scheme": "nyc-boundaries", "layer": "pp"}])
    out, _ = apply_boundaries_mapping(ents, mp, VOCAB)
    assert out[0]["provenance"]["sources"] == ["moda", "manual"]


def test_bp_jurisdiction_uses_census_county_geoid():
    ents = [_entity("bp-mn", "Manhattan BP")]
    mp = _mapping(jurisdictions=[
        {"entity_id": "bp-mn", "scheme": "us_census_geoid", "layer": "county", "id": "36061"}])
    out, skipped = apply_boundaries_mapping(ents, mp, VOCAB)
    assert skipped == []
    area = out[0]["areas"][0]
    assert area == {"scheme": "us_census_geoid", "layer": "county", "id": "36061",
                    "role": "jurisdiction"}


def test_unknown_nyc_boundaries_layer_is_build_error():
    ents = [_entity("nypd", "NYPD")]
    mp = _mapping(operates=[
        {"entity_id": "nypd", "scheme": "nyc-boundaries", "layer": "bogus"}])
    with pytest.raises(ValueError, match="unknown nyc-boundaries layer"):
        apply_boundaries_mapping(ents, mp, VOCAB)


def test_us_census_layer_not_checked_against_nyc_vocab():
    # 'county' is a Census summary level, not an nyc-boundaries id — must NOT raise.
    ents = [_entity("bp-bx", "Bronx BP")]
    mp = _mapping(jurisdictions=[
        {"entity_id": "bp-bx", "scheme": "us_census_geoid", "layer": "county", "id": "36005"}])
    out, _ = apply_boundaries_mapping(ents, mp, VOCAB)
    assert out[0]["areas"][0]["layer"] == "county"


def test_missing_entity_is_skipped_not_minted():
    ents = [_entity("nypd", "NYPD")]
    mp = _mapping(operates=[
        {"entity_id": "ghost-agency", "scheme": "nyc-boundaries", "layer": "dsny"}])
    out, skipped = apply_boundaries_mapping(ents, mp, VOCAB)
    assert len(out) == 1, "a missing entity_id must never mint a new entity"
    assert skipped == [{"entity_id": "ghost-agency", "layer": "dsny",
                        "reason": "entity_id not in registry"}]


def test_idempotent_reattach():
    ents = [_entity("nypd", "NYPD")]
    mp = _mapping(operates=[
        {"entity_id": "nypd", "scheme": "nyc-boundaries", "layer": "pp"},
        {"entity_id": "nypd", "scheme": "nyc-boundaries", "layer": "ps"}])
    out, _ = apply_boundaries_mapping(ents, mp, VOCAB)
    out, _ = apply_boundaries_mapping(out, mp, VOCAB)  # second pass
    areas = out[0]["areas"]
    assert len(areas) == 2, "re-applying duplicated an area reference"
    assert out[0]["provenance"]["sources"].count("manual") == 1


def test_multiple_layers_on_one_entity():
    ents = [_entity("nypd", "NYPD")]
    mp = _mapping(operates=[
        {"entity_id": "nypd", "scheme": "nyc-boundaries", "layer": "pp"},
        {"entity_id": "nypd", "scheme": "nyc-boundaries", "layer": "ps"}])
    out, _ = apply_boundaries_mapping(ents, mp, VOCAB)
    layers = {a["layer"] for a in out[0]["areas"]}
    assert layers == {"pp", "ps"}


# ---- outcome assertions on the committed registry + mapping -------------------------------------

def _registry():
    return json.loads((ROOT / "data" / "registry.json").read_text(encoding="utf-8"))["entities"]


def _mapping_file():
    return json.loads((ROOT / "data" / "boundaries_mapping.json").read_text(encoding="utf-8"))


def test_committed_registry_nypd_has_pp_operates_layer():
    ents = {e["id"]: e for e in _registry()}
    nypd = ents["new-york-city-police-department"]
    keys = {(a["scheme"], a["layer"], a["role"]) for a in nypd.get("areas", [])}
    assert ("nyc-boundaries", "pp", "operates_layer") in keys


def test_committed_registry_bp_county_geoids():
    ents = {e["id"]: e for e in _registry()}
    expect = {
        "office-of-the-borough-president-of-manhattan": "36061",
        "office-of-the-borough-president-of-the-bronx": "36005",
        "office-of-the-borough-president-of-brooklyn": "36047",
        "office-of-the-borough-president-of-queens": "36081",
        "office-of-the-borough-president-of-staten-island": "36085",
    }
    for eid, geoid in expect.items():
        areas = ents[eid].get("areas", [])
        match = [a for a in areas if a["scheme"] == "us_census_geoid" and a["role"] == "jurisdiction"]
        assert match, f"{eid} missing jurisdiction area"
        assert match[0]["id"] == geoid and match[0]["layer"] == "county"


def test_committed_registry_entity_count_unchanged():
    # Phase 4 attaches areas; it must not mint entities (still 317).
    assert len(_registry()) == 317


def test_mapping_covers_all_22_layers_exactly_once():
    mp = _mapping_file()
    covered = []
    for r in mp["operates_layer"]:
        covered.append(r["layer"])
    for r in mp["defines_not_operates"] + mp["no_registry_entity"] + mp["deferred"]:
        covered.append(r["layer"])
    vocab = json.loads((ROOT / "data" / "nyc_boundaries_layers.json").read_text(encoding="utf-8"))
    all_ids = {row["id"] for row in vocab["layers"]}
    # pp and ps both map to NYPD, so operates_layer has 8 rows over the 22-layer vocabulary.
    assert set(covered) == all_ids, "every nyc-boundaries layer must appear in the mapping"
    # No layer double-counted across the disposition buckets (each layer has exactly one home).
    assert len(covered) == len(all_ids)


def test_all_committed_areas_are_schema_valid():
    jsonschema = pytest.importorskip("jsonschema")
    for e in _registry():
        jsonschema.validate(e, SCHEMA)
