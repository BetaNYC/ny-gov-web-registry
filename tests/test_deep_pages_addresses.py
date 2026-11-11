"""Offline tests for deep pages from the address inventory (issue #45): every section and every
address on a path that the host run does not read gets a page scan, optionally with every
crawler name a full-tier host run sends. No network: the runner gets a fake transport and a fake
network lookup. Hosts are *.example.gov placeholders, never real sites."""

import csv
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ai_readability as ar  # noqa: E402
import ai_readability_export as ex  # noqa: E402
import ai_readability_websites as aw  # noqa: E402
import probe_bot_access as p  # noqa: E402
from test_ai_readability import HOME, fake_transport, resp  # noqa: E402

SHARED = "www.shared.example.gov"
COLS = ["address", "owning_body_id", "owning_body", "kind", "level_or_entity_type", "source",
        "date_found", "notes", "platform"]
ADDR = [
    # address, ids, names, kind
    ("https://www.shared.example.gov/", "", "Shared Host", "main site"),
    ("https://shared.example.gov/listed-first", "x", "Host Job", "section"),            # host job
    ("https://shared.example.gov/site/a/index.page", "a", "Agency A", "section"),
    ("https://www.shared.example.gov/site/a/index.page/", "a", "Agency A", "section"),  # dup
    ("https://www.shared.example.gov/site/bc/", "b; c", "Board B; Board C", "section"),
    ("https://www.shared.example.gov/site/blank", "", "", "section"),                 # no owner
    ("https://www.shared.example.gov/site/gone/", "g", "Gone Body", "section"),
    ("https://www.shared.example.gov/site/old.page", "m", "Moved Body", "section"),
    ("https://www.shared.example.gov/site/cur.page", "n", "Now Body", "section"),
    ("https://www.shared.example.gov/arcgis/rest/services", "a", "Agency A", "API"),
    ("https://sectiononly.example.gov/first", "s", "Section Only", "section"),          # host job
    ("https://sectiononly.example.gov/second", "s", "Section Only", "section"),
    ("https://portal.example.gov/data/", "d", "Data Body", "data portal"),             # host job
    ("https://rootsection.example.gov/", "r", "Root Section", "section"),               # homepage
]
CORRECTIONS = [
    ["https://www.shared.example.gov/site/gone/", "gone", "", "2026-11-10", "404"],
    ["https://www.shared.example.gov/site/old.page", "moved",
     "https://www.shared.example.gov/site/new.page", "2026-11-10", "404; new page found"],
    ["https://www.shared.example.gov/site/was.page", "moved",
     "https://www.shared.example.gov/site/cur.page", "2026-11-10", "404; listed at new"],
]
PAGES = ["https://shared.example.gov/site/a/index.page",
         "https://www.shared.example.gov/site/bc/",
         "https://www.shared.example.gov/site/blank",
         "https://www.shared.example.gov/site/new.page",
         "https://www.shared.example.gov/site/cur.page",
         "https://www.shared.example.gov/arcgis/rest/services",
         "https://sectiononly.example.gov/second"]
TODAY = datetime.date(2026, 11, 10)


