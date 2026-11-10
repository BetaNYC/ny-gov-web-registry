"""Offline tests for the AI-readability catalog v0.1 (docs/ai-readability/tests.md).

No network anywhere: parsers get fixture strings, and the runner gets a fake transport that
answers from a dict. Hosts are *.example.gov / example.org placeholders, never real sites.
"""

import gzip
import json
import pathlib
import signal
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import ai_readability as ar  # noqa: E402
import probe_bot_access as p  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "ai_readability"
TODAY = date(2026, 11, 6)


def resp(status=200, body="", ctype="text/html; charset=utf-8", headers=None, **kw):
    cls, block = p.classify_body(body)
    h = {"content-type": ctype}
    h.update({k.lower(): v for k, v in (headers or {}).items()})
    r = {"url": kw.pop("url", "https://example.gov/"), "final_url": kw.pop("final_url", None),
         "status": status, "headers": h, "body": body, "raw": body.encode() if isinstance(
             body, str) else body, "bytes": len(body), "content_type": ctype,
         "is_block_page": block, "body_class": cls, "transport_error": None, "redirects": [],
         "peercert": None, "waf": []}
    r["final_url"] = r["final_url"] or r["url"]
    r.update(kw)
    return r


def transport_err(kind="timeout"):
    return {"url": "", "final_url": "", "status": None, "headers": {}, "body": "", "raw": b"",
            "bytes": 0, "is_block_page": None, "body_class": None, "waf": [], "redirects": [],
            "transport_error": "TimeoutError: timed out", "transport_kind": kind}


HOME = (FIX / "homepage_good.html").read_text()
SHELL = (FIX / "homepage_js_shell.html").read_text()


# ======================================================================== W1 / W2 / W3 / W6
def test_w1_pass_and_partial_and_fail():
    assert ar.test_w1(resp(200, "User-agent: *\nDisallow:\n", "text/plain"))[0] == "pass"
    assert ar.test_w1(resp(200, "User-agent: *\nDisallow:\n", "application/octet-stream"))[0] \
        == "partial"
    assert ar.test_w1(resp(404, "not found"))[0] == "fail"
    v, ev = ar.test_w1(resp(200, "<!DOCTYPE html><html><body>soft 404</body></html>", "text/html"))
    assert v == "fail" and "HTML" in ev["note"]


def test_w1_blocked_and_unreachable_are_not_fail():
    assert ar.test_w1(resp(403, "Access Denied"))[0] == "blocked"
    challenge = "<html><title>Just a moment...</title>" + "x" * 300
    assert ar.test_w1(resp(200, challenge))[0] == "blocked"
    assert ar.test_w1(transport_err())[0] == "na"


def test_w2_names_ai_tokens_case_insensitively():
    v, ev = ar.test_w2(resp(200, "User-agent: gptbot\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
                            "text/plain"))
    assert v == "pass" and ev["tokens_named"] == ["GPTBot"]
    assert ar.test_w2(resp(200, "User-agent: *\nDisallow: /x\n", "text/plain"))[0] == "fail"


def _decide(robots, token, path="/"):
    return ar.robots_decision(ar.parse_robots_full(robots), token, path)["decision"]


def test_w3_longest_match_wins():
    robots = "User-agent: *\nDisallow: /\nAllow: /public\n"
    assert _decide(robots, "GPTBot", "/") == "disallow"
    assert _decide(robots, "GPTBot", "/public/page") == "allow"


def test_w3_allow_wins_a_tie():
    """RFC 9309 §2.2.2: equal-length Allow and Disallow -> the least restrictive (Allow)."""
    assert _decide("User-agent: *\nDisallow: /a\nAllow: /a\n", "GPTBot", "/a") == "allow"


def test_w3_own_group_beats_star_and_star_is_the_fallback():
    robots = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    assert _decide(robots, "GPTBot") == "disallow"
    assert _decide(robots, "ClaudeBot") == "allow"
    d = ar.robots_decision(ar.parse_robots_full(robots), "ClaudeBot", "/")
    assert d["group"] == "*"


def test_w3_same_token_groups_are_combined():
    robots = "User-agent: GPTBot\nAllow: /x\n\nUser-agent: GPTBot\nDisallow: /\n"
    assert _decide(robots, "GPTBot", "/") == "disallow"
    assert _decide(robots, "GPTBot", "/x") == "allow"


