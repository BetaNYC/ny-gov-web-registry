"""Offline tests for probe_bot_access — no network. Covers the three places this script can be
silently wrong: the inverted circuit breaker, block-page-behind-200 detection, and robots grouping.
The AI-readability mode (--tier core|full) is tested in test_ai_readability.py.
"""

import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import probe_bot_access as p  # noqa: E402


# ----------------------------------------------------------------- circuit breaker (the big one)
def test_403_never_trips_the_breaker():
    """The whole study is 403s. If they trip the breaker, the run collects nothing."""
    b = p.CircuitBreaker()
    for _ in range(50):
        b.record(403, transport_error=False)
    assert b.tripped is False


def test_one_host_429_quarantines_that_host_and_does_not_stop_the_run():
    """REGRESSION — this is exactly how the 2026-08-28 00:17 run died.

    A single 429 from brooklynmuseum.org stopped all 126 hosts at 110 of 2,214 units. One museum's
    refusal is not a reason to stop measuring nyc.gov.
    """
    b = p.CircuitBreaker()
    b.record(429, transport_error=False, host="www.brooklynmuseum.org")
    assert b.tripped is False
    assert b.is_quarantined("www.brooklynmuseum.org") is True
    assert b.is_quarantined("www.nyc.gov") is False


def test_a_handful_of_429_refusers_across_a_big_corpus_does_not_stop_the_run():
    """REGRESSION #2 — the 2026-08-28 07:43 failure.

    Six hosts answered 429, most on FIRST contact (brooklynmuseum on robots.txt, javitscenter on
    llms.txt). Those are refusals, not rate limits, and a flat threshold of 5 read them as evidence
    about US and killed the run at 406 of 2,214 units.
    """
    b = p.CircuitBreaker()
    for i in range(120):                      # a realistic corpus of contacted hosts
        b.record(200, transport_error=False, host="ok{}.example.gov".format(i))
    for name in ("brooklynmuseum", "javitscenter", "metmuseum", "cirsplans", "dos", "nycsci"):
        b.record(429, transport_error=False, host="{}.example.gov".format(name))
    assert b.tripped is False, "six 429-refusers out of 126 hosts must not stop the run"
    assert len(b.quarantined) == 6


def test_systemic_429_no_longer_stops_the_run():
    """Policy change 2026-11-06 (Noel, plan.md §4.3a): pushback never stops the run. Even if
    every host 429s, each is quarantined and the run carries on to the end."""
    b = p.CircuitBreaker(monitor=p.NetworkMonitor(check=lambda: False, sleep=lambda s: None))
    for i in range(40):
        b.record(429, transport_error=False, host="h{}.example.gov".format(i))
    assert b.tripped is False
    assert len(b.quarantined) == 40


def test_repeated_429_from_one_host_never_trips_globally():
    b = p.CircuitBreaker()
    for _ in range(40):
        b.record(429, transport_error=False, host="same.example.gov")
    assert b.tripped is False
    assert len(b.quarantined) == 1


def _monitor(ok):
    calls = []

    def check():
        calls.append(1)
        return ok
    return p.NetworkMonitor(check=check, sleep=lambda s: None), calls


def test_transport_failures_on_one_host_never_trip():
    """A dead municipal site is quarantined, never evidence about us."""
    m, calls = _monitor(ok=False)
    b = p.CircuitBreaker(monitor=m)
    for _ in range(50):
        b.record(None, transport_error=True, host="dead.example.gov")
    assert b.tripped is False and calls == []
    assert b.is_quarantined("dead.example.gov")


def test_transport_streak_across_hosts_trips_only_if_reachability_fails():
    m, calls = _monitor(ok=False)
    b = p.CircuitBreaker(monitor=m)
    for i in range(p.NET_DOWN_STREAK - 1):
        b.record(None, transport_error=True, host="h{}.example.gov".format(i))
    assert b.tripped is False
    b.record(200, transport_error=False, host="ok.example.gov")       # a success resets
    for i in range(p.NET_DOWN_STREAK):
        b.record(None, transport_error=True, host="g{}.example.gov".format(i))
    assert b.tripped is True and "network" in b.reason
    assert len(calls) == 2, "checked twice before declaring our network down"


def test_transport_streak_with_network_up_continues():
    m, calls = _monitor(ok=True)
    b = p.CircuitBreaker(monitor=m)
    for i in range(p.NET_DOWN_STREAK * 3):
        b.record(None, transport_error=True, host="h{}.example.gov".format(i))
    assert b.tripped is False
    assert len(calls) == 3, "one check per full streak; each passing check resets the streak"


# ----------------------------------------------------------------- block page behind a 200
def test_akamai_denial_body_is_flagged():
    body = ("<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY><H1>Access Denied</H1>"
            "You don't have permission to access \"http://www.nyc.gov/\" on this server."
            "Reference #18.9378ce17.1787888760.23aef639</BODY></HTML>")
    cls, is_block = p.classify_body(body)
    assert is_block is True
    assert cls in ("access_denied", "akamai_denied", "akamai_reference")


def test_real_content_is_not_flagged():
    body = "<html><body>" + ("Official Website of New York City Government. " * 40) + "</body></html>"
    cls, is_block = p.classify_body(body)
    assert (cls, is_block) == ("content", False)


def test_near_empty_is_distinguished_from_a_block():
    cls, is_block = p.classify_body("<html><body></body></html>")
    assert cls == "near_empty" and is_block is False


