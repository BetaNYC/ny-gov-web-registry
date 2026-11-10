"""Offline tests for deep-page mode (issue #35): the page list, the page plan, its runner, and
the headed (Chrome) person view. No network and no browser: the runner gets a fake transport.
Hosts are *.example.gov placeholders, never real sites."""

import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ai_readability as ar  # noqa: E402
import ai_readability_export as ex  # noqa: E402
import ai_readability_websites as aw  # noqa: E402
import headed_check  # noqa: E402
import probe_bot_access as p  # noqa: E402
from test_ai_readability import HOME, fake_transport, resp  # noqa: E402

SHARED, SHARED1, SOLO = "www.shared.example.gov", "www1.shared.example.gov", "solo.example.gov"


def website_row(host, ids, pages, issue=None):
    """A websites.json row with only the fields deep_page_targets reads (layout kept)."""
    return {"initial_url": "https://{}/".format(host), "ny_initial_domain": host,
            "ny_entity_ids": ids or None, "ny_names": None, "ny_page_urls": pages or None,
            "ny_host_issue": issue}


ROWS = [
    website_row(SHARED, ["a", "b", "c"], ["https://www.shared.example.gov/site/a/index.page",
                                         "https://shared.example.gov/html/b/home.shtml",
                                         "https://www.shared.example.gov/assets/ab"]),
    website_row(SHARED1, ["d", "e"], ["https://www1.shared.example.gov/fund"]),
    website_row(SOLO, ["f"], ["https://solo.example.gov/one-body-only"]),     # one body: skipped
    website_row("lookalike.example.gov", ["g", "h"], ["https://lookalike.example.gov/x"],
                issue="lookalike"),                                           # host issue: skipped
]
OWNERS = {
    "https://www.shared.example.gov/site/a/index.page": {"names": ["Agency A"], "entity_ids": ["a"],
                                                         "sources": ["registry"]},
    "https://shared.example.gov/html/b/home.shtml": {"names": ["Board B"], "entity_ids": [],
                                                     "sources": ["abo"]},
    "https://www.shared.example.gov/assets/ab": {"names": ["Agency A", "Advisory Board"],
                                                 "entity_ids": ["a", "c"],
                                                 "sources": ["registry"]},
}


def write_extra(tmp_path, rows):
    path = tmp_path / "extra.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "name", "source", "checked"])
        w.writerows(rows)
    return path


EXTRA = [["https://www.shared.example.gov/site/cb1/index.page", "Community Board 1 (XX01)",
          "board page", "2026-11-04"],
         ["https://shared.example.gov/site/a/index.page/", "dup of a registry page", "x", "2026-11-04"],
         ["https://www.shared.example.gov/", "a homepage, already scored", "x", "2026-11-04"]]


# ======================================================================== the page list
def test_deep_page_targets_picks_shared_hosts_pairs_owners_and_never_guesses(tmp_path):
    t = aw.deep_page_targets(ROWS, OWNERS, write_extra(tmp_path, EXTRA))
    urls = [x["url"] for x in t]
    assert "https://solo.example.gov/one-body-only" not in urls, "one-body host is not deep"
    assert not any("lookalike" in u for u in urls), "ny_host_issue rows are skipped"
    assert "https://www.shared.example.gov/" not in urls, "the homepage is the host run's"
    assert sum("site/a/index.page" in u for u in urls) == 1, "www. and trailing / dedupe"
    assert len(t) == 5
    by = {x["url"]: x for x in t}
    a = by["https://www.shared.example.gov/site/a/index.page"]
    assert a["entity_id"] == "a" and a["entity_name"] == "Agency A" and a["host"] == SHARED
    b = by["https://shared.example.gov/html/b/home.shtml"]
    assert b["host"] == SHARED and b["page_host"] == "shared.example.gov", \
        "paced on the row's host, robots from the page's own host"
    assert b["entity_id"] is None and b["entity_name"] == "Board B" and b["source"] == "abo"
    ab = by["https://www.shared.example.gov/assets/ab"]
    assert ab["entity_id"] is None and ab["entity_ids"] == ["a", "c"]
    assert ab["entity_names"] == ["Agency A", "Advisory Board"]
    fund = by["https://www1.shared.example.gov/fund"]
    assert fund["entity_name"] is None and fund["entity_names"] == [], "unpaired: no guessed name"
    cb = by["https://www.shared.example.gov/site/cb1/index.page"]
    assert cb["entity_name"] == "Community Board 1 (XX01)" and cb["entity_id"] is None
    assert cb["source"].startswith("extra.csv")