def test_w3_wildcards_and_end_anchor():
    robots = "User-agent: *\nDisallow: /*.pdf$\n"
    assert _decide(robots, "GPTBot", "/doc.pdf") == "disallow"
    assert _decide(robots, "GPTBot", "/doc.pdf?x=1") == "allow"


def test_w3_empty_disallow_and_no_robots_allow_everything():
    assert _decide("User-agent: *\nDisallow:\n", "GPTBot") == "allow"
    v, ev = ar.test_w3(resp(404, "nope"))
    assert v == "reported" and ev["tokens"]["GPTBot"]["/"]["decision"] == "allow"


def test_w3_reports_the_homepage_path_too():
    v, ev = ar.test_w3(resp(200, "User-agent: *\nDisallow: /site/\n", "text/plain"),
                       "/site/home.page")
    assert ev["tokens"]["GPTBot"]["/"]["decision"] == "allow"
    assert ev["tokens"]["GPTBot"]["/site/home.page"]["decision"] == "disallow"


def test_w6_content_signal_usage_and_usage_groups():
    robots = ("User-Agent: *\nContent-Signal: search=yes, ai-train=no\nAllow: /\n"
              "Content-Usage: train-ai=n\n\nUser-agent: __usage_train\nDisallow: /\n")
    v, ev = ar.test_w6(resp(200, robots, "text/plain"))
    assert v == "reported" and ev["present"] is True
    assert ev["content_signal"][0]["pairs"] == {"search": "yes", "ai-train": "no"}
    assert ev["content_usage"][0]["value"] == "train-ai=n"
    assert ev["usage_groups"][0]["agents"] == ["__usage_train"]
    assert ar.test_w6(resp(200, "User-agent: *\nDisallow:\n", "text/plain"))[1]["present"] is False


# ======================================================================== W4 / W5
CLASSES = {"research_betanyc": "baseline_honest", "gptbot": "ai_training",
           "chatgpt_user": "ai_agentic", "mechanism_probe_chrome": "mechanism_probe"}


def test_w4_identity_and_aggregate():
    assert ar.w4_identity_verdict(resp(200, HOME))[0] == "pass"
    assert ar.w4_identity_verdict(resp(403, "Access Denied"))[0] == "blocked"
    assert ar.w4_identity_verdict(resp(500, "oops"))[0] == "fail"
    assert ar.w4_aggregate({"gptbot": "pass", "chatgpt_user": "pass"}, CLASSES)[0] == "pass"
    assert ar.w4_aggregate({"gptbot": "pass", "chatgpt_user": "blocked"}, CLASSES)[0] == "partial"
    assert ar.w4_aggregate({"gptbot": "blocked", "chatgpt_user": "blocked",
                            "mechanism_probe_chrome": "pass"}, CLASSES)[0] == "blocked"


def test_w5_flags_stated_allow_enforced_refuse():
    _, w3 = ar.test_w3(resp(200, "User-agent: *\nDisallow: /html/misc/\n", "text/plain"))
    w4 = {"gptbot": ar.w4_identity_verdict(resp(403, "Access Denied")),
          "chatgpt_user": ar.w4_identity_verdict(resp(200, HOME))}
    v, ev = ar.test_w5(w3, w4)
    assert v == "reported" and ev["divergent"] == ["gptbot"]
    assert ev["pairs"]["gptbot"]["flag"] == "stated_allow_enforced_refuse"
    assert ev["pairs"]["chatgpt_user"]["flag"] is None


# ======================================================================== M1–M4
def test_m1_absolute_sitemaps_only():
    r = resp(200, "Sitemap: https://example.gov/sitemap.xml\nSitemap: /relative.xml\n"
                  "User-agent: *\nDisallow:\n", "text/plain")
    v, ev = ar.test_m1(r)
    assert v == "pass" and ev["absolute"] == ["https://example.gov/sitemap.xml"]
    assert ar.test_m1(resp(200, "User-agent: *\nDisallow:\n", "text/plain"))[0] == "fail"


URLSET = ("<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
          "<url><loc>https://example.gov/a</loc><lastmod>2026-10-01</lastmod></url>"
          "<url><loc>https://example.gov/b</loc><lastmod>2025-01-15T10:00:00Z</lastmod></url>"
          "<url><loc>https://example.gov/c</loc></url></urlset>")
