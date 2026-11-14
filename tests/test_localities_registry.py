"""Outcome tests on the COMMITTED locality mint (issue #25): data/registry.json, the review file,
the QA results and the leads CSV, checked against the dated source snapshots. These read
committed files only and re-derive what they check from the snapshots; they do not import the
minting code (sync_nys_localities), so a bug there cannot also hide here.
"""
import collections
import csv
import hashlib
import json
import pathlib
import re

import pytest
import qa_localities

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC13 = ROOT / "data" / "ai-readability" / "sources" / "2026-11-13"
SRC06 = ROOT / "data" / "ai-readability" / "sources" / "2026-11-06"
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((ROOT / "data" / "registry.json").read_text(encoding="utf-8"))["entities"]
REVIEW = json.loads((ROOT / "data" / "statewide_web_review.json").read_text(encoding="utf-8"))
QA = json.loads((ROOT / "data" / "statewide_localities_qa.json").read_text(encoding="utf-8"))
OSC = json.loads((SRC13 / "osc-entity-tables-general-purpose.json").read_text(encoding="utf-8"))
ITS = json.loads((SRC06 / "its-locality-websites-55k6-h6qq.json").read_text(encoding="utf-8"))
with open(ROOT / "data" / "ai-readability" / "localities-website-leads-2026-11-13.csv", newline="",
          encoding="utf-8") as _fh:
    LEADS = list(csv.DictReader(_fh))

LEVELS = ("county", "city", "town", "village")
SHEET = {"county": "counties", "city": "cities", "town": "towns", "village": "villages"}
NEW = [e for e in REGISTRY if e["government_level"] in LEVELS]
OLD = [e for e in REGISTRY if e["government_level"] not in LEVELS]
ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
DAS = {"manhattan-district-attorney-s-office": "36061", "bronx-district-attorney-s-office": "36005",
       "brooklyn-district-attorney-s-office": "36047", "queens-district-attorney-s-office": "36081",
       "staten-island-district-attorney-s-office": "36085"}
# SHA-256 of the 567 entities on main before this mint (canonical JSON, sort_keys), 2026-11-13.
OLD_HASH = "deceeec9fe88777e957490fe359238b2e920499bce03c4fbb839922f33a9f583"


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def code(e, scheme):
    return next((i["identifier"] for i in e["identifiers"] if i["scheme"] == scheme), None)


def osc_rows():
    """OSC rows outside NYC, keyed by municipal code -> (level, row)."""
    out = {}
    for lv in LEVELS:
        for r in OSC[SHEET[lv]]:
            if lv == "city" and r["name"] == "City of New York City":
                continue
            out[r["municipal_code"]] = (lv, r)
    return out


def expected_name(level, osc_name):
    bare = re.sub(r"^(?:(?:County|City|Town|Village) of )+", "", osc_name)
    return f"{bare} County" if level == "county" else f"{level.capitalize()} of {bare}"


def test_counts_by_level_and_total():
    assert collections.Counter(e["government_level"] for e in NEW) == {"county": 57, "city": 61,
                                                                        "town": 933, "village": 532}
    assert len(OLD) == 567 and len(REGISTRY) == 567 + 1583


def test_osc_snapshot_counts():
    assert {k: len(OSC[k]) for k in SHEET.values()} == {"counties": 57, "cities": 62, "towns": 933, "villages": 532}


def test_every_entity_is_one_osc_row_exactly_and_every_osc_row_is_minted_once():
    rows = osc_rows()
    used = collections.Counter()
    for e in NEW:
        c = code(e, "nys_osc_muni_code")
        assert c in rows, e["id"]
        level, row = rows[c]
        assert level == e["government_level"], e["id"]
        assert e["name"] == expected_name(level, row["name"]), (e["id"], row["name"])
        assert "osc" in e["provenance"]["sources"]
        used[c] += 1
    assert set(used) == set(rows) and max(used.values()) == 1


def test_nyc_counties_and_city_not_minted():
    names = {e["name"] for e in NEW}
    for n in ("Bronx County", "Kings County", "New York County", "Queens County", "Richmond County",
              "City of New York", "City of New York City"):
        assert n not in names


