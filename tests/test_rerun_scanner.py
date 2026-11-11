"""Offline tests for the re-run scanner (issues #43 and #44): the crawler list as the source of
what is sent, the pre-run and VPN guards, the network record, the address inventory, and the
catalog 0.2 tests (C1, D2–D7, R2's advertised alternate). No network: transports and the
network lookup are fakes. Hosts are *.example.gov placeholders, never real sites."""

import csv
import json
import pathlib
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ai_readability as ar  # noqa: E402
import crawler_list as cl  # noqa: E402
import network_record as nr  # noqa: E402
import probe_bot_access as p  # noqa: E402
from test_ai_readability import HOME, URLSET, fake_transport, resp  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "ai_readability"
CHECK_DAY = date(2026, 11, 10)
ROWS = cl.load()
SENT, NOT_SENT = cl.build_identities(ROWS)
BY_KEY = {i["key"]: i for i in SENT}


# ============================================================ crawlers.csv -> what is sent
def test_every_vendor_identity_comes_from_crawlers_csv():
    vendor = [i for i in p.IDENTITIES if i["key"] not in p.CONTROL_KEYS]
    allowed = cl.published_labels(ROWS)
    assert vendor and all(i["ua"] in allowed for i in vendor)
    assert {i["key"] for i in vendor} == set(BY_KEY)
    assert set(p.CONTROL_KEYS) == {"research_betanyc", "mechanism_probe_chrome", "baseline_default"}


def test_googlebot_sends_the_published_label_with_no_version():
    ua = BY_KEY["googlebot"]["ua"]
    assert ua == "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    assert "Chrome/126.0.0.0" not in " ".join(i["ua"] or "" for i in p.IDENTITIES
                                              if i["key"] != "mechanism_probe_chrome")


def test_no_sent_label_carries_a_version_stand_in():
    for i in p.IDENTITIES:
        assert "W.X.Y.Z" not in (i["ua"] or ""), i["key"]


def test_stand_in_only_rows_are_not_sent_and_say_why():
    held = {n["name"]: n for n in NOT_SENT}
    for name in ("Google-Agent", "Amazonbot", "Amzn-SearchBot", "Amzn-User"):
        assert name in held and "stand-in" in held[name]["reason"], name
        assert cl.key_for(name) not in BY_KEY


def test_token_only_names_are_sent_bare_and_marked():
    for key, name in (("claudebot", "ClaudeBot"), ("claude_user", "Claude-User"),
                      ("claude_searchbot", "Claude-SearchBot")):
        assert BY_KEY[key]["ua"] == name
        assert BY_KEY[key]["label_kind"] == "token-only"
    assert all(i["label_kind"] == "published" for i in SENT
               if i["name"] not in ("ClaudeBot", "Claude-User", "Claude-SearchBot"))


def test_perplexity_labels_are_the_current_published_ones():
    rows = {r["name"]: r for r in ROWS}
    for key, name in (("perplexitybot", "PerplexityBot"), ("perplexity_user", "Perplexity-User")):
        assert BY_KEY[key]["ua"] == rows[name]["ua_exact"]
    assert BY_KEY["perplexitybot"]["ua"] == \
        "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)"


def test_meta_sends_its_short_form_and_examples_are_marked():
    assert BY_KEY["meta_externalagent"]["ua"] == "meta-externalagent/1.1"
    assert BY_KEY["meta_externalagent"]["example"] is True
    assert BY_KEY["claude_agent"]["example"] is True and BY_KEY["gptbot"]["example"] is False
    skipped = [c for c in BY_KEY["meta_externalagent"]["candidates"] if not c["used"]]
    assert skipped and "relative-link" in skipped[0]["note"]


def test_rules_only_names_are_read_in_robots_never_sent():
    rules_only = [r["name"] for r in ROWS if r["send"] == "rules-only" and not cl.is_pseudo_name(r["name"])]
    sent = " ".join(i["ua"] or "" for i in p.IDENTITIES)
    for name in rules_only:
        assert name in ar.AI_TOKENS, name
        assert cl.key_for(name) not in {i["key"] for i in p.IDENTITIES}, name
    assert "Bytespider" not in sent and "GrokBot" not in sent


def test_every_sent_name_has_a_class_and_ai_names_count_toward_w4():
    for i in SENT:
        assert i["klass"], i["name"]
    assert BY_KEY["googlebot"]["klass"] == "search" and BY_KEY["claudebot"]["klass"] == "ai_training"