# ----------------------------------------------------------------- robots parsing
def test_nycgov_robots_is_permissive():
    r = p.parse_robots("User-agent: *\nDisallow: /html/misc/\n")
    assert r["names_any_ai_token"] is False
    assert r["wildcard_disallow"] == ["/html/misc/"]


def test_council_robots_crawl_delay_and_sitemap():
    r = p.parse_robots(
        "User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\n"
        "Crawl-delay: 10\n\nSitemap: https://council.nyc.gov/wp-sitemap.xml\n")
    assert r["wildcard_crawl_delay"] == 10.0
    assert "/wp-admin/" in r["wildcard_disallow"]


def test_consecutive_user_agents_share_one_group():
    """RFC 9309 §2.2.1 — stacked User-agent lines share the following rules."""
    r = p.parse_robots("User-agent: GPTBot\nUser-agent: ClaudeBot\nDisallow: /\n")
    assert r["groups"]["GPTBot"]["disallow"] == ["/"]
    assert r["groups"]["ClaudeBot"]["disallow"] == ["/"]
    assert set(r["ai_tokens_named"]) >= {"GPTBot", "ClaudeBot"}


def test_a_new_group_starts_after_a_rule():
    r = p.parse_robots("User-agent: GPTBot\nDisallow: /\nUser-agent: *\nDisallow: /admin\n")
    assert r["groups"]["GPTBot"]["disallow"] == ["/"]
    assert r["groups"]["*"]["disallow"] == ["/admin"]      # must NOT inherit GPTBot's rule


def test_ai_tokens_detected_case_insensitively():
    r = p.parse_robots("User-agent: gptbot\nDisallow: /\n")
    assert "GPTBot" in r["ai_tokens_named"]


def test_comments_are_stripped():
    r = p.parse_robots("# robots welcome\nUser-agent: *  # everyone\nDisallow:  # nothing\n")
    assert r["wildcard_disallow"] == [""]


# ----------------------------------------------------------------- identities & scheduling
def test_every_identity_declares_verification_and_doc():
    for i in p.IDENTITIES:
        assert "string_verified" in i and "doc" in i, i["key"]
        assert i["klass"], i["key"]


def test_robots_only_tokens_are_never_sent_as_live_agents():
    """Google-Extended has no request UA. Sending it would measure a bot that does not exist."""
    sent = " ".join((i["ua"] or "") for i in p.IDENTITIES)
    for token in p.ROBOTS_ONLY_TOKENS:
        assert token not in sent


def test_agentic_class_is_represented():
    assert any(i["klass"] == "ai_agentic" for i in p.IDENTITIES)


def test_interleave_never_puts_a_host_back_to_back():
    work = ([{"host": "a", "url": "u{}".format(i), "identity": "x"} for i in range(20)] +
            [{"host": "b", "url": "v{}".format(i), "identity": "x"} for i in range(3)])
    out = p.interleave(work, random.Random(1))
    assert len(out) == 23
    runs = [out[i]["host"] == out[i + 1]["host"] for i in range(len(out) - 1)]
    # host 'a' dominates so some adjacency is unavoidable, but 'b' must be spread out, never paired
    b_positions = [i for i, w in enumerate(out) if w["host"] == "b"]
    assert all(b_positions[i + 1] - b_positions[i] > 1 for i in range(len(b_positions) - 1))
    assert runs.count(False) >= 3


def test_build_work_tier_b_only_touches_governed_hosts():
    targets = [
        {"entity_id": "e1", "start_url": "https://www.nyc.gov/site/a/index.page"},
        {"entity_id": "e2", "start_url": "https://www.nyc.gov/site/b/index.page"},
        {"entity_id": "e3", "start_url": "https://example.gov/"},
        {"entity_id": "e4", "start_url": "https://example.gov/two"},
    ]
    work = p.build_work(targets, "all")
    tier_b = [w for w in work if w["kind"] == "probe_tier_b"]
    assert tier_b, "tier B should produce work for the concentrated host"
    assert {w["host"] for w in tier_b} == {"www.nyc.gov"}
    assert {w["identity"] for w in tier_b} == set(p.TIER_B_IDENTITY_KEYS)
    # the second example.gov target must NOT generate tier B work
    assert not any(w["host"] == "example.gov" for w in tier_b)


def test_build_work_tier_a_is_one_target_per_host_full_matrix():
    targets = [
        {"entity_id": "e1", "start_url": "https://example.gov/"},
        {"entity_id": "e2", "start_url": "https://example.gov/two"},
    ]
    work = p.build_work(targets, "a")
    tier_a = [w for w in work if w["kind"] == "probe_tier_a"]
    assert len({w["url"] for w in tier_a}) == 1              # one representative
    assert len(tier_a) == len(p.IDENTITIES)                  # full matrix
    assert sum(1 for w in work if w["kind"] == "robots") == 1
    assert sum(1 for w in work if w["kind"] == "llms") == 1


def test_governed_hosts_are_paced_far_slower_than_the_floor():
    for host, delay in p.HOST_GOVERNORS.items():
        assert delay >= p.GLOBAL_PACE_S * 3, host


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print("  ok   {}".format(name))
        except Exception:
            failed += 1
            print("  FAIL {}".format(name))
            traceback.print_exc()
    print("\n{}/{} passed".format(len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