def test_page_owners_pairs_each_listed_page_with_its_source_record(tmp_path):
    reg = {"entities": [
        {"id": "a", "name": "Agency A", "government_level": "nyc",
         "web_properties": [{"role": "primary", "url": "https://www.shared.example.gov/site/a"}]},
        {"id": "b", "name": "Board B", "government_level": "nyc",
         "web_properties": [{"role": "primary", "url": "https://www.shared.example.gov/site/a"},
                            {"role": "legacy", "url": "https://old.example.gov/b"}]},
        {"id": "c", "name": "Host Only", "government_level": "nyc",
         "web_properties": [{"role": "primary", "domain": "www.shared.example.gov"}]}]}
    (tmp_path / "registry.json").write_text(json.dumps(reg))
    paths = {k: tmp_path / "absent" for k in aw.PATHS}
    paths["registry"] = tmp_path / "registry.json"
    owners = aw.page_owners(paths)
    assert list(owners) == ["https://www.shared.example.gov/site/a"], "legacy and bare hosts excluded"
    o = owners["https://www.shared.example.gov/site/a"]
    assert o["names"] == ["Agency A", "Board B"] and o["entity_ids"] == ["a", "b"]


def test_the_committed_extra_list_is_well_formed():
    path = aw.DEEP_PAGES_EXTRA
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    assert rows and set(rows[0]) == {"url", "name", "source", "checked"}
    assert all(r["url"].startswith("https://") and r["name"] for r in rows)
    assert len({r["url"] for r in rows}) == len(rows)


# ======================================================================== the runner
PAGE_A = "https://www.shared.example.gov/site/a/index.page"
PAGE_B = "https://shared.example.gov/html/b/home.shtml"
PAGE_F = "https://www1.shared.example.gov/fund"


def deep_table():
    t = {}
    for h in (SHARED, "shared.example.gov", SHARED1):
        t[("https://{}/robots.txt".format(h), None)] = resp(
            200, "User-agent: *\nDisallow: /html/\n", "text/plain")
    for u in (PAGE_A, PAGE_B, PAGE_F, "https://www.shared.example.gov/assets/ab",
              "https://www.shared.example.gov/site/cb1/index.page"):
        t[(u, None)] = resp(200, HOME)
    return t


def run_deep(tmp_path, table, extra=(), monkeypatch=None, transport=None, calls=None):
    wf = tmp_path / "websites.json"
    wf.write_text(json.dumps(ROWS))
    xf = write_extra(tmp_path, EXTRA)
    if monkeypatch:
        monkeypatch.setattr(aw, "page_owners", lambda *a, **k: OWNERS)
    calls = [] if calls is None else calls
    args = p.build_parser().parse_args(["--deep-pages", "--targets-file", str(wf),
                                        "--deep-pages-extra", str(xf),
                                        "--run-dir", str(tmp_path / "run"), "--vantage", "test",
                                        "--machine", "test"] + list(extra))
    args._argv = []
    code = p.run_readability(
        args, transport=transport or fake_transport(table, calls),
        monitor=p.NetworkMonitor(check=lambda: True, sleep=lambda s: None),
        sleep=lambda s: None,
        pacing={"pace_s": 0, "jitter_s": 0, "host_floor_s": 0, "governors": {},
                "retry_backoff_s": 0})
    return code, tmp_path / "run", calls