def test_choose_label_rules():
    pick = cl.choose_label({"name": "X", "ua_exact": "a X/W.X.Y.Z || X/1.0 (+/doc) || X/1.0 || X/2.0"})
    assert pick["label"] == "X/1.0" and pick["label_kind"] == "published"
    notes = [c["note"] for c in pick["candidates"]]
    assert "stand-in" in notes[0] and "relative-link" in notes[1] and notes[2] is None \
        and "earlier" in notes[3]
    assert cl.choose_label({"name": "X", "ua_exact": "X/W.X.Y.Z"})["label"] is None
    assert cl.choose_label({"name": "X", "ua_exact": "token only"})["label"] == "X"


# ======================================================================= the pre-run guard
def test_guard_passes_the_checked_list_on_its_check_day():
    assert p.pre_run_guard(p.tier_identities("full"), ROWS, CHECK_DAY) == []


def test_guard_refuses_a_list_older_than_30_days():
    reasons = p.pre_run_guard(p.tier_identities("core"), ROWS, CHECK_DAY + timedelta(days=31))
    assert reasons and all("days old" in r for r in reasons)
    assert p.pre_run_guard(p.tier_identities("core"), ROWS, CHECK_DAY + timedelta(days=30)) == []


def test_guard_refuses_a_label_not_in_the_list_but_excepts_controls():
    rogue = dict(BY_KEY["gptbot"], ua="Mozilla/5.0 GPTBot/9.9 (made up)")
    reasons = p.pre_run_guard([p._CTRL["research_betanyc"], rogue], ROWS, CHECK_DAY)
    assert len(reasons) == 1 and "not in crawlers.csv" in reasons[0]
    stand_in = dict(BY_KEY["googlebot"], ua=ROWS[0]["ua_exact"].split(" || ")[0])
    assert any("stand-in" in r for r in p.pre_run_guard([stand_in], ROWS, CHECK_DAY))
    unclassed = dict(BY_KEY["gptbot"], klass=None)
    assert any("no class" in r for r in p.pre_run_guard([unclassed], ROWS, CHECK_DAY))


def test_guard_refuses_a_list_that_fails_its_own_checks():
    bad = [dict(r) for r in ROWS]
    bad[0]["send"] = "maybe"
    bad[1]["reason_not_sent"] = ""
    bad[2]["name"] = bad[3]["name"]
    reasons = p.pre_run_guard([], bad, CHECK_DAY)
    assert any("send='maybe'" in r for r in reasons)
    assert any("listed twice" in r for r in reasons)


def write_crawlers(tmp_path, mutate):
    rows = [dict(r) for r in ROWS]
    mutate(rows)
    path = tmp_path / "crawlers.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cl.HEADER)
        w.writeheader()
        w.writerows(rows)
    return path


def targets_file(tmp_path, hosts=("www.example.gov",)):
    tf = tmp_path / "targets.json"
    tf.write_text(json.dumps([{"entity_id": None, "entity_name": "City of Example",
                               "url": "https://{}/".format(h)} for h in hosts]))
    return tf


def no_net(*a, **k):
    raise AssertionError("a refused or dry run touched the network")


def test_runner_refuses_to_start_on_a_stale_list_and_prints_why(tmp_path, capsys):
    args = p.build_parser().parse_args(["--tier", "core", "--targets-file",
                                        str(targets_file(tmp_path)), "--run-dir",
                                        str(tmp_path / "run")])
    assert p.run_readability(args, transport=no_net, netlookup=no_net,
                             today=CHECK_DAY + timedelta(days=45)) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "days old" in err
    assert not (tmp_path / "run").exists()


def test_runner_reads_the_crawlers_file_it_is_given(tmp_path, capsys):
    def drop_label(rows):
        for r in rows:
            if r["name"] == "GPTBot":
                r["send"] = "rules-only"
                r["reason_not_sent"] = "test"
    path = write_crawlers(tmp_path, drop_label)
    args = p.build_parser().parse_args(["--tier", "full", "--targets-file",
                                        str(targets_file(tmp_path)), "--crawlers", str(path),
                                        "--dry-run"])
    assert p.run_readability(args, transport=no_net, today=CHECK_DAY) == 0
    out = capsys.readouterr().out
    ids = next(ln for ln in out.splitlines() if ln.startswith("identities:"))
    assert "gptbot" not in ids and "claudebot" in ids


