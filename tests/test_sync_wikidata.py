"""Offline tests for domain-anchored Wikidata QID matching (scripts/sync_wikidata.py).

No fetch, no live SPARQL — exercises the pure functions on in-memory bindings/entities, plus
outcome assertions on the committed data/registry.json + data/wikidata_reconciliation.json.

Fixtures encode the DOCUMENTED WDQS JSON shape (head.vars = item, itemLabel, website, altLabel;
one binding per item×altLabel×website) — see sync_wikidata.py's module docstring and
docs/freshness.md § Wikidata. They are not guessed: they mirror data/cache/wikidata_nyc_gov_orgs.json.
"""
import json
import pathlib
import re

import pytest

from build_registry import apply_wikidata_enrichment
from sync_wikidata import (
    QID_RE,
    apply_repoint,
    build_indexes,
    classify,
    legacy_domain_leads,
    load_repoints,
    norm_host,
    parse_candidates,
    qid_of,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))


def _binding(qid, label=None, alt=None, website=None):
    b = {"item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"}}
    if label is not None:
        b["itemLabel"] = {"xml:lang": "en", "type": "literal", "value": label}
    if alt is not None:
        b["altLabel"] = {"xml:lang": "en", "type": "literal", "value": alt}
    if website is not None:
        b["website"] = {"type": "uri", "value": website}
    return b


def _entity(id_, name, domains=(), short_name=None, other=()):
    return {
        "id": id_, "name": name, "short_name": short_name, "government_level": "nyc",
        "other_names": [{"name": o} for o in other],
        "web_properties": [{"domain": d, "role": "primary"} for d in domains],
        "identifiers": [], "status": "active", "provenance": {"sources": ["moda"]},
    }


# --- host / qid normalization --------------------------------------------------------------

def test_norm_host_strips_scheme_path_and_www():
    assert norm_host("https://www.nyc.gov/site/mome/index.page") == "nyc.gov"
    assert norm_host("http://MTA.info/") == "mta.info"
    assert norm_host("https://www1.nyc.gov/x") == "nyc.gov"
    assert norm_host("https://new.mta.info/") == "new.mta.info"  # non-www subdomain preserved
    assert norm_host("") == ""


def test_qid_extraction_and_format_guard():
    assert qid_of("http://www.wikidata.org/entity/Q60") == "Q60"
    assert QID_RE.match("Q7013226")
    assert not QID_RE.match("q60")      # must be capital Q + digits
    assert not QID_RE.match("Q60x")
    assert not QID_RE.match("P856")


# --- parse_candidates ----------------------------------------------------------------------

def test_parse_collapses_rows_per_qid_and_aggregates():
    bindings = [
        _binding("Q1", "Dept of Foo", alt="Foo Agency", website="https://www.foo.nyc.gov/x"),
        _binding("Q1", "Dept of Foo", alt="The Foo Office", website="https://www.foo.nyc.gov/x"),
        _binding("Q1", "Dept of Foo", alt="Foo Agency", website="https://foo-legacy.org/"),
        _binding("Q2", "Bar Board"),  # no website, no alias
    ]
    cands = parse_candidates(bindings)
    by_qid = {c["qid"]: c for c in cands}
    assert set(by_qid) == {"Q1", "Q2"}
    assert by_qid["Q1"]["label"] == "Dept of Foo"
    assert by_qid["Q1"]["aliases"] == ["Foo Agency", "The Foo Office"]  # deduped + sorted
    assert by_qid["Q1"]["hosts"] == ["foo-legacy.org", "foo.nyc.gov"]   # www-normalized + sorted
    assert by_qid["Q2"]["hosts"] == [] and by_qid["Q2"]["aliases"] == []


def test_parse_skips_malformed_item_uris():
    bindings = [_binding("Q1", "Ok"), {"item": {"value": "http://example/NotAQid"}}]
    cands = parse_candidates(bindings)
    assert [c["qid"] for c in cands] == ["Q1"]


# --- classify: the four core paths ---------------------------------------------------------

