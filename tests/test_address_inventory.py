"""Offline tests for the address inventory (issue #41): scripts/address_inventory.py, the portal
rows of scripts/ai_readability_websites.py, and the pure parts of scripts/cert_log_names.py."""
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import address_inventory as ai  # noqa: E402
import ai_readability_websites as ws  # noqa: E402
import cert_log_names as cl  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
AIR = ROOT / "data" / "ai-readability"
PSL = ({"gov", "org", "com", "us", "ny.us"}, set(), set())
OWN_DOMAIN_SITES = ("511ny.org", "myrts.com", "compstat.nypdonline.org", "webcams.nyctmc.org",
                    "checkbooknyc.com", "rocklandgis.com", "webapi.legistar.com")


def _published():
    with open(AIR / "addresses.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _websites():
    return {ws._key(r["ny_initial_domain"]): r
            for r in json.loads((AIR / "websites.json").read_text(encoding="utf-8"))}


# ------------------------------------------------------------------------------ published file
def test_addresses_csv_is_in_sync_with_the_builder():
    with open(AIR / "addresses.csv", newline="", encoding="utf-8") as fh:
        assert tuple(next(csv.reader(fh))) == ai.COLUMNS
    assert _published() == ai.build(), "run: python3 scripts/address_inventory.py"


def test_addresses_are_unique():
    keys = [ai.addr_key(r["address"]) for r in _published()]
    assert len(keys) == len(set(keys))


def test_kinds_and_sources_are_allowed():
    for r in _published():
        assert r["kind"] in ("main site", "sub-site", "section", "data portal", "API"), r
        assert all(s in ai.SOURCE_LABELS for s in r["source"].split("; ")), r
        assert r["address"].startswith(("https://", "http://")), r
        assert len(r["date_found"]) == 10, r


def test_addresses_csv_holds_no_email_or_phone():
    text = (AIR / "addresses.csv").read_text(encoding="utf-8")
    assert "@" not in text
    for f in (AIR / "sources" / "2026-11-10").glob("*.csv"):
        assert "@" not in f.read_text(encoding="utf-8"), f


def test_every_non_government_portal_has_a_non_government_entity_type():
    sites = _websites()
    non_gov = [r for r in sites.values() if r["ny_government"] is False]
    assert non_gov, "the portal registry lists nonprofit, library and university portals"
    for r in non_gov:
        assert r["ny_portal_entity_types"], r["ny_initial_domain"]
        assert set(r["ny_portal_entity_types"]) <= set(ws.NON_GOVERNMENT_TYPES), r["ny_initial_domain"]
        assert r["ny_source_count"] == 1 and r["source_list_portal_registry"], r["ny_initial_domain"]
    # and the reverse: a portal-only host whose every type is non-government is never government
    for r in sites.values():
        types = r.get("ny_portal_entity_types") or []
        if r["ny_source_count"] == 1 and types and set(types) <= set(ws.NON_GOVERNMENT_TYPES):
            assert r["ny_government"] is False, r["ny_initial_domain"]
    by_addr = {ai.addr_key(r["address"]): r for r in _published()}
    for r in non_gov:
        for url in r["ny_portal_urls"]:
            row = by_addr[ai.addr_key(url.split("#")[0])]
            assert set(row["level_or_entity_type"].split("; ")) <= set(ws.NON_GOVERNMENT_TYPES), url


def test_legistar_web_api_is_the_councils_api():
    site = _websites()["webapi.legistar.com"]
    assert site["ny_kind"] == "API" and site["ny_hosting"] == "vendor"
    assert site["ny_portal_registry_ids"] == ["new-york-city-council"] and site["ny_government"]
    rows = [r for r in _published() if "webapi.legistar.com" in r["address"]]
    assert len(rows) == 1
    assert rows[0]["kind"] == "API" and rows[0]["owning_body_id"] == "new-york-city-council"
    assert "vendor-hosted" in rows[0]["notes"]


def test_government_sites_on_their_own_domains_are_in_the_website_list():
    sites = _websites()
    for host in OWN_DOMAIN_SITES:
        r = sites[host]
        assert r["source_list_portal_registry"] and r["ny_government"], host
        assert r["ny_kind"] in ("data portal", "API"), host


def test_every_live_or_unconfirmed_portal_is_in_websites_and_addresses():
    doc = json.loads(ws.PATHS["portal_registry"].read_text(encoding="utf-8"))
    sites, by_addr = _websites(), {ai.addr_key(r["address"]) for r in _published()}
    rows = [r for r in doc["rows"] if r["status"] in ("live", "UNCONFIRMED")]
    assert len(rows) == 100
    for r in rows:
        h, _ = ws._host_path(r["url"])
        assert sites[ws._key(h)]["source_list_portal_registry"], r["url"]
        assert ai.addr_key(r["url"].split("#")[0]) in by_addr, r["url"]
    dead = [r for r in doc["rows"] if r["status"] not in ("live", "UNCONFIRMED")]
    for r in dead:
        h, _ = ws._host_path(r.get("url") or "")
        if h and ws._key(h) in sites:
            assert sites[ws._key(h)]["ny_source_count"] > 1 or not sites[ws._key(h)]["source_list_portal_registry"]


def test_portal_urls_never_become_deep_pages():
    for r in _websites().values():
        for url in r.get("ny_portal_urls") or []:
            assert url not in (r.get("ny_page_urls") or []) or r["ny_source_count"] > 1


# ------------------------------------------------------------------------------ builder fixtures
def _portal_fixture(tmp):
    rows = [
        {"entity": "Town Q", "entity_type": "county", "url": "https://data.townq.gov/", "status": "live",
         "registry_id": None},
        {"entity": "Q Council", "entity_type": "legislature", "url": "https://webapi.legistar.com/v1/q/",
         "status": "live", "registry_id": "q-council"},
        {"entity": "A Library", "entity_type": "library", "url": "https://maps.alib.org/", "status": "UNCONFIRMED",
         "registry_id": None},
        {"entity": "Gone", "entity_type": "county", "url": "https://gone.gov/", "status": "dead",
         "registry_id": None},
        {"entity": "Agency A", "entity_type": "agency", "url": "https://www.shared.gov/data", "status": "live",
         "registry_id": None},
    ]
    (tmp / "portals.json").write_text(json.dumps({"rows": rows}))
    (tmp / "reg.json").write_text(json.dumps({"entities": [
        {"id": "a", "name": "Agency A", "government_level": "nyc",
         "web_properties": [{"domain": "www.shared.gov", "role": "primary"}]}]}))
    return {"registry": tmp / "reg.json", "portal_registry": tmp / "portals.json"}


def test_portal_rows_keep_entity_type_and_kind(tmp_path):
    rows = {r["ny_initial_domain"]: r for r in ws.build(_portal_fixture(tmp_path), PSL)}
    assert "gone.gov" not in rows                                   # dead rows are not read
    assert rows["data.townq.gov"]["ny_kind"] == "data portal" and rows["data.townq.gov"]["ny_government"]
    assert rows["data.townq.gov"]["agency"] == "Town Q"
    api = rows["webapi.legistar.com"]
    assert api["ny_kind"] == "API" and api["ny_hosting"] == "vendor"
    assert api["ny_portal_registry_ids"] == ["q-council"]
    lib = rows["maps.alib.org"]
    assert lib["ny_government"] is False and lib["ny_portal_entity_types"] == ["library"]
    shared = rows["www.shared.gov"]                                 # a portal page on a listed host
    assert shared["ny_kind"] == "main site" and shared["ny_government"]
    assert shared["ny_page_urls"] is None and shared["ny_portal_urls"] == ["https://www.shared.gov/data"]


def test_api_kind():
    assert ws.api_kind("https://services6.arcgis.com/x/arcgis/rest/services")
    assert ws.api_kind("https://www.panynj.gov/bin/portauthority/ridepath.json")
    assert ws.api_kind("https://webapi.legistar.com/v1/nyc/")
    assert not ws.api_kind("https://data.ny.gov/")
    assert not ws.api_kind("https://api-portal.nyc.gov/")       # a developer portal is a page


def test_addr_key_and_board_ids():
    assert ai.addr_key("https://www.x.gov/a/") == ai.addr_key("http://x.gov/a")
    assert ai.addr_key("https://x.gov/a?b=1") != ai.addr_key("https://x.gov/a")
    assert ai.addr_key("https://x.gov/#/ocgis") == ai.addr_key("https://x.gov/")
    assert ai.board_registry_id("BK08") == "brooklyn-community-board-8"
    assert ai.board_registry_id("SI03") == "staten-island-community-board-3"
    assert ai.board_registry_id("XX01") is None


def test_contact_files_give_only_the_website_address(tmp_path):
    cb = tmp_path / "community-boards"
    cb.mkdir()
    (cb / "bk09.md").write_text("---\nboard_id: BK09\n---\n- **Website:** https://cb9.example.org/ (old one 404s)\n"
                                "- **Office email:** someone@example.org\n- **Office phone:** (718) 555-0100\n")
    (cb / "bk10.md").write_text("---\nboard_id: BK10\n---\n- **Website:** https://listed.example.org/\n")
    el = tmp_path / "electeds" / "nycc-districts"
    el.mkdir(parents=True)
    (el / "d07-someone.md").write_text("- **Website:** https://council.nyc.gov/district-7/ · **Social:** @x\n")
    (tmp_path / "agencies").mkdir()
    (tmp_path / "agencies" / "x.md").write_text("- **Website:** https://agency.example.gov/\n")
    out = ai.contact_websites(tmp_path, {ai.addr_key("https://listed.example.org")})
    assert out == [
        {"address": "https://council.nyc.gov/district-7/", "owning_body_id": "new-york-city-council",
         "notes": "Council District 7 page"},
        {"address": "https://cb9.example.org/", "owning_body_id": "brooklyn-community-board-9", "notes": ""},
    ]                                       # agency file: owner not stated, left out
    assert "@" not in json.dumps(out) and "555" not in json.dumps(out)


def test_fetched_links_keep_same_domain_hosts_only(tmp_path):
    lines = [
        {"host": "www.town.gov", "test": "W1", "evidence": {
            "first_8kb": "User-agent: *\nSitemap: https://maps.town.gov/sitemap.xml\n"
                         "Sitemap: https://www.town.gov/sitemap.xml\nSitemap: https://other.org/s.xml\n",
            "final_url": "https://www.town.gov/home"}},
        {"host": "apps.town.gov", "test": "F4", "evidence": {"final_url": "https://login.town.gov/"}},
        {"host": "x.yourtasc.com", "test": "W1", "evidence": {"first_8kb": "Sitemap: https://yourtasc.com/s\n"}},
    ]
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(l) for l in lines))
    websites = [{"ny_initial_domain": "www.town.gov"}, {"ny_initial_domain": "apps.town.gov"}]
    psl = ({"gov", "org", "com"}, set(), set())
    assert ai.fetched_links(tmp_path, websites, psl) == [
        {"address": "https://maps.town.gov/", "listed_host": "www.town.gov"}]


