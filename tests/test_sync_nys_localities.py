"""Offline tests for sync_nys_localities.build_records (counties, cities, towns, villages; issue #23)
and its seam with build_registry. In-memory fixtures only: no file IO beyond the committed Public
Suffix List and schema, no network.

The fixture is a small New York: the five NYC counties and the City of New York (never minted),
two upstate counties, one city, two Towns of Greenville in different counties (an id collision),
a Town and a Village of Harrison (one place, two governments), the Town of Palm Tree (absent from
the 2023 ITS list), and a village that only the stale ITS list still carries (dissolved).
"""
import copy
import json
import pathlib

import ai_readability_export as ex
import pytest
import statewide_common as sc
from build_registry import build_entities
from sync_nys_localities import build_records, name_key, official_name

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "property.schema.json").read_text(encoding="utf-8"))
PSL = ex.load_psl()
TLDS = sc.psl_tlds()


def _county(name, swis, fips, nyc="N", gnis=None):
    return {"NAME": name, "SWIS": swis, "GNIS_ID": gnis or f"9{swis}", "FIPS_CODE": fips, "NYC": nyc}


def _place(name, county, swis, fips=None, gnis=None):
    return {"NAME": name, "COUNTY": county, "SWIS": swis, "GNIS_ID": gnis or f"9{swis}", "FIPS_CODE": fips}


GIS = {
    "county": [
        _county("Bronx", "600000", "36005", "Y"), _county("Kings", "610000", "36047", "Y"),
        _county("New York", "620000", "36061", "Y"), _county("Queens", "630000", "36081", "Y"),
        _county("Richmond", "640000", "36085", "Y"),
        _county("Greene", "190000", "36039", gnis="974117"), _county("Orange", "330000", "36071"),
        _county("Westchester", "550000", "36119"), _county("St Lawrence", "400000", "36089"),
    ],
    "city": [_place("New York", "New York", "650000"), _place("Middletown", "Orange", "331100")],
    "town": [
        _place("Greenville", "Greene", "192400", "3603930968"),
        _place("Greenville", "Orange", "333400", "3607130990"),
        _place("Harrison", "Westchester", "553400", "3611932765"),
        _place("Palm Tree", "Orange", "336000", "3607156060"),
        _place("Canton", "St Lawrence", "402000", "3608912078"),
    ],
    "village": [_place("Harrison", "Westchester", "553401"), _place("De Ruyter", "Orange", "336400")],
}
EXPECTED = {"county": 9, "city": 2, "town": 5, "village": 2}

OSC = {
    "counties": [{"municipal_code": "190100000000", "name": "County of Greene", "county": None},
                 {"municipal_code": "330100000000", "name": "County of Orange", "county": None},
                 {"municipal_code": "550100000000", "name": "County of Westchester", "county": None},
                 {"municipal_code": "400100000000", "name": "County of St. Lawrence", "county": None}],
    "cities": [{"municipal_code": "600234000000", "name": "City of New York City", "county": "New York City"},
               {"municipal_code": "330225000000", "name": "City of Middletown", "county": "Orange"}],
    "towns": [{"municipal_code": "190324000000", "name": "Town of Greenville", "county": "Greene"},
              {"municipal_code": "330334000000", "name": "Town of Greenville", "county": "Orange"},
              {"municipal_code": "550334000000", "name": "Town of Harrison", "county": "Westchester"},
              {"municipal_code": "330363650000", "name": "Town of Palm Tree", "county": "Orange"},
              # Two OSC rows for one GIS place: ambiguous, so no code is applied.
              {"municipal_code": "400302000000", "name": "Town of Canton", "county": "St. Lawrence"},
              {"municipal_code": "400302000001", "name": "Town of Canton", "county": "St. Lawrence"}],
    "villages": [{"municipal_code": "550434000000", "name": "Village of Harrison", "county": "Westchester"},
                 {"municipal_code": "330423000000", "name": "Village of DeRuyter", "county": "Orange"}],
}


def _its(swis, typ, name, county, url):
    return {"swis_code": swis, "type": typ, "municipality": name, "county": county,
            "website": {"url": url} if url else None}


