"""Offline tests for sync_nygov.build_records (NYS executive agencies) and its seam with
build_registry. In-memory fixtures only; no file IO, no network."""
import json
import pathlib

import pytest

from build_registry import build_entities
from sync_nygov import build_records

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))

BASE = [
    {"id": "department-of-transportation", "name": "Department of Transportation",
     "government_level": "nyc", "web_properties": [{"domain": "www.nyc.gov", "role": "primary"}],
     "identifiers": [], "status": "active", "provenance": {"sources": ["moda"], "last_verified": None}},
    {"id": "panynj", "name": "Port Authority of New York and New Jersey", "government_level": "bi-state",
     "web_properties": [{"domain": "panynj.gov", "role": "primary"}],
     "identifiers": [], "status": "active", "provenance": {"sources": ["manual"], "last_verified": None}},
    {"id": "watershed-council", "name": "Watershed Council", "government_level": "nyc",
     "web_properties": [{"domain": "dos.ny.gov", "role": "primary"}],
     "identifiers": [], "status": "active", "provenance": {"sources": ["moda"], "last_verified": None}},
]

NYGOV = [{"name": "Department of Transportation", "url": "https://www.ny.gov/agencies/department-transportation"},
         {"name": "Department of State", "url": "https://www.ny.gov/agencies/department-state"},
         {"name": "Office of the Governor", "url": "https://www.ny.gov/agencies/office-governor"},
         {"name": "Liquidation Bureau", "url": "https://www.ny.gov/agencies/liquidation-bureau"}]
KJW8 = [{"agency": "New York State Department of Transportation (DOT)",
         "agency_contact_url": "https://www.dot.ny.gov/divisions"},
        {"agency": "Office of Renewable Energy Siting (ORES)", "agency_contact_url": None}]

CURATION = {
    "state_agencies": [
        {"key": "dot", "name": "New York State Department of Transportation",
         "nygov_name": "Department of Transportation",
         "kjw8_agency": "New York State Department of Transportation (DOT)",
         "short_name": "DOT", "primary": "dot.ny.gov",
         "host_notes": {"nymtc.org": "A metropolitan planning organization."}},
        {"key": "dos", "name": "New York State Department of State", "nygov_name": "Department of State",
         "kjw8_agency": None, "short_name": "DOS", "primary": "dos.ny.gov"},
    ],
    "state_listing_codes": {
        "DOT": {"target": "agency:dot"},
        "DOS": {"target": "agency:dos"},
        "PANYNJ": {"target": "entity:panynj"},
        "Governor": {"target": "review:statewide_elected_office", "note": "elected"},
        "BPCA": {"target": "abo:Battery Park City Authority", "primary": "bpca.ny.gov"},
    },
    "state_listing_host_overrides": {"my.ny.gov": {"target": "review:listing_attribution_doubtful",
                                                   "note": "login"}},
    "state_directory_covered_elsewhere": {"nygov": {"Office of the Governor": "review"}, "kjw8": {}},
}

LISTING = [
    {"domain": "dot.ny.gov", "url": "", "agency": "DOT"},
    {"domain": "nymtc.org", "url": "", "agency": "DOT"},
    {"domain": "my.ny.gov", "url": "", "agency": "DOT"},
    {"domain": "dos.ny.gov", "url": "", "agency": "DOS"},
    {"domain": "panynj.gov", "url": "", "agency": "PANYNJ"},
    {"domain": "governor.ny.gov", "url": "", "agency": "Governor"},
    {"domain": "bpca.ny.gov", "url": "", "agency": "BPCA"},
]


def _run(curation=CURATION):
    return build_records(LISTING, NYGOV, KJW8, curation, BASE)


def _agencies(records):
    return {r["name"]: r for r in records if r.get("_expect_new")}


def test_agency_record_shape_and_schema():
    jsonschema = pytest.importorskip("jsonschema")
    records, _ = _run()
    entities, _ = build_entities(BASE, [records])
    new = [e for e in entities if e["government_level"] == "nys"]
    assert {e["id"] for e in new} == {"new-york-state-department-of-transportation",
                                      "new-york-state-department-of-state"}
    for e in new:
        jsonschema.validate(e, SCHEMA)
        assert e["identifiers"] == []
        assert "state_listing" in e["provenance"]["sources"]


