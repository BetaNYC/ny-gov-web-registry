"""Offline tests for the WAF-recovery tooling — candidate derivation, verbatim extraction from
browser-pane captures, the recovery-record schema, and the merge rules. NO test touches the
network; captures are in-memory strings shaped like real get_page_text output."""

import extract_waf_descriptions as ex
import merge_descriptions as mg
import plan_waf_candidates as pl

# --- Realistic nyc.gov Full Site Editing "About <Agency>" capture -----------------------------

NYCHA_CAPTURE = """Title: NYCHA - About
URL: https://nyc.gov
Source element: <div>
---
About NYCHA
News
Contact
Select
Leadership
Share
About NYCHA

The New York City Housing Authority (NYCHA), the largest public housing authority in North America, was created in 1934 to provide decent, affordable housing for low- and moderate-income New Yorkers.

*As per July 2023 U.S. Census Population Estimate

You can also follow us on social media – we’re on Facebook
(opens in new tab)
, Twitter
(opens in new tab)

Audio description: This is the NYCHA video playlist.

Tab Context:
- Executed on tabId: seed"""

DSNY_CAPTURE = """Title: About DSNY - DSNY
URL: https://nyc.gov
Source element: <div>
---
About DSNY
Leadership
Contact
Select
Share
About DSNY

The NYC Department of Sanitation (DSNY) keeps New York City clean, safe, and healthy by collecting, recycling, and disposing of waste.

We operate 59 district garages and manage a fleet of more than 2,000 rear-loading collection trucks.

Tab Context:
- Executed on tabId: seed"""

NOT_FOUND_CAPTURE = """Title: NYC
URL: https://nyc.gov
Source element: <div>
---
NYC
We’re Sorry.
You have reached an outdated or non-existing page.
 Download the 311 app

Tab Context:
- Executed on tabId: seed"""

NAV_ONLY_CAPTURE = """Title: Some Board - X
URL: https://nyc.gov
Source element: <div>
---
About
News
Contact
Select
Share

Tab Context:
- Executed on tabId: seed"""


# --- candidate derivation ---------------------------------------------------------------------

def test_candidates_index_page_uses_about_conventions():
    c = pl.candidate_urls("https://www.nyc.gov/site/nycha/index.page")
    assert c[0] == "https://www.nyc.gov/site/nycha/about/about-nycha.page"
    assert c[1] == "https://www.nyc.gov/site/nycha/about/about.page"
    assert c[-1] == "https://www.nyc.gov/site/nycha/index.page"  # landing fallback last


def test_candidates_include_overview_conventions():
    # FDNY's About lives at /about/overview/overview.page — the deeper convention must be probed.
    c = pl.candidate_urls("https://www.nyc.gov/site/fdny/index.page")
    assert "https://www.nyc.gov/site/fdny/about/overview/overview.page" in c
    assert "https://www.nyc.gov/site/fdny/about/overview.page" in c


def test_candidates_include_legacy_html_fallback():
    # A /site/ target also gets one legacy /html/ About fallback (same slug).
    c = pl.candidate_urls("https://www.nyc.gov/site/dsny/index.page")
    assert "https://www.nyc.gov/html/dsny/html/about/about.shtml" in c


def test_candidates_legacy_start_url_probes_legacy_family():
    # DOT's start URL is already legacy; derive the legacy slug from it and probe the legacy About.
    c = pl.candidate_urls("https://www.nyc.gov/html/dot/html/home/home.shtml")
    assert c[0] == "https://www.nyc.gov/html/dot/html/about/about.shtml"
    assert "https://www.nyc.gov/html/dot/html/about/about.html" in c
    assert "https://www.nyc.gov/html/dot/html/home/home.shtml" in c  # start preserved


def test_candidates_specific_subpage_tried_first():
    c = pl.candidate_urls("https://www.nyc.gov/site/wkdev/workforce-board/about-the-council.page")
    assert c[0] == "https://www.nyc.gov/site/wkdev/workforce-board/about-the-council.page"


def test_candidates_www1_about_paths_use_canonical_host():
    c = pl.candidate_urls("https://www1.nyc.gov/site/ocme/index.page")
    assert c[0] == "https://www.nyc.gov/site/ocme/about/about-ocme.page"
    assert "https://www1.nyc.gov/site/ocme/index.page" in c  # original preserved as fallback


def test_candidates_content_target_is_start_only():
    c = pl.candidate_urls("https://www.nyc.gov/content/oti/pages/")
    assert c == ["https://www.nyc.gov/content/oti/pages/"]


def test_candidates_capped_and_deduped():
    for url in ("https://www.nyc.gov/site/x/index.page",
                "https://www.nyc.gov/site/x/about/about.page"):
        c = pl.candidate_urls(url)
        assert len(c) <= pl.MAX_CANDIDATES
        assert len(c) == len(set(u.rstrip("/") for u in c))


# --- extraction -------------------------------------------------------------------------------

def test_extract_verbatim_drops_nav_and_boilerplate():
    text = ex.extract_description(NYCHA_CAPTURE)
    assert text.startswith("The New York City Housing Authority (NYCHA)")
    assert "*As per July 2023" in text          # footnote before the stop marker kept
    assert "News" not in text and "Contact" not in text  # nav dropped
    assert "social media" not in text           # boilerplate tail dropped
    assert "Audio description" not in text
    assert "(opens in new tab)" not in text


def test_extract_multi_paragraph_verbatim():
    text = ex.extract_description(DSNY_CAPTURE)
    assert text.startswith("The NYC Department of Sanitation")
    assert "\n\n" in text  # two paragraphs preserved
    assert "We operate 59 district garages" in text
    assert "Leadership" not in text