INDEX = ("<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
         "<sitemap><loc>https://example.gov/s1.xml</loc></sitemap>"
         "<sitemap><loc>https://example.gov/s2.xml</loc></sitemap></sitemapindex>")


def test_m2_urlset_index_gzip_and_html():
    v, ev = ar.test_m2(resp(200, URLSET, "application/xml"), "robots")
    assert v == "pass" and ev["kind"] == "urlset" and ev["url_count"] == 3
    v, ev = ar.test_m2(resp(200, INDEX, "application/xml"), "default")
    assert v == "pass" and ev["child_count"] == 2
    gz = resp(200, "", "application/gzip")
    gz["raw"] = gzip.compress(URLSET.encode())
    v, ev = ar.test_m2(gz, "robots")
    assert v == "pass" and ev["gzip"] is True
    assert ar.test_m2(resp(200, "<html><body>home</body></html>"), "default")[0] == "fail"
    assert ar.test_m2(resp(404, "nope"), "default")[0] == "fail"
    assert ar.test_m2(resp(403, "Access Denied"), "default")[0] == "blocked"


def test_m3_freshness_and_one_date_flag():
    v, ev = ar.test_m3(resp(200, URLSET, "application/xml"), TODAY)
    assert v == "reported" and ev["with_lastmod"] == 2 and ev["newest"] == "2026-10-01"
    assert ev["newest_age_days"] == 36
    same = "<urlset xmlns='x'>" + "<url><loc>u</loc><lastmod>2026-11-06</lastmod></url>" * 12 + \
        "</urlset>"
    assert ar.test_m3(resp(200, same, "application/xml"), TODAY)[1]["one_date_flag"] is True


def test_m4_llms_shape():
    good = "# Example City\n\n> The official site of Example City.\n\n## Services\n- [x](y)\n"
    assert ar.test_m4(resp(200, good, "text/plain"))[0] == "pass"
    assert ar.test_m4(resp(200, "# Example City\n\nSome text\n", "text/plain"))[0] == "partial"
    assert ar.test_m4(resp(200, "<!doctype html><html>x</html>", "text/html"))[0] == "fail"
    assert ar.test_m4(resp(404, ""))[0] == "fail"


# ======================================================================== R / I / D / F / P
def test_r1_pass_on_real_text_and_fail_on_js_shell():
    v, ev = ar.test_r1(resp(200, HOME))
    assert v == "pass" and ev["words"] >= 150 and ev["scope"] == "main"
    v, ev = ar.test_r1(resp(200, SHELL))
    assert v == "fail" and ev["shell_phrases"]


def test_r1_short_page_with_structure_is_partial():
    short = "<html><body><main><h1>Town of X</h1><a href='/a'>Agendas</a></main></body></html>"
    assert ar.test_r1(resp(200, short))[0] == "partial"


def test_content_tests_are_blocked_when_research_homepage_refused():
    for fn in (ar.test_r1, ar.test_i1, ar.test_i2, ar.test_i3):
        assert fn(resp(403, "Access Denied"))[0] == "blocked"
    assert ar.test_r1(transport_err())[0] == "na"


def test_r3_structure_report():
    v, ev = ar.test_r3(resp(200, HOME))
    assert v == "reported" and ev["lang"] == "en" and ev["one_h1"] and ev["has_main_or_landmark"]


def test_i1_description_rules():
    assert ar.test_i1(resp(200, HOME))[0] == "pass"
    wp = "<html><head><meta name='description' content='Just another WordPress site'></head></html>"
    assert ar.test_i1(resp(200, wp))[0] == "fail"
    short = "<html><head><meta name='description' content='Town of X'></head></html>"
    assert ar.test_i1(resp(200, short))[0] == "partial"
    assert ar.test_i1(resp(200, "<html><body>x</body></html>"))[0] == "fail"


def test_i2_open_graph_counts():
    assert ar.test_i2(resp(200, HOME))[0] == "pass"
    one = "<html><head><meta property='og:title' content='X'></head></html>"
    assert ar.test_i2(resp(200, one))[0] == "partial"
    assert ar.test_i2(resp(200, "<html></html>"))[0] == "fail"