# ------------------------------------------------------------------------------ certificate logs
def test_cert_log_filter():
    entries = [{"name_value": "*.town.gov\nwww.town.gov\nmaps.town.gov", "common_name": "town.gov"},
               {"name_value": "mail.town.gov\nautodiscover.town.gov\nvpn-east.town.gov\nmail2.town.gov"},
               {"name_value": "click.email.town.gov\nportalstaging.town.gov\ncpanel.town.gov"},
               {"name_value": "Permits.Town.gov.\nnottown.gov\ntown.gov.evil.com\nmailbox-help.town.gov"}]
    assert cl.names_from_crtsh(entries, "town.gov") == [          # mailbox-help is not mail-*
        "mailbox-help.town.gov", "maps.town.gov", "permits.town.gov", "town.gov", "www.town.gov"]


def test_cert_log_accept_rule():
    assert cl.accept(200, "text/html; charset=utf-8", None, "a.x.gov") == (True, "200 text/html")
    assert cl.accept(403, "text/html", None, "a.x.gov")[0]
    assert cl.accept(200, "application/json", None, "a.x.gov")[0]
    assert not cl.accept(200, "image/png", None, "a.x.gov")[0]
    assert not cl.accept(404, "text/html", None, "a.x.gov")[0]
    assert not cl.accept(500, "text/html", None, "a.x.gov")[0]
    assert cl.accept(302, None, "/home", "a.x.gov")[0]
    assert cl.accept(301, None, "https://a.x.gov/home", "a.x.gov")[0]
    assert not cl.accept(301, None, "https://www.x.gov/", "a.x.gov")[0]