def test_extract_not_found_returns_empty():
    assert ex.is_not_found(NOT_FOUND_CAPTURE) is True
    assert ex.extract_description(NOT_FOUND_CAPTURE) == ""


def test_extract_nav_only_returns_empty():
    assert ex.extract_description(NAV_ONLY_CAPTURE) == ""


def test_extract_caps_at_max_chars():
    long_para = "word " * 4000  # ~20k chars, one long prose line
    cap = f"Title: X\nURL: https://nyc.gov\n---\nAbout X\n\n{long_para}\n\nTab Context:\n- x"
    text = ex.extract_description(cap)
    out, trunc = ex.truncate(text)
    assert trunc is True
    assert len(out) <= ex.MAX_TEXT_CHARS


# --- record + recovery build ------------------------------------------------------------------

def test_record_ok_shape():
    rec = ex.record_for({
        "source_url": "https://www.nyc.gov/site/nycha/about/about-nycha.page",
        "raw_text": NYCHA_CAPTURE, "status_hint": "found",
    })
    assert rec["status"] == "ok"
    assert rec["method"] == "browser-pane"
    assert set(rec) == {"text", "source_url", "fetched_at", "method", "status", "truncated"}
    assert rec["text"].startswith("The New York City Housing Authority")


def test_record_extraction_empty_when_no_prose():
    rec = ex.record_for({"source_url": "u", "raw_text": NAV_ONLY_CAPTURE, "status_hint": "found"})
    assert rec["status"] == "extraction_empty"
    assert rec["text"] is None


def test_record_passthrough_hints():
    assert ex.record_for({"source_url": "u", "status_hint": "fetch_failed"})["status"] == "fetch_failed"
    assert ex.record_for({"source_url": "u", "status_hint": "no_about_found"})["status"] == "no_about_found"


def test_build_recovery_schema():
    captures = {"nycha": {"source_url": "u", "raw_text": NYCHA_CAPTURE, "status_hint": "found"}}
    rec = ex.build_recovery(captures)
    assert rec["method"] == "browser-pane"
    assert rec["descriptions"]["nycha"]["status"] == "ok"


# --- merge rules ------------------------------------------------------------------------------

def _base(desc):
    return {"_generated_from": "crawl_about.py", "user_agent": "ua", "descriptions": desc}


def _ok(text="x"):
    return {"text": text, "source_url": "u", "fetched_at": "d", "method": "m",
            "status": "ok", "truncated": False}


def _failed():
    return {"text": None, "source_url": "u", "fetched_at": "d", "method": None,
            "status": "fetch_failed", "truncated": False}


def test_merge_recovery_ok_overrides_fetch_failed():
    base = _base({"a": _failed()})
    recovery = {"descriptions": {"a": _ok("recovered")}}
    merged, stats = mg.merge(base, recovery)
    assert merged["descriptions"]["a"]["text"] == "recovered"
    assert stats["recovered_ok"] == 1


def test_merge_never_overwrites_existing_ok():
    base = _base({"a": _ok("original")})
    recovery = {"descriptions": {"a": _ok("recovered")}}
    merged, stats = mg.merge(base, recovery)
    assert merged["descriptions"]["a"]["text"] == "original"
    assert stats["skipped_protected"] == 1


def test_merge_ok_upgrades_no_about_found():
    # A later batch finds a deeper/legacy about URL for an entity previously marked no_about_found.
    base = _base({"a": {"status": "no_about_found", "text": None}})
    recovery = {"descriptions": {"a": _ok("found at last")}}
    merged, stats = mg.merge(base, recovery)
    assert merged["descriptions"]["a"]["text"] == "found at last"
    assert stats["recovered_ok"] == 1


def test_merge_non_ok_recovery_reclassifies_fetch_failed():
    # This pass targets the WAF fetch_failed bucket: a browser-pane "no_about_found" is more
    # honest than leaving "fetch_failed" (which implies the WAF is still blocking).
    base = _base({"a": _failed()})
    recovery = {"descriptions": {"a": {"status": "no_about_found", "text": None}}}
    merged, stats = mg.merge(base, recovery)
    assert merged["descriptions"]["a"]["status"] == "no_about_found"
    assert stats["reclassified"] == 1


def test_merge_protects_non_fetch_failed_base():
    # A base status outside the WAF bucket (e.g. no_url) is never overwritten by recovery.
    base = _base({"a": {"status": "no_url", "text": None}})
    recovery = {"descriptions": {"a": _ok("recovered")}}
    merged, stats = mg.merge(base, recovery)
    assert merged["descriptions"]["a"]["status"] == "no_url"
    assert stats["skipped_protected"] == 1


def test_merge_fills_new_id():
    base = _base({})
    recovery = {"descriptions": {"a": _ok()}}
    merged, stats = mg.merge(base, recovery)
    assert "a" in merged["descriptions"]
    assert stats["recovered_ok"] == 1


def test_merge_is_idempotent():
    base = _base({"a": _failed()})
    recovery = {"descriptions": {"a": _ok("recovered")}}
    once, _ = mg.merge(base, recovery)
    twice, stats = mg.merge(once, recovery)
    assert once["descriptions"] == twice["descriptions"]
    assert stats["skipped_protected"] == 1  # now already ok, protected


def test_merge_preserves_base_wrapper():
    base = _base({"a": _failed()})
    base["crawl_started_at"] = "t0"
    recovery = {"descriptions": {"a": _ok()}}
    merged, _ = mg.merge(base, recovery, merged_at="t1")
    assert merged["crawl_started_at"] == "t0"
    assert merged["_recovery_merged_at"] == "t1"


def test_merge_does_not_mutate_inputs():
    base = _base({"a": _failed()})
    recovery = {"descriptions": {"a": _ok("recovered")}}
    mg.merge(base, recovery)
    assert base["descriptions"]["a"]["status"] == "fetch_failed"