def test_i3_government_type_vs_generic():
    v, ev = ar.test_i3(resp(200, HOME))
    assert v == "pass" and any(t["type"] == "GovernmentOrganization" for t in ev["types"])
    site = ("<html><script type='application/ld+json'>{\"@context\":\"https://schema.org\","
            "\"@type\":\"WebSite\",\"name\":\"X\"}</script></html>")
    assert ar.test_i3(resp(200, site))[0] == "partial"
    org = ("<html><script type='application/ld+json'>{\"@graph\":[{\"@type\":\"Organization\","
           "\"name\":\"Town of Example\"}]}</script></html>")
    assert ar.test_i3(resp(200, org))[0] == "pass"
    micro = "<html><body><div itemscope itemtype='https://schema.org/GovernmentOffice'>x</div></body></html>"
    assert ar.test_i3(resp(200, micro))[0] == "pass"
    bad = "<html><script type='application/ld+json'>{not json</script></html>"
    v, ev = ar.test_i3(resp(200, bad))
    assert v == "fail" and ev["invalid_jsonld_blocks"] == 1


def test_i4_names_compared_to_registry():
    v, ev = ar.test_i4(resp(200, HOME), "City of Example")
    assert v == "reported" and ev["names_agree"] is True


def test_i5_report():
    https = resp(200, HOME, peercert={"issuer": ((("organizationName", "Example CA"),),),
                                      "notAfter": "Jan  1 00:00:00 2027 GMT"})
    http = resp(301, "", headers={"Location": "https://example.gov/"})
    v, ev = ar.test_i5("www.example.gov", https, http, dotgov_hosts={"example.gov"})
    assert v == "reported" and ev["dot_gov"] and ev["http_redirects_to_https"]
    assert ev["cert_issuer"] == "Example CA" and ev["cisa_dotgov_list"] is True
    assert ar.test_i5("example.org", https, resp(200, HOME))[1]["http_redirects_to_https"] is False


def test_d1_portal_host_suffix_prefix_and_text():
    portals = ar.load_portals(pathlib.Path(__file__).resolve().parent.parent / "data" /
                              "ai-readability" / "portals.json")
    v, ev = ar.test_d1(resp(200, HOME), portals)
    whys = {m["why"] for m in ev["matches"]}
    assert v == "pass" and {"portal host", "link text"} <= whys
    hub = "<html><body><a href='https://x-example.hub.arcgis.com/'>Maps</a></body></html>"
    assert ar.test_d1(resp(200, hub), portals)[0] == "pass"
    assert ar.test_d1(resp(200, "<html><body><a href='/about'>About</a></body></html>"),
                      portals)[0] == "fail"


def test_f1_copyright_years():
    assert ar.test_f1(resp(200, HOME), TODAY)[0] == "pass"
    old = "<html><body><footer>© 2019 Town of Example</footer></body></html>"
    assert ar.test_f1(resp(200, old), TODAY)[0] == "fail"
    rng = "<html><body>Copyright 2010-2026 Example</body></html>"
    assert ar.test_f1(resp(200, rng), TODAY)[1]["newest"] == 2026
    assert ar.test_f1(resp(200, "<html><body>no year</body></html>"), TODAY)[0] == "na"


def test_f2_headers_reported():
    v, ev = ar.test_f2(resp(200, HOME, headers={"Last-Modified": "x", "Age": "5"}))
    assert v == "reported" and ev["age"] == "5"


def test_f3_and_link_pick():
    links = ar.f3_pick_links(resp(200, HOME, url="https://www.example.gov/"))
    assert links and all(u.startswith("https://www.example.gov/") for u in links)
    assert len(links) <= 10
    ok = [{"url": "u{}".format(i), "status": 200} for i in range(9)] + [{"url": "x", "status": 404}]
    assert ar.test_f3(ok)[0] == "pass"
    assert ar.test_f3(ok + [{"url": "y", "status": 500}])[0] == "fail"


def test_f4_honest_404_vs_soft_404():
    assert ar.test_f4(resp(404, "not found"))[0] == "pass"
    assert ar.test_f4(resp(410, ""))[0] == "pass"
    v, ev = ar.test_f4(resp(200, HOME, redirects=[{"status": 302, "location": "/"}]))
    assert v == "fail" and "soft 404" in ev["note"]
    assert ar.test_f4(resp(403, "Access Denied"))[0] == "blocked"