ITS = [
    _its("600000", "Borough/County", "Bronx", "Bronx", None),
    _its("650000", "City", "New York City", "New York", "http://www.nyc.gov"),
    _its("190000", "County", "Greene", "Greene", "http://www.greenegovernment.com"),
    _its("330000", "County", "Orange", "Orange", "http://www.orangecountygov.com/index.jsp"),
    _its("331100", "City", "Middletown", "Orange", "http://www.middletown-ny.com"),
    _its("192400", "Town", "Greenville", "Greene", "http://www.townofgreenvilleny.gov"),
    _its("553400", "Town", "Harrison", "Westchester", "http://www.harrison-ny.gov"),
    _its("553401", "Village", "Harrison", "Westchester", "http://www.harrison-ny.gov"),
    _its("336400", "Village", "De Ruyter", "Orange", "http://www.orangecountygov.com/villages/deruyter/"),
    # Dissolved: in the 2023 ITS list, not in GIS. Never minted.
    _its("339999", "Village", "Gone", "Orange", "http://www.villageofgone.org"),
]

DOTGOV = [
    # Greene County: CISA + ITS name different hosts -> disagree.
    {"domain": "greenecountyny.gov", "tier": "county", "name": "Greene", "organization": "Greene County"},
    # Orange County: CISA agrees with ITS (whose /index.jsp counts as the site root).
    {"domain": "orangecountygov.com", "tier": "county", "name": "Orange", "organization": "Orange County"},
    # Two Towns of Greenville: the CISA lead is ambiguous between them.
    {"domain": "townofgreenvilleny.gov", "tier": "town", "name": "Greenville", "organization": "Town of Greenville"},
    {"domain": "harrison-ny.gov", "tier": "town", "name": "Harrison", "organization": "Town of Harrison"},
    {"domain": "villageofharrison.gov", "tier": "village", "name": "Harrison", "organization": "Village of Harrison"},
    {"domain": "palmtreeny.gov", "tier": "town", "name": "Palm Tree", "organization": "Town of Palm Tree"},
    {"domain": "nowhereny.gov", "tier": "town", "name": "Nowhere", "organization": "Town of Nowhere"},
]

GROUNDING = [
    {"entity": "Westchester County", "entity_type": "county", "url": "https://gis.westchestercountyny.gov/",
     "status": "live"},
    {"entity": "Orange County", "entity_type": "county", "url": "https://gis.orangecountygov.com/portal/apps/sites/",
     "status": "live"},
    {"entity": "City of Middletown", "entity_type": "city", "url": "https://www.middletown-ny.com/", "status": "no-portal"},
    {"entity": "St. Lawrence County", "entity_type": "county", "url": "https://maps.dancgis.org/ima/", "status": "no-portal"},
    {"entity": "New York City", "entity_type": "city", "url": "https://data.cityofnewyork.us/", "status": "live"},
]

BASE = [{"id": "bronx-borough-president", "name": "Bronx Borough President", "government_level": "nyc",
         "web_properties": [{"domain": "bronxboropres.nyc.gov", "role": "primary"}], "identifiers": [],
         "status": "active", "provenance": {"sources": ["manual"], "last_verified": None}}]


def run(gis=GIS, osc=OSC, its=ITS, dotgov=DOTGOV, grounding=GROUNDING, base=BASE, expected=EXPECTED):
    return build_records(copy.deepcopy(gis), osc, its, dotgov, grounding, base, TLDS, PSL, expected_rows=expected)


def by_id(records):
    return {r["_mint_id"]: r for r in records}


def review_for(review, pid, reason=None):
    return [i for i in review if i.get("proposed_id") == pid and (reason is None or i["reason"] == reason)]


def test_names_and_name_key():
    assert official_name("county", "St Lawrence") == "St Lawrence County"
    assert official_name("village", "Menands") == "Village of Menands"
    assert name_key("De Ruyter") == name_key("Village of DeRuyter")
    assert name_key("St. Lawrence") == name_key("St Lawrence") == name_key("Saint Lawrence County")
    assert name_key("Village of Village of The Branch") == name_key("The Branch")
    assert name_key("Mt Vernon") == name_key("Mount Vernon")


def test_nyc_counties_and_city_are_not_minted():
    recs, _, summary = run()
    ids = set(by_id(recs))
    assert not {"bronx-county", "kings-county", "new-york-county", "queens-county", "richmond-county",
                "city-of-new-york"} & ids
    assert summary["units"] == {"county": 4, "city": 1, "town": 5, "village": 2}


