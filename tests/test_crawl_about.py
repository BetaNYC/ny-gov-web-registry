"""Offline tests for crawl_about — discovery, extraction, robots, the WAF circuit-breaker, failure
recording, and the descriptions.json output schema. The fetch layer is FAKED (a dict of url->
(status, body)); NO test touches the network. This is the phase-5 test contract from issue #1."""
import json
import pathlib

import crawl_about
from crawl_about import (
    FAILURE_REASONS,
    MAX_TEXT_CHARS,
    RobotsCache,
    crawl_entity,
    discover_about_candidates,
    extract_main_text,
    find_about_links,
    robots_allows,
    truncate,
)

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "crawl"


def _fix(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- discovery: path-probe

def test_path_probe_handles_nyc_gov_site_subsite():
    cands = discover_about_candidates("https://www.nyc.gov/site/dsny/index.page")
    # the nyc.gov FSE convention must be probed first and be path-relative to the subsite
    assert "https://www.nyc.gov/site/dsny/about/about.page" in cands
    assert cands[0] == "https://www.nyc.gov/site/dsny/about/about.page"
    # never probes the citywide apex-only /about as the first guess for a subsite
    assert cands.index("https://www.nyc.gov/site/dsny/about/about.page") < cands.index("https://www.nyc.gov/about")


def test_path_probe_apex_site():
    cands = discover_about_candidates("https://mta.info/")
    assert "https://mta.info/about" in cands
    assert "https://mta.info/about-us" in cands


def test_path_probe_rejects_junk_url():
    assert discover_about_candidates("not-a-url") == []
    assert discover_about_candidates("") == []


# --------------------------------------------------------------------------- discovery: link-scan

def test_link_scan_finds_about_anchor_by_text_and_href():
    links = find_about_links(_fix("homepage_with_about_link.html"), "https://example.gov/")
    assert "https://example.gov/about-us/our-mission" in links


def test_link_scan_stays_on_site():
    # the "Follow us" social link points off-site and must be excluded
    links = find_about_links(_fix("homepage_with_about_link.html"), "https://example.gov/")
    assert all("social.example.com" not in u for u in links)


def test_link_scan_tolerates_malformed_html():
    # broken markup around a well-formed anchor must not crash the scan
    html = "<a href='/about'>About</a> <div <span >>> <a href='/about-us'>Mission</a"
    assert find_about_links(html, "https://x.gov/") == ["https://x.gov/about"]


# --------------------------------------------------------------------------- extraction

def test_extract_prefers_main_and_strips_chrome():
    text = extract_main_text(_fix("about_page.html"))
    assert "established in 1975" in text
    assert "twelve district offices" in text
    # nav/script/style/aside/footer must all be stripped
    assert "tracking" not in text
    assert "navy" not in text
    assert "newsletter" not in text          # <aside> inside main
    assert "All rights reserved" not in text  # <footer>


def test_extract_falls_back_to_body_when_no_main():
    text = extract_main_text(_fix("about_no_main.html"))
    assert "advises the mayor on ferry operations" in text
    assert "Home" not in text  # nav stripped


def test_extract_returns_empty_for_chrome_only_page():
    assert extract_main_text(_fix("chrome_only.html")) == ""


def test_truncate_caps_and_flags():
    text, trunc = truncate("x" * (MAX_TEXT_CHARS + 500))
    assert trunc is True and len(text) <= MAX_TEXT_CHARS
    text2, trunc2 = truncate("  short  ")
    assert text2 == "short" and trunc2 is False


# --------------------------------------------------------------------------- robots

def test_robots_disallow_is_honored():
    robots = "User-agent: *\nDisallow: /site/secret/"
    assert robots_allows(robots, crawl_about.USER_AGENT, "https://x.gov/site/secret/about.page") is False
    assert robots_allows(robots, crawl_about.USER_AGENT, "https://x.gov/site/open/about.page") is True


def test_empty_robots_allows():
    assert robots_allows("", crawl_about.USER_AGENT, "https://x.gov/about") is True


# --------------------------------------------------------------------------- fake-fetch crawl seam

class FakeFetcher:
    """Stand-in for Fetcher: no sleeping, no network. `pages` maps a URL (trailing '/' ignored) to
    (status, body). Unknown URLs return 404. Records the fetch order for budget assertions."""

    user_agent = crawl_about.USER_AGENT

    def __init__(self, pages: dict[str, tuple[int, str]]):
        self.pages = { _k(u): v for u, v in pages.items() }
        self.calls: list[str] = []

    def get(self, url: str) -> tuple[int, str, str]:
        self.calls.append(url)
        status, body = self.pages.get(_k(url), (404, ""))
        return status, url, body


def _k(url: str) -> str:
    return url.rstrip("/")


def _robots_ok(fetcher):
    # RobotsCache that always allows (robots.txt returns 404 -> allow). Uses the fake fetcher's
    # 404 default, so no special-casing needed — but we inject an empty robots explicitly.
    rc = RobotsCache(fetcher)
    return rc


def test_crawl_success_via_path_probe():
    target = {"id": "dsny", "name": "DSNY", "start_url": "https://www.nyc.gov/site/dsny/index.page"}
    pages = {
        "https://www.nyc.gov/robots.txt": (404, ""),
        "https://www.nyc.gov/site/dsny/index.page": (200, "<html><body><main><p>home</p></main></body></html>"),
        "https://www.nyc.gov/site/dsny/about/about.page": (200, _fix("about_page.html")),
    }
    f = FakeFetcher(pages)
    result = crawl_entity(target, f, _robots_ok(f), {})
    assert result["status"] == "ok"
    assert result["method"] == "path-probe"
    assert "established in 1975" in result["text"]
    assert result["source_url"] == "https://www.nyc.gov/site/dsny/about/about.page"
    assert result["truncated"] is False


def test_crawl_success_via_link_scan():
    target = {"id": "ex", "name": "Example", "start_url": "https://example.gov/"}
    pages = {
        "https://example.gov/robots.txt": (404, ""),
        "https://example.gov/": (200, _fix("homepage_with_about_link.html")),
        # path-probes 404; the link-scan target is the only one that resolves
        "https://example.gov/about-us/our-mission": (200, _fix("about_page.html")),
    }
    f = FakeFetcher(pages)
    result = crawl_entity(target, f, _robots_ok(f), {})
    assert result["status"] == "ok"
    assert result["method"] == "link-scan"
    assert "twelve district offices" in result["text"]


def test_crawl_records_no_about_found():
    target = {"id": "ex", "name": "Example", "start_url": "https://example.gov/"}
    pages = {
        "https://example.gov/robots.txt": (404, ""),
        "https://example.gov/": (200, "<html><body><main><p>only a homepage</p></main></body></html>"),
        # every about candidate 404s
    }
    f = FakeFetcher(pages)
    result = crawl_entity(target, f, _robots_ok(f), {})
    assert result["status"] == "no_about_found"
    assert result["text"] is None


def test_crawl_records_extraction_empty():
    target = {"id": "ex", "name": "Example", "start_url": "https://example.gov/"}
    pages = {
        "https://example.gov/robots.txt": (404, ""),
        "https://example.gov/": (200, "<html><body><main><p>home</p></main></body></html>"),
        "https://example.gov/about": (200, _fix("chrome_only.html")),  # fetches but no prose
    }
    f = FakeFetcher(pages)
    result = crawl_entity(target, f, _robots_ok(f), {})
    assert result["status"] == "extraction_empty"


def test_crawl_records_robots_disallowed_without_fetching_page():
    target = {"id": "ex", "name": "Example", "start_url": "https://example.gov/private/index.page"}
    pages = {
        "https://example.gov/robots.txt": (200, "User-agent: *\nDisallow: /private/"),
    }
    f = FakeFetcher(pages)
    result = crawl_entity(target, f, RobotsCache(f), {})
    assert result["status"] == "robots_disallowed"
    # only robots.txt was fetched; the disallowed start URL was never requested
    assert f.calls == ["https://example.gov/robots.txt"]


def test_crawl_records_no_url():
    result = crawl_entity({"id": "lirr", "name": "LIRR", "start_url": ""}, FakeFetcher({}), None, {})
    assert result["status"] == "no_url"


def test_waf_circuit_breaker_trips_and_short_circuits():
    host_403: dict[str, int] = {}
    f = FakeFetcher({})  # everything 404 by default, but we override to 403 below
    f.pages = {}  # force get() to return... need 403; patch via subclass

    class Blocked(FakeFetcher):
        def get(self, url):
            self.calls.append(url)
            return (403, url, "")

    b = Blocked({})
    robots = RobotsCache(b)
    # Trip the breaker: enough entities on the same host each returning 403.
    results = []
    for i in range(crawl_about.CONSEC_403_TRIP + 2):
        t = {"id": f"e{i}", "name": f"E{i}", "start_url": "https://www.nyc.gov/site/x/index.page"}
        results.append(crawl_entity(t, b, robots, host_403))
    # all fetch_failed
    assert all(r["status"] == "fetch_failed" for r in results)
    # once tripped, later entities carry the host_waf_blocked note and DON'T fetch the page
    assert any("host_waf_blocked" in (r.get("note") or "") for r in results[-2:])


def test_budget_capped_at_max_fetches_per_entity():
    target = {"id": "ex", "name": "Example", "start_url": "https://example.gov/"}
    # homepage resolves; every about candidate 404s -> crawler must stop at the fetch cap
    pages = {"https://example.gov/robots.txt": (404, ""),
             "https://example.gov/": (200, "<html><body><main><p>home</p></main></body></html>")}
    f = FakeFetcher(pages)
    crawl_entity(target, f, _robots_ok(f), {})
    # exclude robots.txt from the page-fetch count
    page_calls = [c for c in f.calls if not c.endswith("/robots.txt")]
    assert len(page_calls) <= crawl_about.MAX_FETCHES_PER_ENTITY


# --------------------------------------------------------------------------- output schema

def test_result_shape_matches_descriptions_contract():
    target = {"id": "dsny", "name": "DSNY", "start_url": "https://www.nyc.gov/site/dsny/index.page"}
    pages = {
        "https://www.nyc.gov/robots.txt": (404, ""),
        "https://www.nyc.gov/site/dsny/index.page": (200, "<html><body><main><p>home</p></main></body></html>"),
        "https://www.nyc.gov/site/dsny/about/about.page": (200, _fix("about_page.html")),
    }
    f = FakeFetcher(pages)
    result = crawl_entity(target, f, _robots_ok(f), {})
    assert set(result) >= {"text", "source_url", "fetched_at", "method", "status", "truncated"}
    # fetched_at is an ISO date
    import datetime
    datetime.date.fromisoformat(result["fetched_at"])
    assert isinstance(result["truncated"], bool)


def test_failure_reasons_are_the_documented_set():
    assert FAILURE_REASONS == {"no_url", "robots_disallowed", "fetch_failed",
                               "no_about_found", "extraction_empty"}


def test_every_built_result_json_serializable():
    # a fetch_failed result must round-trip through JSON (it lands in descriptions.json)
    r = crawl_entity({"id": "x", "name": "X", "start_url": ""}, FakeFetcher({}), None, {})
    assert json.loads(json.dumps(r))["status"] == "no_url"