def test_p1_to_p4_observations():
    r = resp(200, HOME, url="https://www.example.gov/", headers={"X-Powered-By": "PHP"})
    p1 = ar.test_p1(r)[1]
    assert "WordPress" in p1["platforms"]
    assert set(ar.test_p2(r)[1]["vendors"]) >= {"Legistar", "eCode360"}
    p3 = ar.test_p3(r)[1]
    assert p3["translation_widget"] and p3["hreflang_alternates"] == 1 and p3["language_links"] >= 1
    p4 = ar.test_p4(r)[1]
    assert p4["third_party_script_hosts"] == 2


# ======================================================================== scoring
def test_abhi_and_betanyc_views_keep_blocked_separate():
    verdicts = {"W1": "pass", "W2": "fail", "W4": "partial", "M1": "pass", "M2": "pass",
                "M4": "fail", "R1": "blocked", "I1": "blocked", "I2": "blocked", "I3": "blocked",
                "D1": "blocked", "F1+F3": "blocked"}
    assert ar.abhi_score(verdicts) == 3.5
    bv = ar.betanyc_view(verdicts)
    assert bv["score"] == 3.5 and bv["measured_rows"] == 6 and bv["blocked_rows"] == 6


def test_core_tier_fresh_row_rests_on_f1():
    rows = [{"test": "F1", "identity": "research_betanyc", "verdict": "pass"}]
    assert ar.host_row_verdicts(rows)["F1+F3"] == "pass"
    rows.append({"test": "F3", "identity": "research_betanyc", "verdict": "fail"})
    assert ar.host_row_verdicts(rows)["F1+F3"] == "fail"


# ======================================================================== runner (fake network)
H1, H2, H3 = "www.example.gov", "slow.example.gov", "edge.example.gov"


def fake_transport(table, calls):
    """table: {(url, ua_key|None): resp or callable}. ua_key None = any identity."""
    ua_to_key = {i["ua"]: i["key"] for i in p.IDENTITIES + [p.BINGBOT]}

    def t(url, ua, method="GET", accept="*/*", follow="any", max_bytes=0):
        key = ua_to_key.get(ua)
        calls.append((url, key, method, accept))
        hit = table.get((url, key), table.get((url, None)))
        if hit is None:
            return resp(404, "not found", url=url)
        r = hit() if callable(hit) else dict(hit)
        r["url"] = r["final_url"] = url
        return r
    return t


def good_host_table(host):
    base = "https://" + host
    return {
        (base + "/robots.txt", None): resp(200, "User-agent: *\nDisallow: /admin\n"
                                                "Sitemap: {}/sitemap.xml\n".format(base),
                                           "text/plain"),
        (base + "/llms.txt", None): resp(200, "# Example\n\n> Official site.\n", "text/plain"),
        (base + "/sitemap.xml", None): resp(200, URLSET, "application/xml"),
        (base + "/", None): resp(200, HOME),
        ("http://{}/".format(host), None): resp(301, "", headers={"Location": base + "/"}),
    }


def run(tmp_path, targets, table, extra=(), monitor=None, transport=None):
    tf = tmp_path / "targets.json"
    tf.write_text(json.dumps(targets))
    run_dir = tmp_path / "run"
    calls = []
    args = p.build_parser().parse_args(["--tier", "core", "--targets-file", str(tf),
                                        "--run-dir", str(run_dir), "--vantage", "test",
                                        "--machine", "test"] + list(extra))
    args._argv = []
    code = p.run_readability(
        args, transport=transport or fake_transport(table, calls),
        monitor=monitor or p.NetworkMonitor(check=lambda: True, sleep=lambda s: None),
        sleep=lambda s: None,
        pacing={"pace_s": 0, "jitter_s": 0, "host_floor_s": 0, "governors": {},
                "retry_backoff_s": 0})
    return code, run_dir, calls


def test_runner_end_to_end_writes_the_run_folder(tmp_path):
    code, d, calls = run(tmp_path, [{"entity_id": "e1", "entity_name": "City of Example",
                                     "url": "https://www.example.gov/"}], good_host_table(H1))
    assert code == 0
    for name in ("run.json", "results.jsonl", "status.json", "summary.md"):
        assert (d / name).exists(), name
    status = json.loads((d / "status.json").read_text())
    assert status["state"] == "done" and status["requests"] == 11 and "resume_cmd" not in status
    rows = [json.loads(x) for x in (d / "results.jsonl").read_text().splitlines()]
    tests = {r["test"] for r in rows}
    for t in ("W1", "W2", "W3", "W4", "W5", "W6", "M1", "M2", "M3", "M4", "R1", "R3", "I1", "I2",
              "I3", "I4", "I5", "D1", "F1", "F2", "F4", "P1", "P2", "P3", "P4"):
        assert t in tests, t
    assert all(r["catalog_version"] == "0.1" and r["git_sha"] for r in rows)
    run_json = json.loads((d / "run.json").read_text())
    assert run_json["catalog_version"] == "0.1" and run_json["git_sha"]
    assert len(calls) == 11
    # content tests added no requests: the homepage was fetched once per identity, no more
    assert sum(1 for c in calls if c[0] == "https://www.example.gov/") == 6
    summary = (d / "summary.md").read_text()
    assert "Abhi-12 view" in summary and "BetaNYC view" in summary


