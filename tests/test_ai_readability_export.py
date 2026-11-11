"""Offline tests for scripts/ai_readability_export.py (no network)."""
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import ai_readability_export as ex  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PSL = ({"gov", "us", "ny.us", "org", "uk", "co.uk"}, {"ck"}, {"www.ck"})


def test_base_domain_follows_public_suffix_rules():
    assert ex.base_domain("dos.ny.gov", PSL) == "ny.gov"
    assert ex.base_domain("www.otda.state.ny.us", PSL) == "state.ny.us"   # ny.us is a suffix
    assert ex.base_domain("a.b.example.co.uk", PSL) == "example.co.uk"
    assert ex.base_domain("x.foo.ck", PSL) == "x.foo.ck"                  # wildcard *.ck
    assert ex.base_domain("www.ck", PSL) == "www.ck"                      # exception !www.ck
    assert ex.base_domain("gov", PSL) is None


def test_columns_are_gsa_order_then_ours():
    gsa = ex.gsa_dictionary()
    cols = ex.columns(gsa)
    assert cols[:len(gsa)] == [r["field id"] for r in gsa]
    assert all(c.startswith(("ny_", "ai_")) for c in cols[len(gsa):])
    assert len(cols) == len(set(cols))
    assert set(ex.GSA_FILLED) <= {r["field id"] for r in gsa}


def test_committed_dictionary_matches_code(tmp_path):
    out = ex.write_dictionary(tmp_path / "d.csv")
    assert out.read_text() == (ROOT / "data" / "ai-readability" / "data-dictionary.csv").read_text(), \
        "run: python scripts/ai_readability_export.py --write-dictionary"
    rows = list(csv.DictReader(out.open()))
    assert tuple(rows[0].keys()) == ex.DICT_COLUMNS
    gsa_types = {r["data type"] for r in ex.gsa_dictionary()}
    assert {r["data type"] for r in rows} <= gsa_types


def _rows(host="www.example.gov"):
    base = {"run_id": "r", "catalog_version": "0.1", "vantage": "office", "ts": "2026-11-06T00:00:00+00:00",
            "host": host, "identity": "research_betanyc"}
    def r(test, verdict, ident="research_betanyc", **ev):
        return dict(base, test=test, verdict=verdict, identity=ident, evidence=ev)
    return [
        r("W1", "pass", status=200, content_type="text/plain; charset=utf-8",
          final_url="https://{}/robots.txt".format(host)),
        r("W3", "reported", tokens={"GPTBot": {"/": "disallow"}, "ClaudeBot": {"/": {"decision": "allow"}}}),
        r("W4", "pass", status=200, final_url="https://{}/home".format(host), server="nginx"),
        r("W4", "blocked", "gptbot", status=403),
        r("M2", "pass", url="https://{}/sitemap.xml".format(host), status=200, kind="urlset", url_count=7,
          content_type="application/xml"),
        r("F4", "pass", status=404),
        r("I5", "reported", http_status=301, http_redirects_to_https=True),
        r("R3", "reported", lang="en", landmarks=["main"], title="Example"),
    ]


def test_build_rows_fills_gsa_fields_and_ours():
    t = [{"url": "https://www.example.gov/", "entity_id": "ex", "entity_name": "Example Agency",
          "pool": "county", "lead": "cisa"}]
    v1 = [{"url": t[0]["url"], "variant": "headless_default", "verdict": "blocked", "status": 403},
          {"url": t[0]["url"], "variant": "headless_as_chrome", "verdict": "pass", "status": 200, "words": 9}]
    row = ex.build_rows(t, _rows(), v1, [], PSL, tenants={"www.example.gov": {"ex"}})[0]
    assert row["name"] == "example.gov" and row["initial_base_domain"] == "example.gov"
    assert row["live"] is True and row["redirect"] is True and row["status_code"] == 200
    assert row["primary_scan_status"] == "completed" and row["404_test"] is True
    assert row["robots_txt_detected"] is True and row["robots_txt_media_type"] == "text/plain"
    assert row["sitemap_xml_detected"] is True and row["sitemap_xml_count"] == 7
    assert row["https_enforced"] is True and row["main_element_present"] is True
    assert row["agency"] == "Example Agency" and row["source_list"] == ["cisa"]
    assert row["ai_w3_decisions"] == ["ClaudeBot=allow", "GPTBot=disallow"]
    assert row["ai_w4_gptbot_admitted"] is False and row["ai_v1_name_decides"] is True
    assert row["accessibility_violations"] is None                      # not measured -> blank