def test_ids_follow_the_plan_and_collisions_get_the_county_on_every_member():
    recs, _, summary = run()
    ids = by_id(recs)
    assert {"greene-county", "orange-county", "st-lawrence-county", "city-of-middletown",
            "town-of-harrison", "village-of-harrison", "town-of-palm-tree"} <= set(ids)
    assert "town-of-greenville" not in ids
    assert {"town-of-greenville-greene-county", "town-of-greenville-orange-county"} <= set(ids)
    assert summary["name_collisions"] == ["town-of-greenville-greene-county", "town-of-greenville-orange-county"]
    assert ids["town-of-greenville-greene-county"]["name"] == "Town of Greenville"


def test_town_and_village_of_one_place_are_two_entities():
    ids = by_id(run()[0])
    town, village = ids["town-of-harrison"], ids["village-of-harrison"]
    assert (town["government_level"], village["government_level"]) == ("town", "village")
    assert {"scheme": "nys_swis", "identifier": "553400"} in town["identifiers"]
    assert {"scheme": "nys_swis", "identifier": "553401"} in village["identifiers"]


def test_palm_tree_is_minted_from_gis_and_osc_though_its_lacks_it():
    pt = by_id(run()[0])["town-of-palm-tree"]
    assert {"scheme": "nys_osc_muni_code", "identifier": "330363650000"} in pt["identifiers"]
    assert pt["provenance"]["sources"] == ["nys_gis", "osc"]


def test_dissolved_village_in_its_only_is_not_minted():
    recs, review, _ = run()
    assert not [r for r in recs if "gone" in r["_mint_id"]]
    item = [i for i in review if i["reason"] == "its_row_not_in_gis"]
    assert [i["its_row"]["swis_code"] for i in item] == ["339999"]


def test_osc_code_only_on_a_one_to_one_county_and_name_match():
    recs, review, summary = run()
    ids = by_id(recs)
    schemes = {s["scheme"] for s in ids["town-of-canton"]["identifiers"]}
    assert "nys_osc_muni_code" not in schemes
    amb = review_for(review, "town-of-canton", "osc_match_ambiguous")
    assert len(amb) == 1 and len(amb[0]["osc_rows"]) == 2
    # Spelling differences still match (De Ruyter / DeRuyter, St Lawrence / St. Lawrence).
    assert {"scheme": "nys_osc_muni_code", "identifier": "330423000000"} in ids["village-of-de-ruyter"]["identifiers"]
    assert {"scheme": "nys_osc_muni_code", "identifier": "400100000000"} in ids["st-lawrence-county"]["identifiers"]
    # The colliding Greenvilles are told apart by county.
    assert {"scheme": "nys_osc_muni_code", "identifier": "330334000000"} in ids["town-of-greenville-orange-county"]["identifiers"]
    unmatched = [i["osc_row"]["municipal_code"] for i in review if i["reason"] == "osc_row_not_in_gis"]
    assert sorted(unmatched) == ["400302000000", "400302000001"]  # NYC's OSC row is not reported
    assert summary["osc_matched"] == 11


def test_osc_name_variant_kept_as_other_name():
    ids = by_id(run()[0])
    assert ids["greene-county"]["other_names"] == [{"name": "County of Greene",
                                                    "note": "Name in the OSC entity tables (2026)."}]
    assert ids["town-of-harrison"]["other_names"] == []


def test_two_agreeing_leads_make_a_primary_and_the_notes_name_both():
    ids = by_id(run()[0])
    wps = ids["orange-county"]["web_properties"]
    assert len(wps) == 1 and wps[0]["role"] == "primary"
    assert wps[0]["domain"] == "orangecountygov.com"
    assert "dotgov, its" in wps[0]["notes"] and "55k6-h6qq" in wps[0]["notes"] and "CISA" in wps[0]["notes"]
    assert set(ids["orange-county"]["provenance"]["sources"]) == {"nys_gis", "osc", "its_localities", "dotgov"}
    # The grounding row for Orange is a portal subdomain (gis.), so it was never a lead.
    assert "gis.orangecountygov.com" not in wps[0]["notes"]


def test_grounding_city_site_counts_as_a_lead():
    ids = by_id(run()[0])
    wps = ids["city-of-middletown"]["web_properties"]
    assert [w["domain"] for w in wps] == ["www.middletown-ny.com"]
    assert "grounding, its" in wps[0]["notes"]


