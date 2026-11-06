"""Offline tests for sync_abo.build_records (ABO State + Local authorities) and its seam with
build_registry. In-memory fixtures only; no file IO, no network.

Regression: before 2026-11-06 sync_abo emitted a v1-shaped record (`crosswalk`, `jurisdiction`,
`entity_type`, `_source_address`) that the v2 schema (additionalProperties: false) rejects, so the
first real ABO build would have failed validation. test_minted_records_validate_against_schema
would have caught it.
"""
import json
import pathlib

import pytest

from build_registry import build_entities
from sync_abo import build_records, website_url

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))


def _row(name, atype="State", url=None, **addr):
    r = {"public_authority_type": atype, "public_authority_name": name}
    if url is not None:
        r["website"] = {"url": url}
    r.update(addr)
    return r


def _entity(id_, name, domains=(), level="authority", other_names=()):
    return {"id": id_, "name": name, "government_level": level,
            "other_names": [{"name": n, "note": None} for n in other_names],
            "web_properties": [{"domain": d, "role": "primary"} for d in domains],
            "identifiers": [], "status": "active",
            "provenance": {"sources": ["manual"], "last_verified": None}}


BASE = [
    _entity("mta", "Metropolitan Transportation Authority", ["mta.info"]),
    _entity("esd", "Empire State Development", ["esd.ny.gov"],
            other_names=["New York State Urban Development Corporation"]),
    _entity("school-construction-authority", "School Construction Authority", ["www.nycsca.org"], level="nyc"),
    _entity("nyserda", "New York State Energy Research and Development Authority", ["nyserda.ny.gov"]),
]

CURATION = {
    "abo_matches": [
        {"abo_name": "New York City School Construction Authority",
         "entity_id": "school-construction-authority", "note": "fixture"},
        {"abo_name": "New York State Urban Development Corporation", "entity_id": "esd", "note": "fixture"},
    ],
    "abo_holds": [],
    "abo_website_holds": [{"abo_name": "Victor Urban Renewal Agency",
                           "reason": "abo_host_is_a_municipal_site", "note": "town site"}],
    "state_listing_codes": {
        "BPCA": {"target": "abo:Battery Park City Authority", "primary": "bpca.ny.gov"},
        "NYSBA": {"target": "abo:New York State Bridge Authority", "primary": "nysba.ny.gov"},
        "ESD": {"target": "abo:New York State Urban Development Corporation", "primary": "esd.ny.gov"},
        "DOH": {"target": "agency:health"},
    },
    "state_listing_host_overrides": {},
}

LISTING = [
    {"domain": "bpca.ny.gov", "url": "https://bpca.ny.gov", "agency": "BPCA"},
    {"domain": "nysba.ny.gov", "url": "https://nysba.ny.gov", "agency": "NYSBA"},
    {"domain": "esd.ny.gov", "url": "https://esd.ny.gov", "agency": "ESD"},
    {"domain": "taste.ny.gov", "url": "https://taste.ny.gov", "agency": "ESD"},
    {"domain": "health.ny.gov", "url": "https://health.ny.gov", "agency": "DOH"},
]

ROWS = [
    _row("Metropolitan Transportation Authority", url="http://www.mta.info"),
    _row("New York State Urban Development Corporation", url="http://www.esd.ny.gov/"),
    _row("New York City School Construction Authority", "Local", url="http://www.nycsca.org"),
    _row("New York State Energy Research and Development Authority", url="http://www.nyserda.org"),
    _row("Battery Park City Authority", url="http://www.bpca.ny.gov",
         address_line_1="200 Liberty Street", city="NEW YORK", state="NY", zip="10281"),
    _row("New York State Bridge Authority", url="http://www.nysba.state.ny.us"),
    _row("Buffalo Sewer Authority", "Local", url="https://buffalosewer.org/"),
    _row("Housing Trust Fund Corporation", url="http://www.nyshcr.org/HTFC/"),
    _row("New York State Housing Finance Agency", url="http://www.nyshcr.org"),
    _row("State of New York Mortgage Agency", url="http://www.nyshcr.org/"),
    _row("Livingston County Water and Sewer Authority", "Local", url="http://wwww.LCWSA.us"),
    _row("Yonkers Community Development Agency", "Local", url="http://www.cityofyonkers"),
    _row("Victor Urban Renewal Agency", "Local", url="http://victorny.org"),
    _row("Albany County Pine Hills Land Authority", "Local"),
    _row("Some County Industrial Development Agency", "IDA", url="http://ida.example.org"),
    _row("Some Local Development Corporation", "LDC", url="http://ldc.example.org"),
]

TLDS = {"org", "gov", "us", "info", "com"}


def _run():
    return build_records(ROWS, LISTING, CURATION, BASE, TLDS)


def _by_name(records):
    return {r["name"]: r for r in records}


def test_only_state_and_local_rows_are_emitted():
    records, _ = _run()
    names = {r["name"] for r in records}
    assert "Some County Industrial Development Agency" not in names
    assert "Some Local Development Corporation" not in names
    assert len(records) == len(ROWS) - 2


def test_website_accepts_socrata_object_and_csv_string():
    assert website_url({"website": {"url": " http://x.org "}}) == "http://x.org"
    assert website_url({"website": "http://x.org"}) == "http://x.org"
    assert website_url({}) == ""