def test_runner_null_entity_id_is_allowed(tmp_path):
    code, d, _ = run(tmp_path, [{"entity_id": None, "entity_name": None,
                                 "url": "https://www.example.gov/"}], good_host_table(H1))
    assert code == 0
    rows = [json.loads(x) for x in (d / "results.jsonl").read_text().splitlines()]
    assert rows and all(r["entity_id"] is None for r in rows)


def test_pushback_quarantines_the_host_and_the_run_continues(tmp_path):
    table = good_host_table(H1)
    table[("https://{}/robots.txt".format(H2), None)] = resp(429, "slow down")
    table[("https://{}/robots.txt".format(H3), None)] = lambda: transport_err("reset")
    targets = [{"entity_id": "e1", "entity_name": "A", "url": "https://{}/".format(H1)},
               {"entity_id": "e2", "entity_name": "B", "url": "https://{}/".format(H2)},
               {"entity_id": "e3", "entity_name": "C", "url": "https://{}/".format(H3)}]
    code, d, calls = run(tmp_path, targets, table)
    assert code == 0, "pushback must never stop the run"
    status = json.loads((d / "status.json").read_text())
    assert status["state"] == "done" and status["quarantined_hosts"] == 2
    review = [json.loads(x) for x in (d / "review.jsonl").read_text().splitlines()]
    assert {(r["host"], r["action"]) for r in review} >= {(H2, "quarantined"), (H3, "quarantined")}
    # quarantined hosts got exactly one request (429) / one retry (transport), then nothing
    assert sum(1 for c in calls if H2 in c[0]) == 1
    assert sum(1 for c in calls if H3 in c[0]) == 2
    rows = [json.loads(x) for x in (d / "results.jsonl").read_text().splitlines()]
    h2_w4 = [r for r in rows if r["host"] == H2 and r["test"] == "W4"]
    assert h2_w4 and all(r["verdict"] == "na" for r in h2_w4)
    assert any(r["host"] == H1 and r["test"] == "R1" and r["verdict"] == "pass" for r in rows)


def test_403_is_data_not_quarantine(tmp_path):
    table = good_host_table(H1)
    gpt_ua_home = ("https://{}/".format(H1), "gptbot")
    table[gpt_ua_home] = resp(403, "Access Denied")
    code, d, calls = run(tmp_path, [{"entity_id": "e1", "entity_name": "A",
                                     "url": "https://{}/".format(H1)}], table)
    assert code == 0
    assert len(calls) == 11, "every other request still sent after the 403"
    review = [json.loads(x) for x in (d / "review.jsonl").read_text().splitlines()]
    assert review[0]["action"] == "recorded" and review[0]["identity"] == "gptbot"
    rows = [json.loads(x) for x in (d / "results.jsonl").read_text().splitlines()]
    w5 = next(r for r in rows if r["test"] == "W5")
    assert w5["evidence"]["divergent"] == ["gptbot"]


def test_network_down_stops_with_broke_and_resume_cmd(tmp_path):
    targets = [{"entity_id": "e{}".format(i), "entity_name": "X",
                "url": "https://h{}.example.gov/".format(i)} for i in range(12)]
    code, d, _ = run(tmp_path, targets, {}, transport=lambda *a, **k: transport_err(),
                     monitor=p.NetworkMonitor(check=lambda: False, sleep=lambda s: None))
    assert code == 1
    status = json.loads((d / "status.json").read_text())
    assert status["state"] == "broke" and "network" in status["error"]
    assert "--run-dir" in status["resume_cmd"]