def test_ids_unique_slug_valid_and_follow_the_collision_rule():
    ids = [e["id"] for e in REGISTRY]
    assert len(ids) == len(set(ids))
    rows = osc_rows()
    name_count = collections.Counter((e["government_level"], e["name"]) for e in NEW)
    for e in NEW:
        assert ID_RE.match(e["id"])
        county = rows[code(e, "nys_osc_muni_code")][1]["county"]
        if name_count[(e["government_level"], e["name"])] == 1:
            assert e["id"] == slug(e["name"]), e["id"]
        else:
            assert e["id"] == f"{slug(e['name'])}-{slug(county)}-county", e["id"]
    assert {"albany-county", "st-lawrence-county", "city-of-albany", "town-of-colonie",
            "village-of-menands", "town-of-greenville-greene-county",
            "town-of-greenville-orange-county"} <= set(ids)


def test_town_and_village_of_one_place_are_two_entities():
    by_id = {e["id"]: e for e in NEW}
    for place in ("harrison", "scarsdale", "mount-kisco", "east-rochester", "green-island"):
        assert by_id[f"town-of-{place}"]["government_level"] == "town"
        assert by_id[f"village-of-{place}"]["government_level"] == "village"
        assert code(by_id[f"town-of-{place}"], "nys_osc_muni_code") != code(by_id[f"village-of-{place}"],
                                                                            "nys_osc_muni_code")


def test_schema_valid():
    jsonschema = pytest.importorskip("jsonschema")
    for e in NEW:
        jsonschema.validate(e, SCHEMA)


def test_codes_come_from_one_its_row_of_the_same_type_and_name():
    its = {r["swis_code"]: r for r in ITS}
    typ = {"county": "County", "city": "City", "town": "Town", "village": "Village"}
    for e in NEW:
        swis = code(e, "nys_swis")
        if swis is None:
            continue
        r = its[swis]
        assert r["type"] == typ[e["government_level"]], e["id"]
        name = r["county"] if e["government_level"] == "county" else r["municipality"]
        assert qa_localities.key(name) == qa_localities.key(e["name"], "county"), e["id"]
        gnis = code(e, "us_gnis")
        assert gnis is None or gnis == r["gnis_id"], e["id"]
    swis_all = [code(e, "nys_swis") for e in REGISTRY if code(e, "nys_swis")]
    gnis_all = [code(e, "us_gnis") for e in REGISTRY if code(e, "us_gnis")]
    assert len(swis_all) == len(set(swis_all)) and len(gnis_all) == len(set(gnis_all))
    assert "334005" not in swis_all  # Kiryas Joel's stale ITS code is held


def test_its_and_census_match_rates():
    # Floors set from the 2026-11-13 run, just under it: ITS 99.4% (villages) and Census 98.4%
    # (cities, where Sherrill is absent from sxhg-qquj). Every miss is named in the QA report; a
    # new miss would show up as a lower rate or a longer unmatched list below.
    qa = qa_localities.run()
    for lv in LEVELS:
        assert qa["its"]["by_level"][lv]["rate"] >= 0.99, lv
        assert qa["census"]["by_level"][lv]["rate"] >= 0.98, lv
    assert qa == {k: QA[k] for k in qa}, "data/statewide_localities_qa.json is stale; rerun qa_localities.py"
    assert sorted(x["entity_id"] for x in qa["its"]["entities_unmatched"]) == [
        "town-of-fallsburg", "town-of-palm-tree", "village-of-ateres", "village-of-bloomingburg",
        "village-of-kiryas-joel"]
    assert sorted(x["name"] for x in qa["census"]["entities_unmatched"]) == [
        "City of Sherrill", "Town of East Rochester", "Town of Mount Kisco", "Town of Scarsdale",
        "Village of Ateres"]
    assert qa["osc"]["problems"] == [] and qa["osc"]["osc_rows_not_minted"] == []


def test_every_mismatch_is_in_the_review_file():
    loc = REVIEW["localities"]["items"]
    flagged = {i["proposed_id"] for i in loc if i["reason"] in ("its_no_match", "its_match_ambiguous",
                                                                  "its_swis_known_stale")}
    assert {x["entity_id"] for x in QA["its"]["entities_unmatched"]} <= flagged
    unmatched_rows = {i["its_row"]["swis_code"] for i in loc if i["reason"] in ("its_row_unmatched",
                                                                                 "its_swis_known_stale")}
    assert {x["swis_code"] for x in QA["its"]["its_rows_unmatched"]} <= unmatched_rows
    cen = REVIEW["localities_census_qa"]["items"]
    assert {x["entity_id"] for x in QA["census"]["entities_unmatched"]} == \
        {i["entity_id"] for i in cen if i["reason"] == "census_no_entity_match"}
    assert {x["census_name"] for x in QA["census"]["census_rows_unmatched"]} == \
        {i["census_name"] for i in cen if i["reason"] == "census_row_no_entity"}
    for section in ("localities", "localities_census_qa"):
        assert all(i.get("reason") for i in REVIEW[section]["items"])