def rows_of(d, name="results.jsonl"):
    path = d / name
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_deep_pages_end_to_end_page_rows_only_robots_once_per_page_host(tmp_path, monkeypatch):
    code, d, calls = run_deep(tmp_path, deep_table(), monkeypatch=monkeypatch)
    assert code == 0
    robots = [c[0] for c in calls if c[0].endswith("/robots.txt")]
    assert sorted(robots) == sorted("https://{}/robots.txt".format(h)
                                    for h in (SHARED, "shared.example.gov", SHARED1)), \
        "one robots.txt per page host, never per page"
    assert len(calls) == 3 + 5 * 3, "robots × 3 page hosts + 5 pages × 3 identities"
    for u in (PAGE_A, PAGE_B, PAGE_F):
        assert sorted(c[1] for c in calls if c[0] == u) == sorted(p.TIER_B_IDENTITY_KEYS)
    assert not any(c[0].endswith(("/llms.txt", "/sitemap.xml")) or "does-not-exist" in c[0]
                   or c[0].startswith("http://") for c in calls), "no host-level files per page"
    rows = rows_of(d)
    assert {r["test"] for r in rows} == set(p.PAGE_TESTS)
    assert all(r["scope"] == "page" and r["page_url"] for r in rows)
    assert len({r["page_url"] for r in rows}) == 5
    a = [r for r in rows if r["page_url"] == PAGE_A]
    assert {r["identity"] for r in a if r["test"] == "W4"} == set(p.TIER_B_IDENTITY_KEYS) | {
        ar.W4_AGGREGATE_IDENTITY}
    w3b = next(r for r in rows if r["page_url"] == PAGE_B and r["test"] == "W3")
    assert w3b["evidence"]["tokens"]["GPTBot"]["/html/b/home.shtml"]["decision"] == "disallow", \
        "W3 reads the page's own path against its host's robots.txt"
    i4 = next(r for r in rows if r["page_url"].endswith("/assets/ab") and r["test"] == "I4")
    assert set(i4["evidence"]["per_name"]) == {"Agency A", "Advisory Board"}
    status = json.loads((d / "status.json").read_text())
    assert status["state"] == "done" and status["mode"] == "deep_pages"
    assert status["pages"] == 5 and status["pages_done"] == 5
    assert "**Pages:** 5" in (d / "summary.md").read_text()


def test_deep_pages_pace_on_the_rows_host():
    assert p.clock_host({"url": PAGE_B, "pace_host": SHARED}) == SHARED
    assert p.clock_host({"url": PAGE_B}) == "shared.example.gov"


def test_worker_window_never_holds_two_jobs_on_one_host(tmp_path, monkeypatch):
    """With a window of 8, five pages on one host must still run one after another."""
    active_by_host = []
    real = p.page_plan

    def spy(job, ctx):
        active_by_host.append(job["host"])
        rows = yield from real(job, ctx)
        active_by_host.remove(job["host"])
        return rows
    monkeypatch.setattr(p, "page_plan", spy)
    seen_max = []

    def transport(url, ua, **k):
        seen_max.append(max(active_by_host.count(h) for h in set(active_by_host)))
        return fake_transport(deep_table(), [])(url, ua, **k)
    code, _, _ = run_deep(tmp_path, {}, monkeypatch=monkeypatch, transport=transport)
    assert code == 0 and seen_max and max(seen_max) == 1


def test_a_429_on_a_page_quarantines_its_host_and_the_run_continues(tmp_path, monkeypatch):
    table = deep_table()
    table[(PAGE_A, None)] = resp(429, "slow down")
    code, d, calls = run_deep(tmp_path, table, monkeypatch=monkeypatch)
    assert code == 0
    assert sum(1 for c in calls if c[0] == PAGE_A) == 1, "429 is never retried"
    review = rows_of(d, "review.jsonl")
    assert any(r["action"] == "quarantined" and r["page_url"] == PAGE_A for r in review)
    first = next(i for i, c in enumerate(calls) if c[0] == PAGE_A)
    assert not any("//www.shared." in c[0] or "//shared." in c[0] for c in calls[first + 1:]), \
        "nothing more is sent to that host"
    rows = rows_of(d)
    a_w4 = [r for r in rows if r["page_url"] == PAGE_A and r["test"] == "W4"
            and r["identity"] != ar.W4_AGGREGATE_IDENTITY]
    assert sorted(r["verdict"] for r in a_w4) == ["blocked", "na", "na"]
    assert any(r["host"] == SHARED1 and r["test"] == "R1" and r["verdict"] == "pass" for r in rows)