def test_shared_host_has_no_agency_and_quarantine_maps_to_gsa_status():
    t = [{"url": "https://www.example.gov/", "entity_id": "a", "entity_name": "A", "pool": "nyc-registry"}]
    row = ex.build_rows(t, _rows(), [], [], PSL, tenants={"www.example.gov": {"a", "b"}})[0]
    assert row["agency"] is None and row["ny_entity_ids"] == ["a", "b"]
    q = [{"host": "down.example.gov", "action": "quarantined", "what": "transport dns: gaierror"}]
    t2 = [{"url": "https://down.example.gov/", "entity_id": None, "entity_name": "D", "pool": "city"}]
    row2 = ex.build_rows(t2, [], [], q, PSL)[0]
    assert row2["primary_scan_status"] == "dns_resolution_error" and row2["live"] is None


def test_csv_conventions(tmp_path):
    assert ex.csv_value(True) == "TRUE" and ex.csv_value(None) == "" and ex.csv_value(["a", "b"]) == "a,b"
    ex.write_outputs([{"name": "x", "live": False}], tmp_path, ["name", "live"])
    assert (tmp_path / "sites.csv").read_text().splitlines() == ["name,live", "x,FALSE"]
    assert json.loads((tmp_path / "sites.json").read_text()) == [{"name": "x", "live": False}]


def test_reach_and_retest_columns():
    t = [{"url": "https://down.example.gov/", "entity_id": None, "entity_name": "D", "pool": "city"},
         {"url": "https://www.example.gov/", "entity_id": "ex", "entity_name": "E", "pool": "county"}]
    reach = [{"test": "reach", "url": t[0]["url"], "host": "down.example.gov", "ts": "2026-11-08T13:00:00+00:00",
              "dns_resolves": True, "tcp443": True, "tcp80": True, "tls": "bad_cert",
              "tls_detail": "certificate has expired", "http_status": 200, "diagnosis": "bad_certificate_http_ok"}]
    retest = [dict(r, run_id="retest") for r in _rows("down.example.gov")]
    rows = ex.build_rows(t, _rows(), [], [], PSL, reach_rows=reach, retest_results=retest)
    assert rows[0]["ai_reach_checked"] is True and rows[0]["ai_reach_diagnosis"] == "bad_certificate_http_ok"
    assert rows[0]["ai_reach_tls"] == "bad_cert" and rows[0]["ai_reach_http_status"] == 200
    assert rows[0]["ai_retest_run_id"] == "retest" and rows[0]["ai_retest_w4_verdict"] == "pass"
    assert rows[1]["ai_reach_checked"] is None and rows[1]["ai_retest_w4_verdict"] is None


def test_person_columns():
    t = [{"url": "https://walled.example.gov/", "entity_id": None, "entity_name": "W", "pool": "city"},
         {"url": "https://www.example.gov/", "entity_id": "ex", "entity_name": "E", "pool": "county"}]
    person = [{"test": "headed", "url": t[0]["url"], "host": "walled.example.gov", "status": 200,
               "challenge": False, "verdict": "pass", "vantage": "home-hearth"}]
    prow = [dict(r, identity="headed_chrome") for r in _rows("walled.example.gov")]
    rows = ex.build_rows(t, _rows(), [], [], PSL, person_rows=person, person_results=prow)
    assert rows[0]["ai_person_checked"] is True and rows[0]["ai_person_verdict"] == "pass"
    assert rows[0]["ai_person_status"] == 200 and rows[0]["ai_person_vantage"] == "home-hearth"
    assert rows[0]["ai_person_w1_verdict"] is not None
    assert isinstance(rows[0]["ai_person_abhi12_score"], float)
    assert rows[1]["ai_person_checked"] is None and rows[1]["ai_person_abhi12_score"] is None