def test_single_and_disagreeing_leads_go_to_review_not_the_record():
    recs, review, _ = run()
    ids = by_id(recs)
    assert ids["greene-county"]["web_properties"] == []
    assert review_for(review, "greene-county", "website_leads_disagree")
    assert ids["town-of-palm-tree"]["web_properties"] == []
    assert review_for(review, "town-of-palm-tree", "website_single_lead")
    # Westchester's only grounding row is a gis. subdomain: no lead at all, so no review item.
    assert ids["westchester-county"]["web_properties"] == []
    assert not review_for(review, "westchester-county")


def test_ambiguous_cisa_name_does_not_settle_a_colliding_town():
    recs, review, _ = run()
    ids = by_id(recs)
    # ITS (by SWIS) and CISA (by name) agree on the Greene Greenville's host, but the CISA lead
    # names two places, so the host counts as shared and is held for review.
    assert ids["town-of-greenville-greene-county"]["web_properties"] == []
    item = review_for(review, "town-of-greenville-greene-county", "website_host_shared")
    assert item and item[0]["shared_with"] == ["town-of-greenville-orange-county"]


def test_a_host_led_for_town_and_village_is_shared_not_primary():
    recs, review, _ = run()
    ids = by_id(recs)
    assert ids["town-of-harrison"]["web_properties"] == []
    assert review_for(review, "town-of-harrison", "website_host_shared")[0]["shared_with"] == ["village-of-harrison"]


def test_path_only_presence_is_proposed_not_applied():
    recs, review, _ = run()
    ids = by_id(recs)
    assert ids["village-of-de-ruyter"]["url_conventions"] == []
    item = review_for(review, "village-of-de-ruyter", "path_only_presence")
    assert item[0]["proposed_url_convention"] == "www.orangecountygov.com/villages/deruyter/"


def test_host_owned_by_existing_entity_goes_to_review():
    base = BASE + [{"id": "orange-thing", "name": "Orange Thing", "government_level": "authority",
                    "web_properties": [{"domain": "www.orangecountygov.com", "role": "primary"}],
                    "identifiers": [], "status": "active", "provenance": {"sources": ["abo"]}}]
    recs, review, _ = run(base=base)
    assert by_id(recs)["orange-county"]["web_properties"] == []
    item = review_for(review, "orange-county", "website_host_owned_by_existing_entity")
    assert item[0]["owned_by"] == "orange-thing"


def test_cisa_lead_naming_no_place_is_reported():
    _, review, _ = run()
    assert [i["domain"] for i in review if i["reason"] == "dotgov_lead_matches_no_place"] == ["nowhereny.gov"]


def test_census_areas_only_where_the_code_form_is_certain():
    ids = by_id(run()[0])
    assert ids["greene-county"]["areas"] == [{"scheme": "us_census_geoid", "layer": "county",
                                              "id": "36039", "role": "jurisdiction"}]
    assert ids["town-of-palm-tree"]["areas"] == [{"scheme": "us_census_geoid", "layer": "county_subdivision",
                                                  "id": "3607156060", "role": "jurisdiction"}]
    assert ids["village-of-harrison"]["areas"] == [] and ids["city-of-middletown"]["areas"] == []


def test_count_mismatch_or_missing_field_stops_the_sync():
    with pytest.raises(ValueError, match="rows, expected"):
        run(expected={**EXPECTED, "town": 933})
    gis = copy.deepcopy(GIS)
    del gis["village"][0]["COUNTY"]
    with pytest.raises(ValueError, match="field 'COUNTY' missing"):
        run(gis=gis)
    gis = copy.deepcopy(GIS)
    gis["county"][0]["NYC"] = "N"
    with pytest.raises(ValueError, match="NYC counties"):
        run(gis=gis)


def test_repeated_gnis_stops_the_sync():
    # The build matches records by identifier first, so a repeated id would merge two places.
    gis = copy.deepcopy(GIS)
    gis["town"][1]["GNIS_ID"] = gis["town"][0]["GNIS_ID"]
    with pytest.raises(ValueError, match="GNIS_ID repeats"):
        run(gis=gis)