def test_primary_and_microsites_with_notes():
    dot = _agencies(_run()[0])["New York State Department of Transportation"]
    roles = [(w["domain"], w["role"]) for w in dot["web_properties"]]
    assert roles == [("dot.ny.gov", "primary"), ("nymtc.org", "microsite")]
    assert "agrees with the agency_contact_url host" in dot["web_properties"][0]["notes"]
    assert "metropolitan planning organization" in dot["web_properties"][1]["notes"]
    assert set(dot["provenance"]["sources"]) == {"state_listing", "nygov", "nygov_contacts"}
    assert {o["name"] for o in dot["other_names"]} == {
        "Department of Transportation", "New York State Department of Transportation (DOT)"}
    assert dot["links"][0]["url"].startswith("https://www.ny.gov/agencies/")


def test_state_agency_never_merges_into_the_city_namesake():
    # The ny.gov name 'Department of Transportation' equals the NYC entity's name. The minted name
    # carries the state prefix, and _expect_new makes any residual collision a hard error.
    records, _ = _run()
    entities, _ = build_entities(BASE, [records])
    city = next(e for e in entities if e["id"] == "department-of-transportation")
    assert city["government_level"] == "nyc" and "state_listing" not in city["provenance"]["sources"]
    clash = [dict(records[0], name="Department of Transportation")]
    with pytest.raises(ValueError):
        build_entities(BASE, [clash])


def test_shared_host_is_applied_and_flagged():
    records, review = _run()
    dos = _agencies(records)["New York State Department of State"]
    assert dos["web_properties"][0]["domain"] == "dos.ny.gov"
    assert any(i["reason"] == "host_also_owned_by_other_entity" and i["host"] == "dos.ny.gov"
               for i in review)


def test_review_targets_and_overrides_are_held_not_minted():
    records, review = _run()
    hosts = {h for r in records for w in r.get("web_properties", []) for h in [w["domain"]]}
    assert "governor.ny.gov" not in hosts and "my.ny.gov" not in hosts
    reasons = {i["reason"]: i for i in review}
    assert reasons["statewide_elected_office"]["hosts"] == ["governor.ny.gov"]
    assert reasons["listing_attribution_doubtful"]["hosts"] == ["my.ny.gov"]


def test_existing_entity_target_adds_only_missing_hosts():
    records, _ = _run()
    pan = next(r for r in records if r.get("_match_entity_id") == "panynj")
    assert pan["_on_match"]["add_web_properties"] == []  # panynj.gov is already owned


def test_unused_directory_names_are_reported():
    _, review = _run()
    item = next(i for i in review if i["reason"] == "in_directory_not_minted_no_listing_host")
    assert item["nygov_names"] == ["Liquidation Bureau"]
    assert item["kjw8_agencies"] == ["Office of Renewable Energy Siting (ORES)"]


def test_bad_curation_fails_loud():
    for mutate in (
        lambda c: c["state_agencies"][0].update(primary="not-listed.ny.gov"),
        lambda c: c["state_agencies"][0].update(nygov_name="Not In Snapshot"),
        lambda c: c["state_listing_codes"].update(DOT={"target": "agency:missing"}),
        lambda c: c["state_listing_codes"].pop("DOS"),
    ):
        bad = json.loads(json.dumps(CURATION))
        mutate(bad)
        with pytest.raises(ValueError):
            _run(bad)


def _marked(*rows):
    """{norm_host: row}, as statewide_common.load_corrections returns it."""
    import statewide_common as sc
    return {sc.norm_host(r["host"]): dict({"superseded_by": "", "checked": "2026-11-18",
                                           "evidence": "fixture"}, **r) for r in rows}


def _hosts(records):
    return {w["domain"] for r in records for w in r.get("web_properties", [])
            + r.get("_on_match", {}).get("add_web_properties", [])}