CHECKS = json.loads((ROOT / "data" / "locality_website_checks.json").read_text(encoding="utf-8"))["checks"]


def test_no_primary_without_two_agreeing_sources_or_a_scan_check():
    cisa = {}
    with open(SRC06 / "cisa-dotgov-current-full.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["State"] == "NY":
                cisa[r["Domain name"].lower()] = r["Organization name"]
    checked = {(c["entity_id"], c["host"], c["role"]) for c in CHECKS}
    for e in NEW:
        prim = [w for w in e["web_properties"] if w["role"] == "primary"]
        assert len(prim) <= 1, e["id"]
        for w in e["web_properties"]:
            if w["role"] == "legacy":
                assert (e["id"], w["domain"], "legacy") in checked, e["id"]
        if not prim:
            assert e["web_properties"] == [], e["id"]
            continue
        notes = prim[0]["notes"]
        if notes.startswith("Confirmed:"):
            assert (e["id"], prim[0]["domain"], "primary") in checked, e["id"]
            continue
        srcs = re.search(r"\[([a-z, ]+)\]", notes).group(1).split(", ")
        assert len(set(srcs)) >= 2 and set(srcs) <= {"its", "dotgov", "grounding"}, e["id"]
        host = re.sub(r"^www\d*\.", "", prim[0]["domain"])
        if "dotgov" in srcs:
            assert host in cisa, e["id"]
        if "its" in srcs:
            assert "55k6-h6qq" in notes and host in notes, e["id"]
        if "grounding" in srcs:
            assert "grounding-ai-with-ny-open-data" in notes, e["id"]


def test_checks_file_names_only_real_entities_and_hosts_and_nothing_else():
    by_id = {e["id"]: e for e in NEW}
    allowed = {"entity_id", "host", "role", "checked_on", "method"}
    primaries = collections.Counter()
    for c in CHECKS:
        assert set(c) == allowed, c
        assert c["role"] in ("primary", "legacy")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["checked_on"])
        assert c["method"] == "page title names the place (BetaNYC AI-readability scan)"
        e = by_id[c["entity_id"]]
        assert {"domain": c["host"], "role": c["role"]}.items() <= next(
            w for w in e["web_properties"] if w["domain"] == c["host"]).items()
        primaries[c["entity_id"]] += c["role"] == "primary"
    assert max(primaries.values()) == 1
    hosts = [re.sub(r"^www\d*\.", "", w["domain"]) for e in REGISTRY for w in e["web_properties"]
             if w["role"] == "primary" and e["government_level"] in LEVELS]
    assert len(hosts) == len(set(hosts)), "a primary host is shared between places"


def test_scan_results_are_not_referenced_or_committed():
    import subprocess
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    # Built from parts so this file does not match itself.
    needles = ("ai-readability-" + "private", "leads-with-" + "scan-results")
    for f in tracked:
        assert not any(n in f for n in needles), f
        p = ROOT / f
        if p.suffix in {".py", ".md", ".json", ".csv", ".txt", ".toml", ".yml", ".yaml"} and p.is_file():
            text = p.read_text(encoding="utf-8", errors="ignore")
            assert not any(n in text for n in needles), f
    for c in CHECKS:  # only the address and how it was confirmed: no scan outcome
        assert not ({"title", "status", "default_body", "chrome_body", "error"} & set(c))


def test_leads_csv_one_host_per_row_and_only_places_without_a_primary():
    assert list(LEADS[0]) == ["entity_id", "name", "county", "level", "host", "source"]
    by_id = {e["id"]: e for e in NEW}
    keys = [(r["entity_id"], r["host"], r["source"]) for r in LEADS]
    assert len(keys) == len(set(keys))
    for r in LEADS:
        e = by_id[r["entity_id"]]
        assert e["web_properties"] == [], r["entity_id"]
        assert re.fullmatch(r"[a-z0-9.-]+", r["host"]), r
        assert r["source"] in {"its", "dotgov", "grounding"} and r["level"] == e["government_level"]


def test_existing_entities_unchanged_except_the_five_da_areas():
    stripped = []
    for e in OLD:
        e = json.loads(json.dumps(e))
        if e["id"] in DAS:
            assert e["areas"] == [{"scheme": "us_census_geoid", "layer": "county", "id": DAS[e["id"]],
                                   "role": "jurisdiction"}]
            e["areas"] = []
            e["provenance"]["sources"].remove("manual")
        stripped.append(e)
    digest = hashlib.sha256(json.dumps(stripped, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert digest == OLD_HASH