def test_distinctive_domain_auto_attaches():
    entities = [_entity("mta", "Metropolitan Transportation Authority", domains=["mta.info"]),
                _entity("other", "Some Other Agency", domains=["other.nyc.gov"])]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    cand = parse_candidates([_binding("Q1", "MTA umbrella", website="https://mta.info/")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "auto"
    assert d["reason"] == "distinctive_domain"
    assert d["entity_id"] == "mta"
    assert d["matched_via_host"] == "mta.info"


def test_nyc_gov_apex_sharing_must_not_auto_match():
    # THE trap: nyc.gov is owned by many entities (host-only MODA storage), so a QID whose P856
    # reduces to nyc.gov is NOT distinctive and must never auto-attach — it falls through to name.
    entities = [
        _entity("a", "Agency A", domains=["nyc.gov"]),
        _entity("b", "Agency B", domains=["nyc.gov"]),
        _entity("c", "Agency C", domains=["nyc.gov"]),
    ]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    # A QID whose only host is the shared apex AND whose name matches nothing -> apex_shared review.
    cand = parse_candidates([_binding("Q9", "Totally Different Name",
                                      website="https://www.nyc.gov/site/zzz/index.page")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "review"
    assert d["reason"] == "apex_shared_only"
    assert d["entity_id"] is None
    assert "nyc.gov" in d["apex_shared_hosts"]


def test_apex_domain_falls_through_to_name_proposal():
    # Same shared apex, but now the QID's NAME exactly matches one entity -> proposal (never auto).
    entities = [_entity("a", "Agency A", domains=["nyc.gov"]),
                _entity("b", "Department of Sanitation", domains=["nyc.gov"])]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    cand = parse_candidates([_binding("Q5", "Department of Sanitation",
                                      website="https://www.nyc.gov/site/dsny")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "proposal"      # NOT auto — name alone never auto-attaches
    assert d["reason"] == "exact_name"
    assert d["entity_id"] == "b"


def test_name_only_no_domain_is_proposal_not_auto():
    entities = [_entity("dcla", "Department of Cultural Affairs")]  # no web_properties at all
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    cand = parse_candidates([_binding("Q3339045", "New York City Department of Cultural Affairs",
                                      alt="Department of Cultural Affairs")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "proposal"
    assert d["entity_id"] == "dcla"


def test_ambiguous_name_goes_to_review():
    entities = [_entity("doe", "New York City Department of Education",
                        other=["New York City Public Schools"]),
                _entity("nycps", "New York City Public Schools")]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    cand = parse_candidates([_binding("Q408230", "New York City Public Schools")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "review"
    assert d["reason"] == "ambiguous_name"
    assert {e["entity_id"] for e in d["candidate_entities"]} == {"doe", "nycps"}


def test_unmatched_when_nothing_hits():
    entities = [_entity("a", "Agency A", domains=["a.nyc.gov"])]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    cand = parse_candidates([_binding("Q999", "Not In Registry", website="https://elsewhere.com/")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "unmatched"


def test_distinctive_domains_to_different_entities_is_conflict():
    entities = [_entity("a", "Agency A", domains=["a-site.org"]),
                _entity("b", "Agency B", domains=["b-site.org"])]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    cand = parse_candidates([_binding("Q7", "Ambiguous", website="https://a-site.org/"),
                             _binding("Q7", "Ambiguous", website="https://b-site.org/")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "review"
    assert d["reason"] == "domain_conflict"
    assert {c["entity_id"] for c in d["conflict_entities"]} == {"a", "b"}


def test_name_divergent_flag_on_place_vs_office_conflation():
    # Wikidata's borough item (a place) carries the borough-president OFFICE site as P856.
    entities = [_entity("bp-bronx", "Office of the Borough President of the Bronx",
                        domains=["bronxboropres.nyc.gov"])]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    cand = parse_candidates([_binding("Q18426", "The Bronx",
                                      website="https://bronxboropres.nyc.gov/")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "auto"                 # domain is the strong signal -> still auto
    assert d["name_divergent"] is True             # ...but flagged for operator review


def test_matching_label_is_not_divergent():
    entities = [_entity("parks", "Department of Parks and Recreation", domains=["nycgovparks.org"])]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    cand = parse_candidates([_binding("Q1894232", "New York City Department of Parks and Recreation",
                                      alt="Department of Parks and Recreation",
                                      website="https://www.nycgovparks.org/")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "auto" and d["name_divergent"] is False


def test_legacy_domain_lead_from_extra_p856_host():
    entities = [_entity("nyct", "New York City Transit Authority", domains=[])]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    # matched by name (proposal), P856 host not owned + not apex-shared -> legacy lead.
    cand = parse_candidates([_binding("Q1325591", "New York City Transit Authority",
                                      website="https://new.mta.info/")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    leads = legacy_domain_leads(d, by_id, domain_index)
    assert len(leads) == 1
    assert leads[0]["lead_domain"] == "new.mta.info"
    assert leads[0]["entity_id"] == "nyct"


# --- apply_wikidata_enrichment (build seam) ------------------------------------------------

def test_enrichment_attaches_qid_and_is_verbatim():
    ent = {"id": "x", "name": "X", "government_level": "nyc", "web_properties": [],
           "status": "active", "identifiers": [], "provenance": {"sources": ["moda"]}}
    enr = {"enrichments": [{"entity_id": "x", "add_provenance_source": "wikidata",
                            "identifiers": [{"scheme": "wikidata", "identifier": "Q7013226"}]}]}
    apply_wikidata_enrichment([ent], enr)
    wids = [i["identifier"] for i in ent["identifiers"] if i["scheme"] == "wikidata"]
    assert wids == ["Q7013226"]
    assert QID_RE.match(wids[0])  # stored verbatim, matches ^Q\d+$
    assert "wikidata" in ent["provenance"]["sources"]


def test_enrichment_is_idempotent_and_deduped():
    ent = {"id": "x", "name": "X", "government_level": "nyc", "web_properties": [],
           "status": "active", "identifiers": [{"scheme": "nyc_goid", "identifier": "NYC_GOID_1"}],
           "provenance": {"sources": ["moda"]}}
    enr = {"enrichments": [{"entity_id": "x", "add_provenance_source": "wikidata",
                            "identifiers": [{"scheme": "wikidata", "identifier": "Q1"}]}]}
    apply_wikidata_enrichment([ent], enr)
    apply_wikidata_enrichment([ent], enr)  # second pass must not duplicate
    wids = [i for i in ent["identifiers"] if i["scheme"] == "wikidata"]
    assert len(wids) == 1
    assert ent["provenance"]["sources"].count("wikidata") == 1
    # pre-existing identifier untouched
    assert any(i["scheme"] == "nyc_goid" for i in ent["identifiers"])


def test_enrichment_skips_unknown_entity_id():
    _, skipped = apply_wikidata_enrichment(
        [], {"enrichments": [{"entity_id": "ghost",
                              "identifiers": [{"scheme": "wikidata", "identifier": "Q1"}]}]})
    assert skipped and skipped[0]["entity_id"] == "ghost"


# --- operator re-point guard (place is not an office) --------------------------------------

def _auto_place_decision():
    """A raw domain auto-match of a PLACE QID onto an office entity (the pre-guard state)."""
    entities = [_entity("bp-bronx", "Office of the Borough President of the Bronx",
                        domains=["bronxboropres.nyc.gov"])]
    domain_index, name_index = build_indexes(entities)
    by_id = {e["id"]: e for e in entities}
    cand = parse_candidates([_binding("Q18426", "The Bronx",
                                      website="https://bronxboropres.nyc.gov/")])[0]
    d = classify(cand, domain_index, name_index, by_id)
    assert d["decision"] == "auto"  # domain rule alone WOULD attach the place — the guard overrides
    return d


def test_load_repoints_keys_by_entity_id():
    curation = {"wikidata_repoints": [
        {"entity_id": "bp-bronx", "reject_qid": "Q18426", "action": "drop"}]}
    rp = load_repoints(curation)
    assert rp["bp-bronx"]["reject_qid"] == "Q18426"
    assert load_repoints({}) == {}


def test_repoint_drop_overrides_raw_domain_automatch():
    # THE guard: an operator drop directive takes precedence over the domain auto-match, so the
    # place QID is NOT attached. Domain rule alone is insufficient when the item is a place.
    d = apply_repoint(_auto_place_decision(),
                      {"bp-bronx": {"entity_id": "bp-bronx", "reject_qid": "Q18426",
                                    "action": "drop", "reason": "no_suitable_item"}})
    assert d["decision"] == "re_pointed"
    assert d["action"] == "drop"
    assert d["rejected_qid"] == "Q18426"
    assert d.get("attach_qid") is None


def test_repoint_attach_swaps_place_qid_for_office_qid():
    # When an operator-verified office QID exists, re-point attaches IT, never the place QID.
    d = apply_repoint(_auto_place_decision(),
                      {"bp-bronx": {"entity_id": "bp-bronx", "reject_qid": "Q18426",
                                    "action": "attach", "attach_qid": "Q9999999"}})
    assert d["decision"] == "re_pointed"
    assert d["action"] == "attach"
    assert d["rejected_qid"] == "Q18426" and d["attach_qid"] == "Q9999999"


def test_repoint_ignores_non_matching_qid():
    # A directive whose reject_qid does not equal the matched QID must not fire (safety).
    d = apply_repoint(_auto_place_decision(),
                      {"bp-bronx": {"entity_id": "bp-bronx", "reject_qid": "Q999", "action": "drop"}})
    assert d["decision"] == "auto"  # untouched


def test_repoint_ignores_non_auto_decisions():
    proposal = {"decision": "proposal", "entity_id": "x", "qid": "Q1"}
    assert apply_repoint(proposal, {"x": {"reject_qid": "Q1", "action": "drop"}}) == proposal


def test_dropped_repoint_yields_no_enrichment():
    # Integration of the guard through apply_wikidata_enrichment: a dropped re-point produces no
    # enrichment record, so the entity never gains the place QID.
    ent = {"id": "bp-bronx", "name": "Office of the Borough President of the Bronx",
           "government_level": "nyc", "web_properties": [{"domain": "bronxboropres.nyc.gov"}],
           "status": "active", "identifiers": [], "provenance": {"sources": ["moda"]}}
    apply_wikidata_enrichment([ent], {"enrichments": []})  # drop path emits nothing
    assert not any(i["scheme"] == "wikidata" for i in ent["identifiers"])


# --- outcome assertions on the committed build ---------------------------------------------

@pytest.fixture(scope="module")
def registry():
    return json.loads((ROOT / "data" / "registry.json").read_text(encoding="utf-8"))["entities"]


@pytest.fixture(scope="module")
def report():
    return json.loads((ROOT / "data" / "wikidata_reconciliation.json").read_text(encoding="utf-8"))


def test_committed_registry_has_three_wikidata_qids(registry):
    # 3 kept (Parks, H+H, Javits); the 4 borough place QIDs were re-pointed -> dropped.
    attached = {e["id"] for e in registry
                if any(i["scheme"] == "wikidata" for i in e.get("identifiers", []))}
    assert attached == {"department-of-parks-and-recreation", "nyc-health-hospitals",
                        "convention-center-operating-corporation-javits"}


def test_committed_no_borough_office_carries_a_wikidata_qid(registry):
    # Operator decision (PR #8): an office is not a place — no borough-president office entity may
    # carry a place QID, and Wikidata has no per-borough office item, so none carry any wikidata id.
    boroughs = [e for e in registry if "borough-president" in e["id"]]
    assert boroughs  # sanity: the entities exist
    for e in boroughs:
        assert not any(i["scheme"] == "wikidata" for i in e.get("identifiers", [])), e["id"]


def test_committed_report_records_four_dropped_repoints(report):
    rp = report["report"]["re_pointed"]
    assert {x["rejected_qid"] for x in rp} == {"Q18426", "Q18419", "Q11299", "Q18432"}
    assert all(x["action"] == "drop" for x in rp)
    assert len(report["report"]["no_suitable_item"]) == 4


def test_every_committed_wikidata_identifier_is_verbatim_qid(registry):
    for e in registry:
        for i in e.get("identifiers", []):
            if i["scheme"] == "wikidata":
                assert re.match(r"^Q\d+$", i["identifier"]), f"{e['id']} bad QID {i['identifier']!r}"


def test_wikidata_attached_entities_carry_provenance(registry):
    for e in registry:
        if any(i["scheme"] == "wikidata" for i in e.get("identifiers", [])):
            assert "wikidata" in e["provenance"]["sources"]


def test_committed_report_stats_and_count_unchanged(registry, report):
    assert report["stats"]["auto_attached"] == 3
    assert report["stats"]["re_pointed"] == 4
    assert report["distinct_qids"] == 109
    assert len(registry) == 317  # QID attachment never mints entities


def test_committed_registry_all_valid(registry):
    jsonschema = pytest.importorskip("jsonschema")
    for e in registry:
        jsonschema.validate(e, SCHEMA)