# ========================================================================= the VPN guard
def vpn_args(tmp_path, extra):
    return p.build_parser().parse_args(["--tier", "core", "--targets-file",
                                        str(targets_file(tmp_path, ("a.example.gov", "b.example.gov"))),
                                        "--vantage", "vpn", "--dry-run"] + extra)


def test_vpn_full_run_is_refused(tmp_path, capsys):
    assert p.run_readability(vpn_args(tmp_path, []), transport=no_net, today=CHECK_DAY) == 2
    assert "--only-hosts" in capsys.readouterr().err


def test_vpn_named_small_set_is_allowed(tmp_path):
    assert p.run_readability(vpn_args(tmp_path, ["--only-hosts", "a.example.gov"]),
                             transport=no_net, today=CHECK_DAY) == 0


def test_vpn_guard_caps_the_set():
    class A:
        vantage, only_hosts = "vpn", "x"
    assert p.vpn_guard(A, [{"host": str(i)} for i in range(p.VPN_MAX_HOSTS)]) == []
    assert "limited" in p.vpn_guard(A, [{"host": str(i)} for i in range(p.VPN_MAX_HOSTS + 1)])[0]

    class B:
        vantage, only_hosts = "office", None
    assert p.vpn_guard(B, [{"host": "x"}] * 500) == []


# ======================================================================= network record
def fake_ripestat(ip, asn, holder, fail_at=None):
    """A fake RIPEstat: the three documented calls, keyed by their path."""
    def get(url):
        call = url.split("/data/")[1].split("/")[0]
        assert "sourceapp=" + nr.SOURCEAPP in url
        if call == fail_at:
            raise OSError("timed out")
        return {"whats-my-ip": {"status": "ok", "data": {"ip": ip}},
                "network-info": {"status": "ok", "data": {"asns": [asn.lstrip("AS")],
                                                          "prefix": ip.rsplit(".", 1)[0] + ".0/24"}},
                "as-overview": {"status": "ok", "data": {"holder": holder}}}[call]
    return get


# Known values, used ONLY as fixtures (never in code): office is Charter/Spectrum AS12271, home
# is Verizon Business AS701. The documentation addresses below are RFC 5737 examples.
OFFICE = ("198.51.100.7", "AS12271", "TWC-12271-NYC - Charter Communications Inc")
HOME_NET = ("203.0.113.9", "AS701", "UUNET - MCI Communications Services, Inc. d/b/a Verizon Business")


def test_lookup_reads_documented_fields():
    rec = nr.lookup(get=fake_ripestat(*OFFICE), now=lambda: "t0")
    assert (rec["public_ip"], rec["asn"], rec["provider"]) == OFFICE and rec["error"] is None
    assert rec["time"] == "t0" and rec["service"] == "RIPEstat Data API"
    rec = nr.lookup(get=fake_ripestat(*HOME_NET))
    assert rec["asn"] == "AS701" and "Verizon" in rec["provider"]


def test_lookup_all_reads_both_families_and_records_a_missing_one():
    def get_for(fam):
        if fam == "ipv4":
            return fake_ripestat(*HOME_NET)
        def no_v6(url):
            raise OSError("no IPv6 address for stat.ripe.net")
        return no_v6
    rec = nr.lookup_all(get=fake_ripestat(*HOME_NET), get_for=get_for, now=lambda: "t0")
    assert rec["asn"] == "AS701" and set(rec["by_family"]) == {"ipv4", "ipv6"}
    assert rec["by_family"]["ipv4"]["public_ip"] == HOME_NET[0]
    v6 = rec["by_family"]["ipv6"]
    assert v6["public_ip"] is None and "no IPv6" in v6["error"]
    # a "forced IPv6" reading that answers from an IPv4 address is an error, not an IPv6 address
    rec = nr.lookup_all(get=fake_ripestat(*HOME_NET), get_for=lambda fam: fake_ripestat(*HOME_NET))
    v6 = rec["by_family"]["ipv6"]
    assert v6["public_ip"] is None and v6["seen_ip"] == HOME_NET[0] and "answered from" in v6["error"]
    assert rec["by_family"]["ipv4"]["public_ip"] == HOME_NET[0]