# ---------------------------------------------------------------- catalog 0.2 (issue #45)
def _full_rows(host="data.example.gov"):
    base = {"run_id": "r2", "catalog_version": "0.2", "vantage": "home", "ts": "2026-11-10T00:00:00+00:00",
            "host": host}

    def r(test, verdict, ident="research_betanyc", **ev):
        return dict(base, test=test, verdict=verdict, identity=ident, evidence=ev)
    return [
        r("W4", "pass", status=200, final_url="https://{}/".format(host)),
        r("W4", "blocked", "claudebot", status=403),
        r("W4", "pass", "meta_webindexer", status=200),
        r("C1", "reported", gsa_cms=None, cms="WordPress", source="homepage",
          homepage_platforms=["WordPress"], homepage_signals=[]),
        r("D2", "reported", count=3, by_kind={"open_data": 2, "feed": 1}, links=[]),
        r("D3", "reported", status=200, rest_index=True, namespaces=["wp/v2", "oembed/1.0"], routes=40),
        r("D4", "reported", status=404, present=False, media_type_ok=False, profile_rfc9727=False),
        r("D5", "pass", platform="ckan", url="https://{}/api/3/action/package_list?limit=1".format(host),
          status=200, datasets=1, platform_signal="address list"),
        r("D6", "partial", words=80, dataset_url="https://{}/dataset/x".format(host)),
        r("D7", "pass", "gptbot", status=200),
        r("D7", "partial", "all_ai", ai_identities={"gptbot": "pass", "claudebot": "blocked"},
          admitted=["gptbot"]),
        r("R2", "reported", accept_markdown={"status": 200, "content_type": "text/html; charset=utf-8",
                                             "markdown": False, "vary": "Accept-Encoding"},
          index_md={"status": 404, "content_type": "text/html", "markdown": False},
          markdown_served=False, advertised_alternates=[{"href": "https://{}/index.md".format(host),
                                                         "via": "<link>"}]),
        r("M5", "reported", status=200, present=True, bytes=1234, content_type="text/plain"),
        r("F3", "pass", checked=10, broken=1, links=[]),
    ]


RUN = {"tier": "full",
       "identities": [{"key": "research_betanyc"}, {"key": "baseline_default"}, {"key": "googlebot"},
                      {"key": "claudebot"}, {"key": "meta_webindexer"}, {"key": "mechanism_probe_chrome"}],
       "network": {"place": "home", "vantage": "home",
                   "start": {"asn": "AS64500", "provider": "EXAMPLE-NET", "public_ip": "192.0.2.1",
                             "by_family": {"ipv4": {"public_ip": "192.0.2.1", "error": None},
                                           "ipv6": {"public_ip": None, "error": "OSError: no route"}}},
                   "end": {"asn": "AS64500", "provider": "EXAMPLE-NET", "public_ip": "192.0.2.9"},
                   "changed": {"asn": False, "public_ip": True}}}


def test_run_identities_keep_the_first_six_then_the_run_order():
    keys = ex.run_identities(RUN)
    assert keys[:6] == list(ex.IDENTITIES)
    assert keys[6:] == ["baseline_default", "claudebot", "meta_webindexer"]
    assert ex.run_identities(None) == list(ex.IDENTITIES)
    assert ex.run_identities({"identities": ["ccbot", "gptbot"]})[6:] == ["ccbot"]   # plain keys too


def test_w4_columns_follow_the_identities_the_run_used():
    old = ex.columns()
    new = ex.columns(identities=ex.run_identities(RUN))
    assert set(old) <= set(new) and len(new) == len(set(new))
    assert "ai_w4_claudebot_status" in new and "ai_w4_claudebot_status" not in old
    t = [{"url": "https://data.example.gov/", "entity_id": None, "entity_name": "D"}]
    row = ex.build_rows(t, _full_rows(), [], [], PSL, run=RUN)[0]
    assert row["ai_w4_claudebot_status"] == 403 and row["ai_w4_claudebot_admitted"] is False
    assert row["ai_w4_meta_webindexer_admitted"] is True
    assert row["ai_w4_gptbot_status"] is None                      # a column of the six, not measured
    assert "ai_w4_claudebot_status" not in ex.build_rows(t, _full_rows(), [], [], PSL)[0]