def test_crash_still_writes_broke_and_runs_on_exit_hook(tmp_path):
    marker = tmp_path / "hook-got"

    def boom(*a, **k):
        raise RuntimeError("simulated crash")
    code, d, _ = run(tmp_path, [{"entity_id": "e1", "entity_name": "A",
                                 "url": "https://{}/".format(H1)}], {}, transport=boom,
                     extra=["--on-exit", "{} -c 'import sys,shutil;shutil.copy(sys.argv[1],\"{}\")'"
                            .format(sys.executable, marker)])
    assert code == 1
    status = json.loads((d / "status.json").read_text())
    assert status["state"] == "broke" and "simulated crash" in status["error"]
    assert json.loads(marker.read_text())["state"] == "broke", "hook received status.json path"


def test_sigterm_writes_broke(tmp_path):
    def term(*a, **k):
        signal.raise_signal(signal.SIGTERM)
        return resp(200, "")
    code, d, _ = run(tmp_path, [{"entity_id": "e1", "entity_name": "A",
                                 "url": "https://{}/".format(H1)}], {}, transport=term)
    assert code == 1
    status = json.loads((d / "status.json").read_text())
    assert status["state"] == "broke" and "SIGTERM" in status["error"]


def test_resume_skips_finished_hosts_and_drops_torn_host_rows(tmp_path):
    table = good_host_table(H1)
    table.update(good_host_table(H2))
    targets = [{"entity_id": "e1", "entity_name": "A", "url": "https://{}/".format(H1)},
               {"entity_id": "e2", "entity_name": "B", "url": "https://{}/".format(H2)}]
    code, d, _ = run(tmp_path, targets, table)
    assert code == 0
    # simulate: H2 crashed after writing rows but before progress was recorded
    progress = [x for x in (d / "progress.jsonl").read_text().splitlines() if H2 not in x]
    (d / "progress.jsonl").write_text("\n".join(progress) + "\n")
    code, d, calls = run(tmp_path, targets, table)
    assert code == 0
    assert calls and all(H2 in c[0] for c in calls), "only the unfinished host is re-run"
    rows = [json.loads(x) for x in (d / "results.jsonl").read_text().splitlines()]
    w1 = [r for r in rows if r["test"] == "W1"]
    assert sorted(r["host"] for r in w1) == sorted([H1, H2]), "each host written exactly once"


def test_parallel_workers_split_hosts(tmp_path):
    table = {}
    targets = []
    for i in range(6):
        h = "w{}.example.gov".format(i)
        table.update(good_host_table(h))
        targets.append({"entity_id": "e{}".format(i), "entity_name": "X", "url": "https://" + h + "/"})
    code, d, calls = run(tmp_path, targets, table, extra=["--max-workers", "3"])
    assert code == 0 and len(calls) == 66
    status = json.loads((d / "status.json").read_text())
    assert status["hosts_done"] == 6


def test_dry_run_uses_no_network(tmp_path, capsys):
    tf = tmp_path / "t.json"
    tf.write_text(json.dumps([{"entity_id": None, "entity_name": None,
                               "url": "https://www.example.gov/"}]))

    def no_net(*a, **k):
        raise AssertionError("dry run touched the network")
    args = p.build_parser().parse_args(["--tier", "core", "--targets-file", str(tf), "--dry-run"])
    assert p.run_readability(args, transport=no_net) == 0
    out = capsys.readouterr().out
    assert "requests≈11–11" in out and "no network used" in out


def test_core_identities_and_full_has_bingbot_with_documented_string():
    assert [i["key"] for i in p.tier_identities("core")] == list(p.CORE_IDENTITY_KEYS)
    full = p.tier_identities("full")
    assert full[0]["key"] == "research_betanyc" and len(full) == len(p.IDENTITIES) + 1
    b = next(i for i in full if i["key"] == "bingbot")
    assert b["ua"] == "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"


def test_http_request_never_follows_offhost_when_told_not_to(monkeypatch):
    """Exercise the real transport against a fake http.client — still no network."""
    class FakeResp:
        def __init__(self, status, headers, body=b""):
            self.status, self._h, self._b = status, headers, body

        def getheaders(self):
            return list(self._h.items())

        def read(self, n):
            return self._b[:n]

    seq = {"/robots.txt": FakeResp(301, {"Location": "https://other.example.org/robots.txt"})}

    class FakeConn:
        def __init__(self, netloc, timeout=None, context=None):
            self.netloc, self.sock = netloc, None

        def request(self, method, path, headers=None):
            self.path = path

        def getresponse(self):
            return seq[self.path]

        def close(self):
            pass
    monkeypatch.setattr(p.http.client, "HTTPSConnection", FakeConn)
    r = p.http_request("https://www.example.gov/robots.txt", None, follow="same_host")
    assert r["status"] == 301 and r["offhost_redirect"] is True and r["redirects"] == []