def test_lookup_failure_is_recorded_never_guessed():
    rec = nr.lookup(get=fake_ripestat(*OFFICE, fail_at="network-info"))
    assert rec["public_ip"] == OFFICE[0] and rec["asn"] is None and rec["provider"] is None
    assert "timed out" in rec["error"]
    rec = nr.lookup(get=lambda url: {"status": "maintenance", "data": {}})
    assert rec["public_ip"] is None and "maintenance" in rec["error"]


def test_finish_record_flags_a_network_change():
    start = nr.start_record("office", nr.lookup(get=fake_ripestat(*OFFICE)))
    assert start["place"] == "office"
    done = nr.finish_record(start, nr.lookup(get=fake_ripestat(*HOME_NET)))
    assert done["changed"] == {"asn": True, "public_ip": True}
    same = nr.finish_record(nr.start_record("home", nr.lookup(get=fake_ripestat(*HOME_NET))),
                            nr.lookup(get=fake_ripestat(*HOME_NET)))
    assert same["changed"] == {"asn": False, "public_ip": False}
    unknown = nr.finish_record(nr.start_record("home", nr.lookup(get=fake_ripestat(*HOME_NET))),
                               {"error": "x"})
    assert unknown["changed"] is None
    assert nr.start_record("residential", {})["place"] is None


def run_core(tmp_path, table, targets, extra=(), netlookup=None, tier="core"):
    tf = tmp_path / "targets.json"
    tf.write_text(json.dumps(targets))
    calls = []
    args = p.build_parser().parse_args(["--tier", tier, "--targets-file", str(tf), "--run-dir",
                                        str(tmp_path / "run"), "--machine", "test"] + list(extra))
    args._argv = []
    code = p.run_readability(args, transport=fake_transport(table, calls), today=CHECK_DAY,
                             netlookup=netlookup,
                             monitor=p.NetworkMonitor(check=lambda: True, sleep=lambda s: None),
                             sleep=lambda s: None,
                             pacing={"pace_s": 0, "jitter_s": 0, "host_floor_s": 0,
                                     "governors": {}, "retry_backoff_s": 0})
    return code, tmp_path / "run", calls


def good_host(host):
    base = "https://" + host
    return {(base + "/robots.txt", None): resp(200, "User-agent: *\nDisallow: /admin\n"
                                                    "Sitemap: {}/sitemap.xml\n".format(base),
                                               "text/plain"),
            (base + "/llms.txt", None): resp(200, "# Example\n\n> Official site.\n", "text/plain"),
            (base + "/sitemap.xml", None): resp(200, URLSET, "application/xml"),
            (base + "/", None): resp(200, HOME),
            ("http://{}/".format(host), None): resp(301, "", headers={"Location": base + "/"})}


def test_run_json_carries_the_network_record_start_and_end(tmp_path):
    readings = iter([nr.lookup(get=fake_ripestat(*OFFICE), now=lambda: "start"),
                     nr.lookup(get=fake_ripestat(*OFFICE), now=lambda: "end")])
    code, d, calls = run_core(tmp_path, good_host("www.example.gov"),
                              [{"entity_id": "e1", "entity_name": "City of Example",
                                "url": "https://www.example.gov/"}],
                              extra=["--vantage", "office"], netlookup=lambda: next(readings))
    assert code == 0
    net = json.loads((d / "run.json").read_text())["network"]
    assert net["place"] == "office"
    assert net["start"]["time"] == "start" and net["end"]["time"] == "end"
    assert net["start"]["asn"] == "AS12271" and net["changed"] == {"asn": False, "public_ip": False}
    assert not any("ripe" in c[0] for c in calls), "the lookup never goes through the probe"
    rows = [json.loads(x) for x in (d / "results.jsonl").read_text().splitlines()]
    assert rows and all(r["place"] == "office" for r in rows)


def test_run_json_records_a_failed_lookup(tmp_path):
    code, d, _ = run_core(tmp_path, good_host("www.example.gov"),
                          [{"entity_id": "e1", "entity_name": "x", "url": "https://www.example.gov/"}],
                          extra=["--vantage", "home"])
    net = json.loads((d / "run.json").read_text())["network"]
    assert code == 0 and net["start"]["error"] and net["start"]["asn"] is None
    assert net["changed"] is None
    run = json.loads((d / "run.json").read_text())
    assert run["crawlers_csv"]["sha256"] and run["identities_not_sent"]
    kinds = {i["key"]: i["label_kind"] for i in run["identities"]}
    assert kinds["research_betanyc"] == "control"