def test_a_marked_primary_host_is_reviewed_never_applied():
    """A curated primary marked in website-corrections.csv is not applied: the agency is still
    minted, with no primary, and the host goes to review with the correction attached."""
    marked = _marked({"host": "www.dot.ny.gov", "issue": "lookalike", "superseded_by": "dot.gov.example"})
    records, review = build_records(LISTING, NYGOV, KJW8, CURATION, BASE, corrections=marked)
    dot = _agencies(records)["New York State Department of Transportation"]
    assert dot["_expect_new"]
    assert [(w["domain"], w["role"]) for w in dot["web_properties"]] == [("nymtc.org", "microsite")]
    item = next(i for i in review if i["reason"] == "listing_host_marked_in_website_corrections")
    assert (item["agency_key"], item["host"], item["role"]) == ("dot", "dot.ny.gov", "primary")
    assert item["marked_hosts"] == [{"host": "www.dot.ny.gov", "issue": "lookalike",
                                     "superseded_by": "dot.gov.example", "checked": "2026-11-18"}]


def test_a_marked_extra_host_is_reviewed_never_applied():
    marked = _marked({"host": "nymtc.org", "issue": "for_sale"},
                     {"host": "governor.ny.gov", "issue": "spam"})
    records, review = build_records(LISTING, NYGOV, KJW8, CURATION, BASE, corrections=marked)
    dot = _agencies(records)["New York State Department of Transportation"]
    assert [(w["domain"], w["role"]) for w in dot["web_properties"]] == [("dot.ny.gov", "primary")]
    assert "nymtc.org" not in _hosts(records)
    held = [i for i in review if i["reason"] == "listing_host_marked_in_website_corrections"]
    assert [(i["agency_key"], i["host"], i["role"]) for i in held] == [("dot", "nymtc.org", "microsite")]
    assert held[0]["marked_hosts"][0]["issue"] == "for_sale"
    assert held[0]["marked_hosts"][0]["superseded_by"] is None
    # an item already held for another reason that names a marked host is tagged too
    gov = next(i for i in review if i["reason"] == "statewide_elected_office")
    assert [m["host"] for m in gov["marked_hosts"]] == ["governor.ny.gov"]


def test_a_marked_host_for_an_existing_entity_is_not_added():
    listing = LISTING + [{"domain": "www.panynj-old.example", "url": "", "agency": "PANYNJ"},
                         {"domain": "panynj.info", "url": "", "agency": "PANYNJ"}]
    marked = _marked({"host": "panynj-old.example", "issue": "redirects_offsite"})
    records, review = build_records(listing, NYGOV, KJW8, CURATION, BASE, corrections=marked)
    pan = next(r for r in records if r.get("_match_entity_id") == "panynj")
    assert [w["domain"] for w in pan["_on_match"]["add_web_properties"]] == ["panynj.info"]
    item = next(i for i in review if i["reason"] == "listing_host_marked_in_website_corrections")
    assert (item["entity_id"], item["host"]) == ("panynj", "www.panynj-old.example")
    assert item["marked_hosts"][0]["issue"] == "redirects_offsite"


def test_unmarked_hosts_are_unaffected_by_corrections():
    """A correction for a host the listing never names changes nothing, and no item is tagged."""
    marked = _marked({"host": "unrelated.example", "issue": "spam"})
    assert build_records(LISTING, NYGOV, KJW8, CURATION, BASE, corrections=marked) == _run()
    _, review = _run()
    assert not any("marked_hosts" in i for i in review)


def test_committed_state_agencies_review_matches_the_corrections():
    """Every host held by sync_nygov for a mark is still marked, and no state agency in the
    committed registry has a marked host as a current site."""
    import statewide_common as sc
    marked = sc.load_corrections()
    review = json.loads((ROOT / "data" / "statewide_web_review.json").read_text(encoding="utf-8"))
    held = [i for i in review["state_agencies"]["items"]
            if i["reason"] == "listing_host_marked_in_website_corrections"]
    assert all(sc.norm_host(i["host"]) in marked for i in held)
    entities = json.loads((ROOT / "data" / "registry.json").read_text(encoding="utf-8"))["entities"]
    bad = [(e["id"], w["domain"]) for e in entities if e.get("government_level") == "nys"
           for w in e.get("web_properties", []) if w["role"] != "legacy" and sc.norm_host(w["domain"]) in marked]
    assert bad == []