def test_cert_log_domains_skip_vendors_and_shared_hosting():
    rows = [
        {"base_domain": "town.gov", "ny_initial_domain": "www.town.gov"},
        {"base_domain": "villagex.org", "ny_initial_domain": "www.villagex.org"},
        {"base_domain": "wordpress.com", "ny_initial_domain": "villagey.wordpress.com"},
        {"base_domain": "a.wixsite.com", "ny_initial_domain": "a.wixsite.com"},
        {"base_domain": "bigvendor.com", "ny_initial_domain": "townz.bigvendor.com"},
        {"base_domain": "co.wayne.ny.us", "ny_initial_domain": "gis.co.wayne.ny.us"},
        {"base_domain": "lookalike.org", "ny_initial_domain": "lookalike.org", "ny_host_issue": "lookalike"},
        {"base_domain": "charity.org", "ny_initial_domain": "charity.org", "ny_government": False},
        {"base_domain": "bad,org", "ny_initial_domain": "bad,org"},
    ]
    assert cl.registered_domains(rows) == ["co.wayne.ny.us", "town.gov", "villagex.org"]


def test_cert_log_user_agent_is_the_research_identity():
    import probe_bot_access as p
    ua = next(i["ua"] for i in p.IDENTITIES if i["key"] == "research_betanyc")
    assert cl.UA == ua