def test_core_tier_still_costs_eleven_requests_and_adds_c1_d2(tmp_path):
    code, d, calls = run_core(tmp_path, good_host("www.example.gov"),
                              [{"entity_id": "e1", "entity_name": "x",
                                "url": "https://www.example.gov/"}])
    assert code == 0 and len(calls) == 11
    tests = {json.loads(x)["test"] for x in (d / "results.jsonl").read_text().splitlines()}
    assert {"C1", "D2"} <= tests and not ({"D3", "D4", "D5"} & tests)


# ===================================================================== address inventory
def test_addresses_csv_is_read_with_kinds():
    t = p.load_targets(str(FIX / "addresses.csv"))
    assert len(t) == 5
    assert {x["kind"] for x in t} == set(p.ADDRESS_KINDS)
    assert t[4]["url"] == "https://blog.example.gov" and t[4]["entity_name"] == "City of Example"
    jobs = {j["host"]: j for j in p.plan_targets(t)}
    assert jobs["www.example.gov"]["url"] == "https://www.example.gov/", "main site, not section"
    assert jobs["www.example.gov"]["kinds"] == ["main site", "section"]
    assert jobs["data.example.gov"]["kinds"] == ["data portal"]


def test_addresses_csv_rejects_an_unknown_kind(tmp_path):
    f = tmp_path / "addresses.csv"
    f.write_text("address,owning body,kind,level,source,date found,notes\n"
                 "https://x.example.gov/,X,homepage,city,t,2026-11-10,\n")
    try:
        p.load_targets(str(f))
    except ValueError as e:
        assert "homepage" in str(e)
    else:
        raise AssertionError("an unknown kind must stop the load")


def test_default_targets_fall_back_to_websites_json(monkeypatch, tmp_path):
    monkeypatch.setattr(p, "ADDRESSES", tmp_path / "missing.csv")
    assert p.default_targets_file() == p.WEBSITES
    monkeypatch.setattr(p, "ADDRESSES", FIX / "addresses.csv")
    assert p.default_targets_file() == FIX / "addresses.csv"


# ============================================================================ C1 and D2
WP_HOME = ("<html lang='en'><head><title>Town of Example</title>"
           "<meta name='generator' content='WordPress 6.6'>"
           "<link rel='https://api.w.org/' href='https://wp.example.gov/wp-json/'>"
           "<link rel='alternate' type='application/rss+xml' href='/feed/'>"
           "<link rel='alternate' type='text/markdown' href='/index.md'>"
           "</head><body><main><h1>Town</h1>"
           "<a href='https://data.ny.gov/'>Data</a> <a href='/open-data/'>Open Data</a>"
           "<a href='https://gis.example.gov/arcgis/rest/services'>Maps</a>"
           "<a href='https://developer.example.gov/'>Developers</a>"
           "<a href='/about/'>About</a></main></body></html>")


def test_c1_keeps_the_gsa_value_and_fills_gaps_from_the_homepage():
    r = resp(200, WP_HOME, url="https://wp.example.gov/")
    v, ev = ar.test_c1(r, None, gsa_cms="Drupal")
    assert v == "reported" and ev["cms"] == "Drupal" and ev["source"] == "gsa"
    assert "WordPress" in " ".join(ev["homepage_platforms"])
    v, ev = ar.test_c1(r)
    assert ev["source"] == "homepage" and "WordPress" in ev["cms"]
    bare = resp(200, "<html><head><link rel='https://api.w.org/' href='/wp-json/'></head></html>",
                url="https://wp.example.gov/")
    assert ar.test_c1(bare)[1]["cms"] == "WordPress", "the REST link alone shows WordPress"
    assert ar.test_c1(resp(403, "denied"), None, "Drupal")[1]["cms"] == "Drupal"
    assert ar.test_c1(resp(200, "<html><body>plain</body></html>"))[1]["cms"] is None


def test_wp_api_root_from_link_header_or_link_tag():
    r = resp(200, "<html></html>", url="https://wp.example.gov/",
             headers={"Link": '<https://wp.example.gov/wp-json/>; rel="https://api.w.org/"'})
    assert ar.wp_api_root(r) == "https://wp.example.gov/wp-json/"
    r = resp(200, "<html></html>", url="https://wp.example.gov/",
             headers={"Link": '<https://wp.example.gov/?rest_route=/>; rel="https://api.w.org/"'})
    assert ar.wp_api_root(r) == "https://wp.example.gov/?rest_route=/"
    page = ar.parse_page(WP_HOME)
    assert ar.wp_api_root(resp(200, WP_HOME, url="https://wp.example.gov/"), page) == \
        "https://wp.example.gov/wp-json/"
    assert ar.wp_api_root(resp(200, "<html></html>")) is None