def test_integer_swis_keeps_its_leading_zero():
    gis = copy.deepcopy(GIS)
    gis["county"].append(_county("Albany", 10000, "36001"))
    recs, _, _ = run(gis=gis, expected={**EXPECTED, "county": 10})
    assert {"scheme": "nys_swis", "identifier": "010000"} in by_id(recs)["albany-county"]["identifiers"]


def test_existing_name_or_id_is_an_error():
    base = BASE + [{"id": "orange-county", "name": "Something Else", "government_level": "nyc",
                    "web_properties": [], "identifiers": [], "status": "active"}]
    with pytest.raises(ValueError, match="ids already used"):
        run(base=base)
    base = BASE + [{"id": "x", "name": "Orange County", "government_level": "nyc",
                    "web_properties": [], "identifiers": [], "status": "active"}]
    with pytest.raises(ValueError, match="names already used"):
        run(base=base)


def test_deterministic():
    assert run() == run()


def test_records_build_with_their_stated_ids_and_validate():
    jsonschema = pytest.importorskip("jsonschema")
    recs, _, _ = run()
    entities, _ = build_entities(BASE, [recs])
    new = [e for e in entities if e["id"] != "bronx-borough-president"]
    assert sorted(e["id"] for e in new) == sorted(r["_mint_id"] for r in recs)
    greenvilles = [e for e in new if e["name"] == "Town of Greenville"]
    assert len(greenvilles) == 2, "same-name localities must not merge by name"
    for e in entities:
        jsonschema.validate(e, SCHEMA)
        assert not [k for k in e if k.startswith("_")]


def test_build_rejects_a_stated_id_in_use_or_a_name_clash():
    recs, _, _ = run()
    rec = next(r for r in recs if r["_mint_id"] == "orange-county")
    with pytest.raises(ValueError, match="already in use"):
        build_entities(BASE + [{"id": "orange-county", "name": "Zzz", "identifiers": []}], [[rec]])
    with pytest.raises(ValueError, match="has the name of existing entity"):
        build_entities(BASE + [{"id": "zzz", "name": "Orange County", "identifiers": []}], [[rec]])


# --- OSC spine (2026-11-13: mint from OSC while the GIS service is down) -------------------------

from sync_nys_localities import LEADS_CSV_COLUMNS, build_records_osc, osc_place  # noqa: E402

OSC2 = copy.deepcopy(OSC)
OSC2["towns"] = [r for r in OSC2["towns"] if r["municipal_code"] != "400302000001"]  # no duplicate
OSC2["towns"].append({"municipal_code": "330334600000", "name": "Town of Newburgh", "county": "Orange"})
OSC2["cities"].append({"municipal_code": "330211000000", "name": "City of Newburgh", "county": "Orange"})
OSC2["villages"].append({"municipal_code": "160437504410", "name": "Village of Saranac Lake", "county": "Franklin"})
OSC2["villages"].append({"municipal_code": "330454202545", "name": "Village of Kiryas Joel", "county": "Orange"})
OSC2["villages"].append({"municipal_code": "470478005080", "name": "Village of Village of The Branch",
                         "county": "Suffolk"})
OSC_EXPECTED = {"county": 4, "city": 2, "town": 6, "village": 5}
ITS2 = ITS + [
    _its("400000", "County", None, "St Lawrence", None),
    _its("402000", "Town", "Canton", "St Lawrence", None),
    _its("333400", "Town", "Greenville", "Orange", None),
    _its("336000", "Town", "Palm Tree", "Orange", None),
    _its("331200", "City", "Newburgh", "Orange", None),
    _its("334600", "Town", "Newburgh", "Orange", None),
    {"swis_code": "153801", "type": "Village", "municipality": "Saranac Lake", "county": "Essex",
     "_2nd_county": "Franklin", "website": None},
    _its("334005", "Village", "Kiryas Joel", "Orange", None),
    _its("472800", "Village", "The Branch", "Suffolk", None),
]
for r in ITS2:
    r.setdefault("gnis_id", "7" + r["swis_code"][:6])
# ITS gives a town its city's GNIS id (as it does for 24 real pairs).
next(r for r in ITS2 if r["swis_code"] == "334600")["gnis_id"] = next(
    r for r in ITS2 if r["swis_code"] == "331200")["gnis_id"]
next(r for r in ITS2 if r["swis_code"] == "190000")["county_fips"] = "36039"
STALE = {"334005": "old code; GIS gives 336001"}