def test_full_tier_adds_m5_r2_f3_and_bingbot(tmp_path):
    table = good_host_table(H1)
    table[("https://{}/services/".format(H1), None)] = resp(405, "")
    tf = tmp_path / "targets.json"
    tf.write_text(json.dumps([{"entity_id": "e1", "entity_name": "City of Example",
                               "url": "https://{}/".format(H1)}]))
    calls = []
    args = p.build_parser().parse_args(["--tier", "full", "--targets-file", str(tf),
                                        "--run-dir", str(tmp_path / "run")])
    args._argv = []
    code = p.run_readability(args, transport=fake_transport(table, calls),
                             monitor=p.NetworkMonitor(check=lambda: True, sleep=lambda s: None),
                             sleep=lambda s: None,
                             pacing={"pace_s": 0, "jitter_s": 0, "host_floor_s": 0,
                                     "governors": {}, "retry_backoff_s": 0})
    assert code == 0
    rows = [json.loads(x) for x in (tmp_path / "run" / "results.jsonl").read_text().splitlines()]
    tests = {r["test"] for r in rows}
    assert {"M5", "R2", "F3"} <= tests
    assert any(r["test"] == "W4" and r["identity"] == "bingbot" for r in rows)
    assert any(c[2] == "HEAD" for c in calls)
    assert any(c[0].endswith("/services/") and c[2] == "GET" for c in calls), "GET after 405"
    assert any(c[3] == "text/markdown" for c in calls)


def test_http_request_against_a_localhost_server():
    """The real transport, end to end, against 127.0.0.1 only (no outside host)."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body=b""):
            if self.path == "/old":
                self.send_response(301)
                self.send_header("Location", "/new")
                self.end_headers()
                return
            if self.path == "/away":
                self.send_response(302)
                self.send_header("Location", "http://other.invalid/x")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("X-UA", self.headers.get("User-Agent", ""))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            self._send(b"User-agent: *\nDisallow:\n")

        def do_HEAD(self):
            self._send()

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    try:
        r = p.http_request(base + "/old", "TestUA/1.0", follow="same_host")
        assert r["status"] == 200 and r["final_url"].endswith("/new") and len(r["redirects"]) == 1
        assert r["headers"]["x-ua"] == "TestUA/1.0" and "Disallow" in r["body"]
        r = p.http_request(base + "/away", None, follow="same_host")
        assert r["status"] == 302 and r["offhost_redirect"] is True
        r = p.http_request(base + "/old", None, follow="none")
        assert r["status"] == 301 and r["headers"]["location"] == "/new"
        r = p.http_request(base + "/x", None, method="HEAD")
        assert r["status"] == 200 and r["body"] == ""
    finally:
        srv.shutdown()


def test_redirect_url_cleans_a_location_the_way_a_browser_does():
    assert p.redirect_url("https://a.example.gov/x", " /c/?q=Some Bot\t1.0\n ") == \
        "https://a.example.gov/c/?q=Some%20Bot1.0"
    assert p.redirect_url("https://a.example.gov/x", "/plain") == "https://a.example.gov/plain"


def test_bot_manager_redirect_is_a_block_not_a_transport_failure():
    """A shield's challenge redirect (spaces in its Location, as seen live) is recorded as a
    block page and never quarantines the host. 127.0.0.1 only."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/page":
                self.send_response(302)
                self.send_header("Location", "/chk/?ssc=x&ssk=botmanager_support@radware.com"
                                             "&ua=Some Bot 1.0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>" + b"please wait " * 40 + b"</body></html>")

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    try:
        r = p.http_request(base + "/page", "TestUA/1.0", follow="same_host")
    finally:
        srv.shutdown()
    assert r["transport_error"] is None and r["status"] == 200
    assert "%20" in r["final_url"]
    assert r["is_block_page"] is True and r["body_class"] == "radware_botmanager"
    assert "radware_botmanager" in r["waf"]
    what, quarantine = p._pushback(r)
    assert quarantine is False, "a block is data, not pushback that quarantines the host"