def test_d2_sorts_links_already_fetched_without_a_request():
    portals = {"hosts": {"data.ny.gov"}, "host_suffixes": (), "url_prefixes": ()}
    llms = resp(200, "# T\n> s\n- [Open data API](https://api.example.gov/v1/)\n", "text/plain")
    v, ev = ar.test_d2(resp(200, WP_HOME, url="https://wp.example.gov/"), None, portals, llms)
    assert v == "reported"
    kinds = {l["url"]: l["kinds"] for l in ev["links"]}
    assert "open_data" in kinds["https://data.ny.gov/"]
    assert "open_data" in kinds["https://wp.example.gov/open-data/"]
    assert "gis_service" in kinds["https://gis.example.gov/arcgis/rest/services"]
    assert "developer" in kinds["https://developer.example.gov/"]
    assert "feed" in kinds["https://wp.example.gov/feed/"]
    assert "api" in kinds["https://wp.example.gov/wp-json/"]
    assert "api" in kinds["https://api.example.gov/v1/"]
    assert "https://wp.example.gov/about/" not in kinds
    assert ar.test_d2(resp(403, "denied"))[0] == "blocked"


def test_r2_records_an_advertised_markdown_alternate_at_no_cost():
    home = resp(200, WP_HOME, url="https://wp.example.gov/",
                headers={"Link": '</page.md>; rel="alternate"; type="text/markdown"'})
    _, ev = ar.test_r2(resp(200, "<html></html>"), resp(404, ""), home, ar.parse_page(WP_HOME))
    vias = {a["via"] for a in ev["advertised_alternates"]}
    assert vias == {"Link header", "<link>"} and ev["markdown_served"] is False


# ============================================================================ D3 and D4
def test_d3_reads_the_documented_namespaces_field():
    idx = json.dumps({"name": "Town", "namespaces": ["oembed/1.0", "wp/v2"], "routes": {"/": {}}})
    v, ev = ar.test_d3(resp(200, idx, "application/json"))
    assert v == "reported" and ev["rest_index"] is True and ev["namespaces"] == ["oembed/1.0", "wp/v2"]
    assert ar.test_d3(resp(200, "<html>not json</html>"))[1]["rest_index"] is False
    assert ar.test_d3(resp(401, "{}", "application/json"))[0] == "blocked"


def test_d4_reads_an_rfc_9727_linkset():
    doc = json.dumps({"linkset": [{"anchor": "https://www.example.gov/api/v1",
                                   "service-desc": [{"href": "https://www.example.gov/openapi.json"}]},
                                  {"anchor": "https://www.example.gov/.well-known/api-catalog",
                                   "item": [{"href": "https://www.example.gov/api/v1"},
                                            {"href": "https://www.example.gov/api/v2"}]}]})
    ct = 'application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"'
    v, ev = ar.test_d4(resp(200, doc, ct))
    assert v == "reported" and ev["present"] and ev["items"] == 2
    assert ev["media_type_ok"] and ev["profile_rfc9727"] and "service-desc" in ev["relations"]
    _, ev = ar.test_d4(resp(200, doc, "application/json"))
    assert ev["present"] and not ev["media_type_ok"]
    assert ar.test_d4(resp(404, "nope"))[1]["present"] is False
    assert ar.test_d4(resp(200, "<html>soft 404</html>"))[1]["present"] is False


# ======================================================================== D5–D7 portals
def test_portal_platform_from_the_homepage_only():
    assert ar.portal_platform("x.hub.arcgis.com", None)["platform"] == "arcgis_hub"
    ckan = resp(200, "<html><head><meta name='generator' content='ckan 2.10.4'></head></html>")
    assert ar.portal_platform("data.example.gov", ckan)["platform"] == "ckan"
    soc = resp(200, "<html><script src='https://x/socrata-site.js'></script></html>")
    assert ar.portal_platform("data.example.gov", soc)["platform"] == "socrata"
    assert ar.portal_platform("data.example.gov", resp(200, "<html></html>"))["platform"] is None
    assert ar.portal_platform("data.example.gov", None, hint="CKAN")["platform"] == "ckan"


