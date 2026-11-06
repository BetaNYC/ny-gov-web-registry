"""Outcome tests on the COMMITTED statewide mint (issue #24): data/registry.json,
data/curation.json, data/statewide_web_review.json, against the dated source snapshots in
data/ai-readability/sources/2026-11-06/. Offline; reads committed files only."""
import collections
import csv
import json
import pathlib
import re

import pytest

from build_registry import _norm, _slugify

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "ai-readability" / "sources" / "2026-11-06"
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((ROOT / "data" / "registry.json").read_text(encoding="utf-8"))["entities"]
CURATION = json.loads((ROOT / "data" / "curation.json").read_text(encoding="utf-8"))
REVIEW = json.loads((ROOT / "data" / "statewide_web_review.json").read_text(encoding="utf-8"))
ABO = json.loads((SRC / "abo-directory-4vym-q77x.json").read_text(encoding="utf-8"))
with open(SRC / "state-agency-listing-websites.csv", newline="", encoding="utf-8") as _fh:
    LISTING = list(csv.DictReader(_fh))

STATE_LOCAL = [r["public_authority_name"].strip() for r in ABO if r["public_authority_type"] in ("State", "Local")]
IDA_LDC = [r["public_authority_name"].strip() for r in ABO if r["public_authority_type"] in ("IDA", "LDC")]


def _names(e):
    return {_norm(e["name"])} | {_norm(o["name"]) for o in e.get("other_names", [])}


def test_snapshot_has_156_state_and_local_rows():
    c = collections.Counter(r["public_authority_type"] for r in ABO)
    assert (c["State"], c["Local"], c["IDA"], c["LDC"]) == (45, 111, 106, 346)
    assert len(STATE_LOCAL) == 156 == len(set(STATE_LOCAL))


def test_all_156_state_and_local_authorities_are_in_the_registry_once():
    abo_entities = [e for e in REGISTRY if "abo" in e.get("provenance", {}).get("sources", [])]
    assert len(abo_entities) == 156
    held = {h["abo_name"] for h in CURATION.get("abo_holds", [])}
    for name in STATE_LOCAL:
        if name in held:
            continue
        owners = [e["id"] for e in abo_entities if _norm(name) in _names(e)]
        assert len(owners) == 1, (name, owners)


def test_no_ida_or_ldc_was_minted():
    abo_names = set().union(*(_names(e) for e in REGISTRY if "abo" in e["provenance"]["sources"]))
    assert not [n for n in IDA_LDC if _norm(n) in abo_names]
    assert not [e for e in REGISTRY if "abo" in e["provenance"]["sources"]
                and e.get("classification") in {"industrial-development-agency", "local-development-corporation"}
                and e["id"] != "nycedc"]


def test_matched_authorities_kept_their_level():
    by_id = {e["id"]: e for e in REGISTRY}
    for m in CURATION["abo_matches"]:
        e = by_id[m["entity_id"]]
        assert "abo" in e["provenance"]["sources"]
        assert e["government_level"] in {"nyc", "authority", "pbc", "bi-state"}
    # NYC-local authorities stay single (no state-level twin minted).
    for name in ("New York City Health and Hospitals Corporation", "New York City School Construction Authority",
                 "New York City Housing Development Corporation"):
        assert sum(_norm(name) in _names(e) for e in REGISTRY) == 1, name


def test_counts_by_level():
    c = collections.Counter(e["government_level"] for e in REGISTRY)
    assert c["nys"] == 53
    assert c["authority"] == 150  # 12 before + 138 minted from ABO
    assert len(REGISTRY) == 567


def test_every_entity_is_schema_valid_and_ids_unique():
    jsonschema = pytest.importorskip("jsonschema")
    for e in REGISTRY:
        jsonschema.validate(e, SCHEMA)
    ids = [e["id"] for e in REGISTRY]
    assert len(ids) == len(set(ids))


def test_statewide_ids_are_plain_slugs_of_their_names():
    # A collision would have produced a '-2' suffix; every statewide id must be its own name's slug.
    for e in REGISTRY:
        if e["government_level"] == "nys" or (e["government_level"] == "authority"
                                              and e["provenance"]["sources"][0] == "abo"):
            assert e["id"] == _slugify(e["name"]), e["id"]


def test_web_properties_are_hosts_not_urls():
    for e in REGISTRY:
        for w in e["web_properties"]:
            assert "/" not in w["domain"] and ":" not in w["domain"], (e["id"], w["domain"])
            assert w["domain"] == w["domain"].lower()


def test_every_agency_has_one_primary_and_no_verified_identifier_invented():
    for e in REGISTRY:
        if e["government_level"] != "nys":
            continue
        assert [w["role"] for w in e["web_properties"]].count("primary") == 1, e["id"]
        assert e["identifiers"] == []
        assert e["provenance"]["sources"][0] == "state_listing"


def test_every_listing_code_is_curated_and_every_host_accounted_for():
    codes = CURATION["state_listing_codes"]
    assert {r["agency"] for r in LISTING} == set(codes)
    owned = {w["domain"] for e in REGISTRY for w in e["web_properties"]}
    owned_norm = {re.sub(r"^www\d*\.", "", d) for d in owned}
    reviewed = {h for i in REVIEW["state_agencies"]["items"] for h in i.get("hosts", [])}
    for r in LISTING:
        assert r["domain"] in owned_norm or r["domain"] in reviewed, r["domain"]


def test_review_file_items_all_carry_a_reason():
    for section in ("authorities", "state_agencies"):
        for item in REVIEW[section]["items"]:
            assert item.get("reason"), item


def test_legislature_elected_offices_and_courts_are_not_minted():
    hosts = {w["domain"] for e in REGISTRY for w in e["web_properties"]}
    for h in ("assembly.ny.gov", "governor.ny.gov", "ag.ny.gov", "nycourts.gov", "legethics.ny.gov"):
        assert h not in hosts, h
