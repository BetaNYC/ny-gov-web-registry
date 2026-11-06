"""Offline tests for scripts/ai_readability_websites.py (no network)."""
import csv
import json
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import ai_readability_websites as ws  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
AIR = ROOT / "data" / "ai-readability"
PSL = ({"gov", "org", "us", "ny.us"}, set(), set())


def _key(h):
    return h[4:] if h.startswith("www.") else h


def _fixture(tmp):
    (tmp / "reg.json").write_text(json.dumps({"entities": [
        {"id": "a", "name": "Agency A", "government_level": "nyc",
         "web_properties": [{"domain": "www.shared.gov", "role": "primary"},
                            {"domain": "old-a.gov", "role": "legacy"}]},
        {"id": "b", "name": "Agency B", "government_level": "nyc",
         "web_properties": [{"domain": "shared.gov", "role": "primary"}]}]}))
    (tmp / "ct.json").write_text(json.dumps({"targets": [
        {"id": "a", "name": "Agency A", "start_url": "https://new.a.gov/home"}]}))
    (tmp / "state.csv").write_text("domain,url,agency\nhealth.ny.gov,https://health.ny.gov,DOH\n")
    (tmp / "abo.json").write_text(json.dumps([
        {"public_authority_name": "Auth X", "public_authority_type": "State",
         "website": {"url": "http://www.authx.org/"}}]))
    (tmp / "cisa.csv").write_text(
        "Domain name,Domain type,Organization name,Suborganization name,City,State,Security contact email\n"
        "townofz.gov,City,Town of Z,,Z,NY,secret@example.test\n"
        "elsewhere.gov,City,City of Elsewhere,,E,CA,(blank)\n")
    (tmp / "its.json").write_text(json.dumps([
        {"type": "Town", "town_name": "Z", "county": "Albany", "website": {"url": "http://townofz.gov"}},
        {"type": "Village", "village_name": "Q", "county": "Albany",
         "website": {"url": "https://co.albany.ny.us/q"}}]))
    return {"registry": tmp / "reg.json", "crawl_targets": tmp / "ct.json", "state_agency": tmp / "state.csv",
            "abo": tmp / "abo.json", "cisa": tmp / "cisa.csv", "its_2023": tmp / "its.json"}


def test_build_merges_sources_one_row_per_host(tmp_path):
    rows = {r["ny_initial_domain"]: r for r in ws.build(_fixture(tmp_path), PSL)}
    assert set(rows) == {"www.shared.gov", "new.a.gov", "health.ny.gov", "www.authx.org",
                         "townofz.gov", "co.albany.ny.us"}            # legacy and non-NY rows dropped
    shared = rows["www.shared.gov"]
    assert shared["agency"] is None and shared["ny_entity_ids"] == ["a", "b"]
    z = rows["townofz.gov"]
    assert z["source_list_cisa"] and z["source_list_its_2023"] and z["ny_source_count"] == 2
    assert z["agency"] == "Town of Z"
    assert rows["health.ny.gov"]["base_domain"] == "ny.gov" and rows["health.ny.gov"]["agency"] == "DOH"
    page = rows["co.albany.ny.us"]
    assert page["agency"] is None and page["ny_page_urls"] == ["https://co.albany.ny.us/q"]
    assert page["base_domain"] == "albany.ny.us"
    assert "secret@example.test" not in json.dumps(list(rows.values()))


def test_published_list_is_in_sync_with_registry_and_pool():
    pub = json.loads((AIR / "websites.json").read_text())
    assert pub == json.loads(json.dumps(ws.build())), "run: python scripts/ai_readability_websites.py"
    keys = {_key(r["ny_initial_domain"]) for r in pub}
    reg = json.loads((ROOT / "data" / "registry.json").read_text())
    for e in reg["entities"]:
        for w in e.get("web_properties") or []:
            if w.get("role") != "legacy":
                assert _key(w["domain"].lower()) in keys, w["domain"]
    for t in json.loads((AIR / "pool-2026-11-06.json").read_text()):
        assert _key(urllib.parse.urlparse(t["url"]).hostname) in keys, t["url"]


def test_columns_follow_gsa_target_list_and_dictionary_is_current(tmp_path):
    with open(AIR / "gsa" / "Target_URL_List_Data_Dictionary.csv", newline="") as fh:
        gsa = [r["field id"] for r in csv.DictReader(fh)]
    assert ws.COLUMNS[:6] == gsa[:6]
    with open(AIR / "websites.csv", newline="") as fh:
        assert next(csv.reader(fh)) == ws.COLUMNS
    out = ws.write_dictionary(tmp_path / "d.csv")
    assert out.read_text() == (AIR / "target-list-dictionary.csv").read_text()
    assert [r["field id"] for r in csv.DictReader(out.open())] == ws.COLUMNS


def test_websites_json_is_a_valid_probe_and_render_target_list():
    sys.path.insert(0, str(ROOT / "scripts"))
    import probe_bot_access as p
    import render_check as rc
    targets = p.load_targets(str(AIR / "websites.json"))
    pub = json.loads((AIR / "websites.json").read_text())
    assert len(targets) == len(pub) and all(t["url"].startswith("https://") for t in targets)
    assert rc.load_targets(str(AIR / "websites.json")) == targets
    one = p.website_row_as_target({"initial_url": "https://x.gov/", "agency": None,
                                   "ny_entity_ids": ["a", "b"], "ny_names": ["A", "B"]})
    assert one == {"entity_id": None, "entity_name": "A", "url": "https://x.gov/"}