def test_new_test_columns():
    t = [{"url": "https://data.example.gov/", "entity_id": None, "entity_name": "D"}]
    row = ex.build_rows(t, _full_rows(), [], [], PSL, run=RUN)[0]
    assert row["ai_c1_verdict"] == "reported" and row["ai_c1_cms"] == "WordPress"
    assert row["ai_c1_source"] == "homepage" and row["ai_c1_homepage_platforms"] == ["WordPress"]
    assert row["cms"] == "WordPress"
    assert row["ai_d2_count"] == 3 and row["ai_d2_open_data_count"] == 2 and row["ai_d2_feed_count"] == 1
    assert row["ai_d2_api_count"] == 0                             # D2 ran and found none
    assert row["ai_d3_rest_index"] is True and row["ai_d3_routes"] == 40
    assert row["ai_d3_namespaces"] == ["wp/v2", "oembed/1.0"]
    assert row["ai_d4_status"] == 404 and row["ai_d4_present"] is False and row["ai_d4_items"] is None
    assert row["ai_d5_verdict"] == "pass" and row["ai_d5_platform"] == "ckan"
    assert row["ai_d5_platform_signal"] == "address list" and row["ai_d5_datasets"] == 1
    assert row["ai_d6_verdict"] == "partial" and row["ai_d6_words"] == 80
    assert row["ai_d6_dataset_url"].endswith("/dataset/x")
    assert row["ai_d7_verdict"] == "partial"
    assert row["ai_d7_admitted"] == ["gptbot"] and row["ai_d7_not_admitted"] == ["claudebot"]
    assert row["ai_r2_markdown_served"] is False and row["ai_r2_accept_status"] == 200
    assert row["ai_r2_accept_media_type"] == "text/html" and row["ai_r2_vary"] == "Accept-Encoding"
    assert row["ai_r2_index_md_status"] == 404
    assert row["ai_r2_advertised_alternates"] == ["https://data.example.gov/index.md"]
    assert row["ai_m5_present"] is True and row["ai_m5_status_code"] == 200 and row["ai_m5_bytes"] == 1234
    assert row["ai_f3_verdict"] == "pass" and row["ai_f3_checked"] == 10 and row["ai_f3_broken"] == 1


def test_new_test_columns_blank_in_a_core_run():
    t = [{"url": "https://www.example.gov/", "entity_id": "ex", "entity_name": "E"}]
    row = ex.build_rows(t, _rows(), [], [], PSL)[0]
    for c in ("ai_c1_verdict", "ai_d2_count", "ai_d2_api_count", "ai_d3_rest_index", "ai_d5_verdict",
              "ai_d7_admitted", "ai_r2_markdown_served", "ai_m5_present", "ai_f3_verdict",
              "ai_run_place", "ai_run_ipv4", "ai_run_network_changed", "ny_kind"):
        assert row[c] is None, c


def test_network_columns():
    t = [{"url": "https://data.example.gov/", "entity_id": None, "entity_name": "D"}]
    row = ex.build_rows(t, _full_rows(), [], [], PSL, run=RUN)[0]
    assert row["ai_run_tier"] == "full" and row["ai_run_place"] == "home"
    assert row["ai_run_provider"] == "EXAMPLE-NET"
    assert row["ai_run_asn_start"] == "AS64500" and row["ai_run_asn_end"] == "AS64500"
    assert row["ai_run_ipv4"] is True and row["ai_run_ipv6"] is False
    assert row["ai_run_network_changed"] is False and row["ai_run_ip_changed"] is True
    assert "192.0.2.1" not in json.dumps(row)                       # the address is never exported
    # a run before per-family readings: only the family its one reading used is known
    old = {"network": {"place": "home", "start": {"asn": "AS64500", "public_ip": "2001:db8::1"},
                       "end": {"asn": None, "error": "URLError: down"}, "changed": None}}
    row = ex.build_rows(t, _full_rows(), [], [], PSL, run=old)[0]
    assert row["ai_run_ipv6"] is True and row["ai_run_ipv4"] is None
    assert row["ai_run_asn_end"] is None and row["ai_run_network_changed"] is None