def test_deep_pages_resume_is_per_page(tmp_path, monkeypatch):
    code, d, _ = run_deep(tmp_path, deep_table(), monkeypatch=monkeypatch)
    assert code == 0
    progress = [x for x in (d / "progress.jsonl").read_text().splitlines() if PAGE_F not in x]
    (d / "progress.jsonl").write_text("\n".join(progress) + "\n")
    code, d, calls = run_deep(tmp_path, deep_table(), monkeypatch=monkeypatch)
    assert code == 0
    assert sorted(c[0] for c in calls) == sorted(
        ["https://{}/robots.txt".format(SHARED1)] + [PAGE_F] * 3), "only the torn page re-runs"
    w3 = [r for r in rows_of(d) if r["test"] == "W3"]
    assert len(w3) == 5 and len({r["page_url"] for r in w3}) == 5, "each page written once"


def test_deep_pages_dry_run_estimate_uses_no_network(tmp_path, monkeypatch, capsys):
    def no_net(*a, **k):
        raise AssertionError("dry run touched the network")
    code, _, _ = run_deep(tmp_path, {}, extra=["--dry-run"], monkeypatch=monkeypatch,
                          transport=no_net)
    assert code == 0
    out = capsys.readouterr().out
    assert "pages=5" in out and "requests≈18" in out and "no network used" in out
    assert "pages with no owner name: 1" in out and PAGE_F in out


def test_page_request_estimate_counts_robots_per_page_host_and_the_busiest_floor():
    jobs = p.page_jobs(aw.deep_page_targets(ROWS, OWNERS, None))
    est = p.page_request_estimate(jobs, 3, 30.0, {SHARED: 20.0})
    assert est["pages"] == 4 and est["robots"] == 3 and est["requests"] == 4 * 3 + 3
    assert est["busiest"] == SHARED and est["per_host"][SHARED] == 3 * 3 + 2
    assert est["min_hours"] == round(10 * 30.0 / 3600, 1), "floor is max(30 s, governor)"


def test_split_by_host_keeps_a_hosts_pages_together():
    jobs = p.page_jobs(aw.deep_page_targets(ROWS, OWNERS, None))
    groups = p.split_by_host(jobs, 2)
    for g in groups:
        for h in {j["host"] for j in g}:
            assert all(j in g for j in jobs if j["host"] == h)


def test_parallel_deep_pages_fetch_each_robots_once(tmp_path, monkeypatch):
    code, _, calls = run_deep(tmp_path, deep_table(), extra=["--max-workers", "2"],
                              monkeypatch=monkeypatch)
    assert code == 0
    robots = [c[0] for c in calls if c[0].endswith("/robots.txt")]
    assert len(robots) == len(set(robots)) == 3


def test_host_runs_are_unchanged_by_page_rows_in_export():
    host_row = {"host": SHARED, "test": "W1", "identity": p.RESEARCH_KEY, "verdict": "pass"}
    page_row = dict(host_row, scope="page", page_url=PAGE_A, verdict="fail")
    targets = [{"entity_id": "a", "entity_name": "A", "url": "https://{}/".format(SHARED)}]
    psl = ex.load_psl()
    with_pages = ex.build_rows(targets, [host_row, page_row], [], [], psl)
    without = ex.build_rows(targets, [host_row], [], [], psl)
    assert with_pages == without


def test_summary_counts_pages_not_hosts():
    rows = [{"host": SHARED, "page_url": u, "test": "R1", "identity": p.RESEARCH_KEY,
             "verdict": "pass"} for u in (PAGE_A, PAGE_B)]
    out = ar.summarize(rows, 0, {"run_id": "x"})
    assert "**Pages:** 2" in out and PAGE_A in out and PAGE_B in out