def test_portal_catalog_paths_are_the_documented_ones():
    assert ar.portal_catalog_url("socrata", "d.example.gov") == \
        "https://d.example.gov/api/views/metadata/v1?limit=1&page=1"
    assert ar.portal_catalog_url("arcgis_hub", "d.example.gov") == \
        "https://d.example.gov/api/feed/dcat-us/1.1.json"
    assert ar.portal_catalog_url("ckan", "d.example.gov") == \
        "https://d.example.gov/api/3/action/package_list?limit=1"
    assert ar.portal_catalog_url(None, "d.example.gov") is None
    assert ar.dataset_lookup_url("socrata", "d.example.gov") == \
        "https://api.us.socrata.com/api/catalog/v1?domains=d.example.gov&limit=1"
    assert ar.dataset_lookup_url("ckan", "d.example.gov") is None


DCAT = json.dumps({"conformsTo": "https://project-open-data.cio.gov/v1.1/schema",
                   "dataset": [{"title": "Trees", "landingPage": "https://d.example.gov/datasets/trees"}]})


def test_d5_pass_rules_per_platform():
    assert ar.test_d5("arcgis_hub", resp(200, DCAT, "application/json"))[0] == "pass"
    assert ar.test_d5("arcgis_hub", resp(200, json.dumps({"dataset": []}), "application/json"))[0] == "fail"
    ok = json.dumps({"help": "h", "success": True, "result": ["trees"]})
    assert ar.test_d5("ckan", resp(200, ok, "application/json"))[0] == "pass"
    assert ar.test_d5("ckan", resp(200, json.dumps({"success": False}), "application/json"))[0] == "fail"
    assert ar.test_d5("socrata", resp(200, "[]", "application/json"))[0] == "pass"
    assert ar.test_d5("socrata", resp(200, "<html></html>"))[0] == "fail"
    assert ar.test_d5("socrata", resp(403, "denied"))[0] == "blocked"
    assert ar.test_d5(None, None)[0] == "na"


def test_dataset_page_comes_from_a_documented_field():
    assert ar.dataset_page_url("arcgis_hub", "d.example.gov", resp(200, DCAT)) == \
        "https://d.example.gov/datasets/trees"
    assert ar.dataset_page_url("ckan", "d.example.gov",
                               resp(200, json.dumps({"success": True, "result": ["a b"]}))) == \
        "https://d.example.gov/dataset/a%20b"
    disc = json.dumps({"results": [{"resource": {"id": "abcd-1234"},
                                    "permalink": "https://d.example.gov/d/abcd-1234",
                                    "link": "https://d.example.gov/x/abcd-1234"}],
                       "resultSetSize": 1})
    assert ar.dataset_page_url("socrata", "d.example.gov", resp(200, "[]"), resp(200, disc)) == \
        "https://d.example.gov/d/abcd-1234"
    assert ar.dataset_page_url("ckan", "d.example.gov", resp(200, "{}")) is None


def test_d6_is_r1_on_the_dataset_page():
    v, ev = ar.test_d6("https://d.example.gov/dataset/trees", resp(200, HOME))
    assert v == "pass" and ev["dataset_url"].endswith("/trees")
    assert ar.test_d6(None, None)[0] == "na"


def test_d7_is_w4_per_identity_on_the_catalog():
    per = {"research_betanyc": resp(200, "[]"), "gptbot": resp(403, "denied"),
           "claudebot": resp(200, "[]")}
    verdicts, agg = ar.test_d7(per, {"research_betanyc": "baseline_honest",
                                     "gptbot": "ai_training", "claudebot": "ai_training"})
    assert verdicts["gptbot"][0] == "blocked" and verdicts["claudebot"][0] == "pass"
    assert agg[0] == "partial"


# ============================================================== full tier, end to end
def portal_and_wp_table():
    t = good_host("www.example.gov")
    t[("https://www.example.gov/", None)] = resp(200, WP_HOME.replace("wp.example.gov", "www.example.gov"))
    t[("https://www.example.gov/wp-json/", None)] = resp(
        200, json.dumps({"namespaces": ["wp/v2"], "routes": {}}), "application/json")
    t.update(good_host("data.example.gov"))
    t[("https://data.example.gov/", None)] = resp(
        200, "<html><head><meta name='generator' content='ckan 2.10'></head><body>" +
        "word " * 200 + "</body></html>")
    t[("https://data.example.gov/api/3/action/package_list?limit=1", None)] = resp(
        200, json.dumps({"success": True, "result": ["trees"]}), "application/json")
    t[("https://data.example.gov/api/3/action/package_list?limit=1", "gptbot")] = resp(403, "denied")
    t[("https://data.example.gov/dataset/trees", None)] = resp(200, HOME)
    return t