def run_osc(osc=OSC2, its=ITS2, expected=OSC_EXPECTED):
    return build_records_osc(osc, its, DOTGOV, GROUNDING, BASE, STALE, TLDS, PSL, expected)


def test_osc_place_names():
    assert osc_place("county", "County of St. Lawrence") == "St. Lawrence"
    assert osc_place("village", "Village of Village of The Branch") == "The Branch"


def test_osc_spine_counts_ids_and_no_nyc():
    recs, _, summary = run_osc()
    ids = by_id(recs)
    assert summary["units"] == OSC_EXPECTED
    assert "city-of-new-york-city" not in ids and "city-of-new-york" not in ids
    assert {"st-lawrence-county", "village-of-the-branch", "town-of-greenville-greene-county",
            "town-of-greenville-orange-county", "town-of-harrison", "village-of-harrison"} <= set(ids)
    assert ids["st-lawrence-county"]["name"] == "St. Lawrence County"
    assert all(r["provenance"]["sources"][0] == "osc" for r in recs)
    assert all(any(i["scheme"] == "nys_osc_muni_code" for i in r["identifiers"]) for r in recs)
    with pytest.raises(ValueError, match="OSC places"):
        run_osc(expected={**OSC_EXPECTED, "town": 933})


def test_its_codes_only_on_a_single_match():
    recs, review, summary = run_osc()
    ids = by_id(recs)

    def swis(i):
        return next((x["identifier"] for x in ids[i]["identifiers"] if x["scheme"] == "nys_swis"), None)
    assert swis("town-of-palm-tree") == "336000"
    assert swis("village-of-deruyter") == "336400"             # OSC DeRuyter, ITS De Ruyter
    assert swis("village-of-the-branch") == "472800"           # Village of Village of The Branch
    assert swis("village-of-saranac-lake") == "153801"         # matched on ITS's second county
    assert summary["its"]["via_second_county"] == 1
    assert swis("village-of-kiryas-joel") is None              # curated stale code held
    assert review_for(review, "village-of-kiryas-joel", "its_swis_known_stale")
    assert swis("town-of-canton") == "402000"
    assert swis("city-of-middletown") == "331100"
    assert "its_localities" in ids["town-of-palm-tree"]["provenance"]["sources"]
    # The dissolved ITS village matches no OSC place and is listed.
    assert [i["its_row"]["swis_code"] for i in review if i["reason"] == "its_row_unmatched"] == ["339999"]


def test_county_census_area_from_its_county_fips():
    ids = by_id(run_osc()[0])
    assert ids["greene-county"]["areas"] == [{"scheme": "us_census_geoid", "layer": "county",
                                              "id": "36039", "role": "jurisdiction"}]
    assert ids["town-of-palm-tree"]["areas"] == []  # ITS gives no county-subdivision code


def test_gnis_shared_by_city_and_town_is_attached_to_neither():
    recs, review, summary = run_osc()
    ids = by_id(recs)
    for i in ("city-of-newburgh", "town-of-newburgh"):
        assert not [x for x in ids[i]["identifiers"] if x["scheme"] == "us_gnis"], i
        assert [x for x in ids[i]["identifiers"] if x["scheme"] == "nys_swis"], i
        assert review_for(review, i, "its_code_shared_by_places")
    assert summary["its"]["codes_shared_not_attached"] == {"nys_swis": 0, "us_gnis": 2}


def test_osc_spine_keeps_the_two_lead_rule():
    ids = by_id(run_osc()[0])
    assert [w["domain"] for w in ids["orange-county"]["web_properties"]] == ["orangecountygov.com"]
    assert ids["town-of-harrison"]["web_properties"] == []


def test_lead_rows_one_host_per_row_only_without_primary():
    _, _, summary = run_osc()
    rows = summary["lead_rows"]
    assert all(set(r) == set(LEADS_CSV_COLUMNS) for r in rows)
    ids = {r["entity_id"] for r in rows}
    assert "orange-county" not in ids and "city-of-middletown" not in ids  # they got a primary
    greene = [(r["host"], r["source"]) for r in rows if r["entity_id"] == "greene-county"]
    assert greene == [("greenecountyny.gov", "dotgov"), ("www.greenegovernment.com", "its")]
    # A path-only lead (a page on the county's site) is not a host lead.
    assert not [r for r in rows if r["entity_id"] == "village-of-deruyter"]