# ======================================================================== headed (person view)
class FakeChrome:
    """Answers like browser_transport would: one fetch per call, never a retry of its own."""

    def __init__(self, table, captured, challenge_urls=()):
        self.table, self.captured, self.challenge = table, captured, set(challenge_urls)
        self.calls = []

    def __call__(self, url, ua, method="GET", accept="*/*", follow="any", max_bytes=0):
        self.calls.append((url, ua))
        hit = self.table.get((url, None))
        r = dict(hit) if hit else resp(404, "not found")
        r["url"] = r["final_url"] = url
        if headed_check.page_kind(url) == "page":
            title = "Just a moment..." if url in self.challenge else "Agency"
            self.captured[url] = (r, title, "word " * 50)
        return r


def test_headed_deep_pages_label_rows_and_list_pages_that_need_a_person(tmp_path):
    jobs = p.page_jobs(aw.deep_page_targets(ROWS, OWNERS, None))
    table = deep_table()
    table[(PAGE_F, None)] = resp(503, "unavailable")
    captured = {}
    chrome = FakeChrome(table, captured, challenge_urls=[PAGE_A])
    run_dir = tmp_path / "headed"
    run_dir.mkdir()
    counts = headed_check.run_pages(jobs, run_dir, "test", chrome, "Mozilla/5.0 Chrome/142",
                                    captured, sleep=lambda s: None,
                                    pacing={"pace_s": 0, "jitter_s": 0, "host_floor_s": 0,
                                            "governors": {}, "retry_backoff_s": 0})
    assert sum(1 for c in chrome.calls if c[0] == PAGE_F) == 1, "a 503 is never retried"
    assert sum(1 for c in chrome.calls if c[0] == PAGE_A) == 1, "a challenge is never retried"
    rows = rows_of(run_dir)
    assert rows and all(r["identity"] != p.RESEARCH_KEY for r in rows)
    assert all(r["scope"] == "page" and r["client"] == headed_check.IDENTITY for r in rows)
    headed = {r["page_url"]: r for r in rows_of(run_dir, "headed.jsonl")}
    assert len(headed) == 4
    assert headed[PAGE_A]["challenge"] and headed[PAGE_A]["verdict"] == "blocked"
    assert headed[PAGE_A]["needs_person"] is True
    lines = (run_dir / "needs_person.txt").read_text().splitlines()
    assert [ln.split("\t")[0] for ln in lines] == [PAGE_A]
    assert counts.get("blocked", 0) >= 2   # the challenge and the 503


def test_headed_deep_pages_gone_page_is_not_found_not_blocked(tmp_path):
    jobs = p.page_jobs(aw.deep_page_targets(ROWS, OWNERS, None))
    table = deep_table()
    table[(PAGE_F, None)] = resp(404, "not found")
    captured = {}
    chrome = FakeChrome(table, captured)
    run_dir = tmp_path / "headed"
    run_dir.mkdir()
    counts = headed_check.run_pages(jobs, run_dir, "test", chrome, "Mozilla/5.0 Chrome/142",
                                    captured, sleep=lambda s: None,
                                    pacing={"pace_s": 0, "jitter_s": 0, "host_floor_s": 0,
                                            "governors": {}, "retry_backoff_s": 0})
    headed = {r["page_url"]: r for r in rows_of(run_dir, "headed.jsonl")}
    assert headed[PAGE_F]["verdict"] == headed_check.NOT_FOUND
    assert headed[PAGE_F]["needs_person"] is False
    gone = (run_dir / "pages_not_found.txt").read_text().splitlines()
    assert [ln.split("\t")[0] for ln in gone] == [PAGE_F]
    assert (run_dir / "needs_person.txt").read_text() == "", "written even when empty"
    assert counts.get(headed_check.NOT_FOUND) == 1 and "blocked" not in counts


def test_headed_needs_a_targets_file_unless_deep_pages(capsys):
    assert headed_check.main(["--run-dir", "/tmp/x", "--vantage", "t",
                              "--operator-authorized"]) == 2
    assert "--targets-file" in capsys.readouterr().err


def test_headed_page_kind():
    assert headed_check.page_kind("https://a.example.gov/robots.txt") == "robots"
    assert headed_check.page_kind(PAGE_A) == "page"