def test_minted_records_validate_against_schema():
    jsonschema = pytest.importorskip("jsonschema")
    records, _ = _run()
    entities, _ = build_entities(BASE, [records])
    minted = [e for e in entities if e["id"] not in {b["id"] for b in BASE}]
    assert minted, "fixture should mint"
    for e in minted:
        assert not any(k.startswith("_") for k in e), e
        jsonschema.validate(e, SCHEMA)
        assert e["government_level"] == "authority"
        assert e["classification"] in {"state-authority", "local-authority"}
        assert e["identifiers"] == [], "ABO has no id column; no identifier may be invented"


def test_exact_name_and_curated_matches_enrich_instead_of_minting():
    records, _ = _run()
    entities, _ = build_entities(BASE, [records])
    names = [e["name"] for e in entities]
    assert names.count("Metropolitan Transportation Authority") == 1
    assert "New York City School Construction Authority" not in names
    assert "New York State Urban Development Corporation" not in names
    sca = next(e for e in entities if e["id"] == "school-construction-authority")
    assert sca["government_level"] == "nyc", "a match never changes the existing level"
    assert "abo" in sca["provenance"]["sources"]
    assert "New York City School Construction Authority" in {o["name"] for o in sca["other_names"]}
    esd = next(e for e in entities if e["id"] == "esd")
    assert [d["domain"] for d in esd["web_properties"]] == ["esd.ny.gov", "taste.ny.gov"]
    assert esd["web_properties"][1]["role"] == "microsite"
    # The name was already curated on esd; its existing note must not be replaced or duplicated.
    assert [o["name"] for o in esd["other_names"]].count("New York State Urban Development Corporation") == 1


def test_root_url_becomes_host_only_primary_and_address_is_kept():
    rec = _by_name(_run()[0])["Buffalo Sewer Authority"]
    assert rec["web_properties"][0]["domain"] == "buffalosewer.org"
    assert rec["web_properties"][0]["role"] == "primary"
    bpca = _by_name(_run()[0])["Battery Park City Authority"]
    assert bpca["contact_details"][0]["value"] == "200 Liberty Street, NEW YORK, NY 10281"


def test_path_only_presence_goes_to_url_conventions_not_a_domain():
    rec = _by_name(_run()[0])["Housing Trust Fund Corporation"]
    assert rec["web_properties"] == []
    assert rec["url_conventions"][0]["pattern"] == "www.nyshcr.org/HTFC/"


def test_listing_primary_wins_and_differing_abo_host_goes_to_review():
    records, review = _run()
    bpca = _by_name(records)["Battery Park City Authority"]
    assert [w["domain"] for w in bpca["web_properties"]] == ["bpca.ny.gov"]
    assert "agrees with the ABO" in bpca["web_properties"][0]["notes"]
    assert "state_listing" in bpca["provenance"]["sources"]
    bridge = _by_name(records)["New York State Bridge Authority"]
    assert [w["domain"] for w in bridge["web_properties"]] == ["nysba.ny.gov"]
    assert {"abo_name": "New York State Bridge Authority", "reason": "abo_host_differs_from_state_listing"}.items() \
        <= next(i for i in review if i.get("abo_name") == "New York State Bridge Authority").items()


def test_held_malformed_and_shared_hosts_are_reviewed_never_applied():
    records, review = _run()
    by = _by_name(records)
    reasons = {(i["abo_name"], i["reason"]) for i in review}
    for name in ("New York State Housing Finance Agency", "State of New York Mortgage Agency"):
        assert by[name]["web_properties"] == []
        assert (name, "abo_host_shared_by_several_authorities") in reasons
    for name in ("Livingston County Water and Sewer Authority", "Yonkers Community Development Agency"):
        assert by[name]["web_properties"] == []
        assert (name, "abo_website_malformed") in reasons
    assert by["Victor Urban Renewal Agency"]["web_properties"] == []
    assert ("Victor Urban Renewal Agency", "abo_host_is_a_municipal_site") in reasons
    assert ("New York State Energy Research and Development Authority",
            "abo_host_differs_from_existing_entity") in reasons


def test_no_website_still_mints_with_no_property():
    rec = _by_name(_run()[0])["Albany County Pine Hills Land Authority"]
    assert rec["web_properties"] == [] and rec["url_conventions"] == []


def test_stale_curation_fails_loud():
    bad = dict(CURATION, abo_matches=[{"abo_name": "No Such Authority", "entity_id": "mta"}])
    with pytest.raises(ValueError):
        build_records(ROWS, LISTING, bad, BASE, TLDS)
    bad = dict(CURATION, abo_matches=[{"abo_name": "Buffalo Sewer Authority", "entity_id": "gone"}])
    with pytest.raises(ValueError):
        build_records(ROWS, LISTING, bad, BASE, TLDS)


def test_uncurated_listing_code_fails_loud():
    listing = LISTING + [{"domain": "new.ny.gov", "url": "", "agency": "NEWCODE"}]
    with pytest.raises(ValueError):
        build_records(ROWS, listing, CURATION, BASE, TLDS)


def test_rerun_is_idempotent():
    assert _run() == _run()
    records, _ = _run()
    once, _ = build_entities(BASE, [records])
    twice, _ = build_entities(BASE, [records])
    assert once == twice