ADDRESSES = """address,owning_body_id,owning_body,kind,level_or_entity_type,source,date_found,notes,platform
https://www.example.gov/services,ex-agency,Example Agency,section,state agency,test,2026-11-10,,
https://www.example.gov/,ex-agency,Example Agency,main site,state agency,test,2026-11-10,,
data.example.gov,ex-agency,Example Agency,data portal,state agency,test,2026-11-10,,Socrata
"""


def test_addresses_csv_gives_one_row_per_host(tmp_path):
    f = tmp_path / "addresses.csv"
    f.write_text(ADDRESSES)
    targets = ex.load_targets(str(f))
    by_url = {t["url"]: t for t in targets}
    assert sorted(by_url) == ["https://data.example.gov", "https://www.example.gov/"]
    main = by_url["https://www.example.gov/"]                       # the main site, not the section
    assert main["kind"] == "main site" and main["kinds"] == ["main site", "section"]
    assert main["entity_id"] == "ex-agency" and main["entity_name"] == "Example Agency"
    portal = by_url["https://data.example.gov"]
    assert portal["platform"] == "Socrata" and portal["kinds"] == ["data portal"]
    rows = ex.build_rows(targets, _full_rows(), [], [], PSL, run=RUN)
    prow = next(r for r in rows if r["initial_domain"] == "data.example.gov")
    assert prow["ny_kind"] == "data portal" and prow["ny_platform"] == "Socrata"
    assert prow["ny_owning_body"] == "Example Agency" and prow["ny_entity_id"] == "ex-agency"
    assert prow["ny_level"] == "state agency" and prow["ny_kinds"] == ["data portal"]


def _write_run(d, run=RUN):
    d.mkdir()
    rows = _full_rows("data.example.gov")
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "run.json").write_text(json.dumps(run))


def test_only_run_hosts_and_out_dir(tmp_path):
    run = tmp_path / "run"
    _write_run(run)
    targets = tmp_path / "t.json"
    targets.write_text(json.dumps([{"url": "https://data.example.gov/", "entity_name": "D"},
                                   {"url": "https://other.example.gov/", "entity_name": "O"}]))
    assert ex.main(["--run-dir", str(run), "--targets-file", str(targets)]) == 0
    assert len(json.loads((run / "sites.json").read_text())) == 2   # default: every target
    out = tmp_path / "out"
    assert ex.main(["--run-dir", str(run), "--targets-file", str(targets), "--only-run-hosts",
                    "--out-dir", str(out)]) == 0
    rows = json.loads((out / "sites.json").read_text())
    assert [r["initial_domain"] for r in rows] == ["data.example.gov"]
    header = (out / "sites.csv").read_text().splitlines()[0].split(",")
    assert "ai_w4_claudebot_status" in header and "ai_run_place" in header


def test_dictionary_covers_every_full_tier_identity_and_new_column():
    fids = {r["field id"] for r in ex.dictionary_rows()}
    import probe_bot_access
    for i in probe_bot_access.tier_identities("full"):
        assert "ai_w4_{}_status".format(i["key"]) in fids
        assert "ai_w4_{}_admitted".format(i["key"]) in fids
    assert set(ex.columns(identities=ex.dictionary_identities())) - fids == set()
    for c in ("ai_c1_cms", "ai_d7_verdict", "ai_r2_markdown_served", "ai_m5_present", "ai_f3_broken",
              "ny_kind", "ny_platform", "ny_owning_body", "ai_run_asn_start", "ai_run_ipv6"):
        assert c in fids, c