def test_full_tier_runs_d3_d4_and_the_portal_tests(tmp_path):
    code, d, calls = run_core(tmp_path, portal_and_wp_table(),
                              [{"entity_id": "e1", "entity_name": "City of Example",
                                "url": "https://www.example.gov/", "kind": "main site"},
                               {"entity_id": "e2", "entity_name": "City of Example",
                                "url": "https://data.example.gov/", "kind": "data portal"}],
                              tier="full")
    assert code == 0
    rows = [json.loads(x) for x in (d / "results.jsonl").read_text().splitlines()]
    by = {(r["host"], r["test"], r["identity"]): r for r in rows}
    assert by[("www.example.gov", "D3", "research_betanyc")]["evidence"]["rest_index"] is True
    assert ("www.example.gov", "D4", "research_betanyc") in by
    assert ("data.example.gov", "D3", "research_betanyc") in by          # na: not WordPress
    assert by[("data.example.gov", "D5", "research_betanyc")]["verdict"] == "pass"
    assert by[("data.example.gov", "D6", "research_betanyc")]["verdict"] == "pass"
    assert by[("data.example.gov", "D7", "gptbot")]["verdict"] == "blocked"
    assert by[("data.example.gov", "D7", ar.W4_AGGREGATE_IDENTITY)]["verdict"] == "partial"
    assert not any(r["test"].startswith("D5") for r in rows if r["host"] == "www.example.gov")
    n = len(p.tier_identities("full"))
    catalog_calls = [c for c in calls if "package_list" in c[0]]
    assert len(catalog_calls) == n, "the catalog once per identity"
    assert sum(1 for c in calls if c[0].endswith("/.well-known/api-catalog")) == 2
    assert sum(1 for c in calls if c[0].endswith("/wp-json/")) == 1
    # the request count lies inside the dry-run estimate for each host
    per_host = {}
    for c in calls:
        h = c[0].split("/")[2]
        per_host[h] = per_host.get(h, 0) + 1
    www = p.requests_per_host("full", n, {"kinds": ["main site"]})
    portal = p.requests_per_host("full", n, {"kinds": ["data portal"]})
    assert www[0] <= per_host["www.example.gov"] <= www[1]
    assert portal[0] <= per_host["data.example.gov"] <= portal[1]


# ================================================================= the dry-run estimate
def test_run_estimate_matches_the_pacing():
    jobs = [{"host": "h{}.example.gov".format(i)} for i in range(100)]
    est = p.run_estimate(jobs, "core", 6, workers=1, pace_s=6.0, floor_s=30.0, governors={})
    assert est["requests_min"] == est["requests_max"] == 1100
    assert est["hours_min"] == round(1100 * 7.0 / 3600, 1)
    est4 = p.run_estimate(jobs, "core", 6, workers=4, pace_s=6.0, floor_s=30.0, governors={})
    assert est4["hours_min"] == round(1100 * 7.0 / 4 / 3600, 1)
    # one host alone: its own floor sets the time, whatever the worker count
    one = p.run_estimate(jobs[:1], "core", 6, workers=8, pace_s=6.0, floor_s=30.0, governors={})
    assert one["hours_min"] == round(10 * 30.0 / 3600, 1)
    full = p.run_estimate(jobs + [{"host": "d.example.gov", "kinds": ["data portal"]}], "full",
                          24, workers=1)
    assert full["portals"] == 1 and full["requests_max"] > full["requests_min"]


def test_dry_run_prints_requests_and_hours(tmp_path, capsys):
    args = p.build_parser().parse_args(["--tier", "full", "--targets-file",
                                        str(FIX / "addresses.csv"), "--dry-run"])
    assert p.run_readability(args, transport=no_net, netlookup=no_net, today=CHECK_DAY) == 0
    out = capsys.readouterr().out
    assert "requests≈" in out and "estimated wall clock" in out and " h with 1 worker" in out
    assert "data portals (D5–D7): 1" in out and "no network used" in out