def write_inputs(tmp_path):
    a = tmp_path / "addresses.csv"
    with open(a, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        for url, ids, names, kind in ADDR:
            w.writerow([url, ids, names, kind, "nyc", "websites.json", "2026-11-06", "", ""])
    c = tmp_path / "page-corrections.csv"
    with open(c, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["page_url", "issue", "moved_to", "checked", "evidence"])
        w.writerows(CORRECTIONS)
    return a, c


# ======================================================================== the page list
def test_address_pages_cover_sections_and_paths_the_host_run_does_not_read(tmp_path):
    a, c = write_inputs(tmp_path)
    t = p.load_address_page_targets(a, c)
    assert sorted(x["url"] for x in t) == sorted(PAGES)
    by = {x["url"]: x for x in t}
    a1 = by["https://shared.example.gov/site/a/index.page"]
    assert a1["host"] == SHARED and a1["page_host"] == "shared.example.gov", \
        "paced on the host run's job host (www. ignored); robots.txt from the page's own host"
    assert a1["entity_id"] == "a" and a1["entity_name"] == "Agency A" and a1["kind"] == "section"
    bc = by["https://www.shared.example.gov/site/bc/"]
    assert bc["entity_id"] is None and bc["entity_ids"] == ["b", "c"]
    assert bc["entity_names"] == ["Board B", "Board C"] and bc["entity_name"] == "Board B; Board C"
    blank = by["https://www.shared.example.gov/site/blank"]
    assert blank["entity_name"] is None and blank["entity_names"] == [], "never a guessed name"
    new = by["https://www.shared.example.gov/site/new.page"]
    assert new["moved_from"] == "https://www.shared.example.gov/site/old.page"
    assert new["entity_name"] == "Moved Body"
    cur = by["https://www.shared.example.gov/site/cur.page"]
    assert cur["moved_from"] == "https://www.shared.example.gov/site/was.page", \
        "an address already at moved_to keeps its listed address"
    assert by["https://www.shared.example.gov/arcgis/rest/services"]["kind"] == "API"
    assert by["https://sectiononly.example.gov/second"]["host"] == "sectiononly.example.gov"


def test_every_section_in_the_real_inventory_is_scanned_by_a_page_or_the_host_run():
    """Noel's rule: sections get the same scan as sites. Each section (and each address on a
    path) is either a page target or the address its host's job reads, unless a hand check
    found it gone. A homepage is the host run's."""
    targets = p.load_addresses(p.ADDRESSES)
    host_urls = {aw._page_key(j["url"]) for j in p.plan_targets(targets)}
    page_keys = set()
    for x in p.load_address_page_targets():
        page_keys.add(aw._page_key(x["url"]))
        if x.get("moved_from"):
            page_keys.add(aw._page_key(x["moved_from"]))
    gone = {k for k, r in aw.read_page_corrections().items() if r["issue"] == "gone"}
    missed = []
    for t in targets:
        u = p.urllib.parse.urlparse(t["url"])
        on_path = (u.path or "/") != "/" or u.query
        if t["kind"] != "section" and not on_path:
            continue
        k = aw._page_key(t["url"])
        if k in gone or (not on_path and k in host_urls):
            continue
        if k not in page_keys and k not in host_urls:
            missed.append(t["url"])
    assert missed == []
    assert not page_keys & {aw._page_key(j["url"]) for j in p.plan_targets(targets)
                            if (p.urllib.parse.urlparse(j["url"]).path or "/") == "/"}


# ======================================================================== identities
def test_page_identities_default_is_tier_b_and_full_is_the_host_runs_set():
    assert [i["key"] for i in p.page_identities()] == list(p.TIER_B_IDENTITY_KEYS)
    assert [i["key"] for i in p.page_identities(which="full")] == \
        [i["key"] for i in p.tier_identities("full")]
    assert len(p.page_identities(which="full")) == len(p.tier_identities("full"))
    try:
        p.page_identities(which="most")
        raise AssertionError("an unknown set must be refused")
    except ValueError:
        pass
    args = p.build_parser().parse_args(["--deep-pages"])
    assert args.page_identities == "tierb" and args.pages_from is None


# ======================================================================== the runner
def page_table():
    t = {}
    for h in (SHARED, "shared.example.gov", "sectiononly.example.gov"):
        t[("https://{}/robots.txt".format(h), None)] = resp(
            200, "User-agent: *\nDisallow: /site/bc/\n", "text/plain")
    for u in PAGES:
        t[(u, None)] = resp(200, HOME)
    return t


def fake_net():
    return {"public_ip": "192.0.2.1", "asn": "AS64500", "provider": "TEST-NET",
            "by_family": {"ipv4": {"public_ip": "192.0.2.1", "asn": "AS64500"},
                          "ipv6": {"error": "no route"}}}


def run_pages(tmp_path, extra=(), transport=None, calls=None, today=TODAY, run_dir=True):
    a, c = write_inputs(tmp_path)
    p_fix = aw.PAGE_CORRECTIONS
    aw.PAGE_CORRECTIONS = c          # the runner reads the default corrections path
    calls = [] if calls is None else calls
    argv = ["--pages-from", "addresses", "--targets-file", str(a), "--vantage", "office",
            "--machine", "test"] + (["--run-dir", str(tmp_path / "run")] if run_dir else
                                    ["--out-root", str(tmp_path / "runs")]) + list(extra)
    args = p.build_parser().parse_args(argv)
    args._argv = []
    try:
        code = p.run_readability(
            args, transport=transport or fake_transport(page_table(), calls), today=today,
            monitor=p.NetworkMonitor(check=lambda: True, sleep=lambda s: None),
            sleep=lambda s: None, netlookup=fake_net,
            pacing={"pace_s": 0, "jitter_s": 0, "host_floor_s": 0, "governors": {},
                    "retry_backoff_s": 0})
    finally:
        aw.PAGE_CORRECTIONS = p_fix
    return code, tmp_path / "run", calls


def rows_of(d, name="results.jsonl"):
    path = d / name
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_address_pages_full_identities_end_to_end(tmp_path):
    code, d, calls = run_pages(tmp_path, extra=["--page-identities", "full"])
    assert code == 0
    full = [i["key"] for i in p.tier_identities("full")]
    robots = [c[0] for c in calls if c[0].endswith("/robots.txt")]
    assert len(robots) == len(set(robots)) == 3, "robots.txt once per page host"
    assert len(calls) == 3 + len(PAGES) * len(full)
    for u in PAGES:
        assert sorted(c[1] for c in calls if c[0] == u) == sorted(full)
    assert not any(c[0].endswith(("/llms.txt", "/sitemap.xml")) or "does-not-exist" in c[0]
                   or c[0].startswith("http://") for c in calls), "no host-level files per page"
    rows = rows_of(d)
    assert {r["test"] for r in rows} == set(p.PAGE_TESTS) and "D2" in p.PAGE_TESTS
    assert all(r["scope"] == "page" for r in rows)
    w4 = {r["identity"] for r in rows if r["test"] == "W4"
          and r["page_url"] == "https://www.shared.example.gov/site/blank"}
    assert w4 == set(full) | {ar.W4_AGGREGATE_IDENTITY}
    new = next(r for r in rows if r["page_url"].endswith("/site/new.page"))
    assert new["moved_from"].endswith("/site/old.page") and new["kind"] == "section"
    w3 = next(r for r in rows if r["page_url"].endswith("/site/bc/") and r["test"] == "W3")
    assert w3["evidence"]["tokens"]["GPTBot"]["/site/bc/"]["decision"] == "disallow"
    run = json.loads((d / "run.json").read_text())
    assert run["mode"] == "deep_pages" and run["pages_from"] == "addresses"
    assert run["page_identities"] == "full" and len(run["identities"]) == len(full)
    assert run["network"]["start"]["asn"] == "AS64500" and run["network"]["end"]["asn"] == "AS64500"
    assert run["network"]["start"]["by_family"]["ipv6"]["error"] == "no route"
    assert run["network"]["place"] == "office"
    status = json.loads((d / "status.json").read_text())
    assert status["state"] == "done" and status["pages_done"] == len(PAGES)


def test_tierb_default_sends_three_identities(tmp_path):
    code, _, calls = run_pages(tmp_path)
    assert code == 0 and len(calls) == 3 + len(PAGES) * 3


def test_full_identities_go_through_the_pre_run_guard(tmp_path):
    def no_net(*a, **k):
        raise AssertionError("a refused run touched the network")
    code, d, _ = run_pages(tmp_path, extra=["--page-identities", "full"], transport=no_net,
                           today=datetime.date(2027, 6, 1))
    assert code == 2 and not (d / "results.jsonl").exists()


def test_page_runs_get_their_own_default_folder(tmp_path):
    code, _, _ = run_pages(tmp_path, run_dir=False)
    assert code == 0
    folders = [x.name for x in (tmp_path / "runs").iterdir()]
    assert len(folders) == 1 and folders[0].endswith("-office-pages"), \
        "never the same folder as the day's host run"


def test_dry_run_prints_pages_hosts_identities_requests_and_hours(tmp_path, capsys):
    def no_net(*a, **k):
        raise AssertionError("dry run touched the network")
    code, _, _ = run_pages(tmp_path, extra=["--page-identities", "full", "--dry-run"],
                           transport=no_net)
    assert code == 0
    out = capsys.readouterr().out
    n = len(p.tier_identities("full"))
    assert "pages={}".format(len(PAGES)) in out and "hosts=2" in out
    assert "identities={}".format(n) in out
    assert "requests≈{}".format(3 + len(PAGES) * n) in out
    assert "estimated wall clock" in out and "busiest host: {}".format(SHARED) in out
    assert "pages by kind: API 1, section 6" in out and "moved (probed at moved_to): 2" in out
    assert "no network used" in out


def test_page_run_hours_takes_the_busiest_host_floor_or_the_global_pace():
    est = {"requests": 3600, "min_hours": 2.0}
    assert p.page_run_hours(est, 1, 6.0) == 7.0
    assert p.page_run_hours(est, 8, 6.0) == 2.0


def test_extra_list_is_refused_with_addresses(tmp_path, capsys):
    a, _ = write_inputs(tmp_path)
    code = p.main(["--pages-from", "addresses", "--targets-file", str(a),
                   "--deep-pages-extra", str(tmp_path / "x.csv"), "--dry-run"])
    assert code == 2 and "--deep-pages-extra" in capsys.readouterr().err


# ======================================================================== the export
def test_export_writes_one_row_per_page(tmp_path):
    code, d, _ = run_pages(tmp_path, extra=["--page-identities", "full"])
    assert code == 0
    assert ex.main(["--run-dir", str(d)]) == 0
    rows = list(csv.DictReader(open(d / "pages.csv", newline="", encoding="utf-8")))
    assert len(rows) == len(PAGES) and not (d / "sites.csv").exists()
    by = {r["page_url"]: r for r in rows}
    assert set(by) == set(PAGES)
    assert all(r["ai_scope"] == "page" for r in rows)
    bc = by["https://www.shared.example.gov/site/bc/"]
    assert bc["initial_url"] == bc["page_url"] and bc["initial_domain"] == SHARED
    assert bc["ny_owning_body"] == "Board B; Board C" and bc["ny_kind"] == "section"
    assert bc["ny_entity_ids"] == "b,c" and bc["agency"] == "", "two owners: agency blank"
    assert "GPTBot=disallow" in bc["ai_w3_decisions"].split(","), "W3 at the page's own path"
    a1 = by["https://shared.example.gov/site/a/index.page"]
    assert a1["agency"] == "Agency A" and a1["status_code"] == "200"
    assert by["https://www.shared.example.gov/site/new.page"]["ny_moved_from"].endswith("/old.page")
    assert a1["ai_d2_verdict"] == "reported"
    assert a1["ai_w1_verdict"] == "" and a1["404_test"] == "", "host-level tests stay blank"
    assert a1["ai_w4_gptbot_status"] == "200", "a W4 column per identity, as a host row"
    assert a1["ai_run_asn_start"] == "AS64500" and a1["ai_run_ipv6"] == "FALSE"
    cols = list(rows[0])
    assert cols[-3:] == ["ai_scope", "page_url", "ny_moved_from"]
    assert cols[:-3] == ex.columns(identities=ex.run_identities(json.loads(
        (d / "run.json").read_text())))
    fids = {r["field id"] for r in ex.dictionary_rows()}
    assert {"ai_scope", "page_url", "ny_moved_from"} <= fids
