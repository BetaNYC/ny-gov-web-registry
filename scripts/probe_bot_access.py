"""probe_bot_access.py — measure how NYC government hosts treat self-identifying AI agents.

WHY THIS EXISTS
  A 2026-08-27 spot check found `www.nyc.gov` publishing a maximally permissive robots.txt
  (`Disallow: /html/misc/`, no AI directives at all) while its Akamai edge returned 403 to every
  client that identified itself honestly — GPTBot, ClaudeBot, plain urllib — and 200 to a desktop
  Chrome User-Agent. The stated policy and the enforced policy disagree.

  This script generalizes that check across the registry's crawl set so the divergence can be
  counted rather than anecdoted. It answers two questions kept deliberately separate:

    STATED   — what does the host say? (robots.txt, llms.txt)
    ENFORCED — what does the host do, per declared identity? (live status + body classification)

WHAT IT IS NOT
  Not a content crawler. One GET per (target, identity); nothing followed, nothing recursive.
  Not an evasion tool. A 403 to an honest agent is THE DATUM — we record it and move on. There is
  no retry-until-through, no cookie harvesting, no TLS-fingerprint work.

THE ONE NON-OBVIOUS DESIGN POINT
  `crawl_about.py` trips a circuit-breaker after 4 consecutive 403s, because there a 403 means "we
  failed." Here a 403 is the expected, wanted result. That breaker would fire constantly and the run
  would collect nothing. So the breaker here trips on 429s, connection failures, and a
  sudden-everything-fails shift — NEVER on 403. See CircuitBreaker.

ETIQUETTE
  Runs from a residential or office connection, so the pacing is deliberately conservative:
  round-robin across hosts (never host-by-host), a global pace with jitter, a per-host floor, and a
  much stricter governor for www.nyc.gov / www1.nyc.gov which carry ~150 of the targets between
  them. A 429 or a transport failure quarantines that host and the run continues; only our own
  network being down stops it (see CircuitBreaker and NetworkMonitor; the rule was corrected
  twice on 2026-08-28 and replaced on 2026-11-06).

Run:  python3 scripts/probe_bot_access.py --out data/probe/  [--tier a|b|all] [--dry-run]
Resume: same command; completed work units are read from the checkpoint and skipped.

AI-READABILITY MODE (--tier core|full; docs/ai-readability/tests.md, plan.md §4)
  Runs the test catalog v0.1: robots/llms/sitemap, the homepage once per identity, a 404 probe and
  an http:// check per host, then parses the ONE research-UA homepage body for the content tests.
  Output: data/ai-readability/runs/<YYYY-MM-DD>-<vantage>/{run.json, results.jsonl, review.jsonl,
  status.json, summary.md} (git-ignored: results stay private). Pushback quarantines a host and
  never stops the run; see the AI-READABILITY RUNNER section below.

    python3 scripts/probe_bot_access.py --tier core --targets-file pilot.json --dry-run
    python3 scripts/probe_bot_access.py --tier core --targets-file pilot.json --vantage office \
        --machine office --on-exit "bash notify.sh"
  Resume: the status.json `resume_cmd` (same command plus --run-dir).

DEEP-PAGE MODE (--deep-pages; issue #35, docs/ai-readability/tests.md "Deep pages")
  One unit per page on a host shared by several registry bodies, plus
  data/ai-readability/deep-pages-extra.csv. Each page host's robots.txt is fetched once per run;
  each page once per Tier B identity. Page-level rows only (PAGE_TESTS), with `page_url` and
  scope "page". Same pacing, quarantine and resume rules; resume is per page.

    python3 scripts/probe_bot_access.py --deep-pages --dry-run
    python3 scripts/probe_bot_access.py --deep-pages --vantage office --machine office \
        --run-dir data/ai-readability/runs/<YYYY-MM-DD>-office-pages
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import pathlib
import random
import re
import shlex
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date as _date
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ai_readability as ar  # noqa: E402  (pure parsers for --tier core|full)

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = ROOT / "data" / "crawl_targets.json"

# --------------------------------------------------------------------------- etiquette constants
GLOBAL_PACE_S = 6.0          # baseline gap between ANY two requests
GLOBAL_JITTER_S = 2.0        # +/- jitter so the run does not look metronomic
MIN_HOST_DELAY_S = 30.0      # floor between two requests to the same host
REQUEST_TIMEOUT_S = 25
HARD_REQUEST_CAP = 2600      # absolute ceiling; exit regardless of progress
RETRY_BACKOFF_S = 5.0        # single retry, 5xx/timeout only — never on 403

# Hosts carrying an outsized share of the work get their own governor. www.nyc.gov holds ~131
# Tier B targets plus its Tier A matrix; it is both the heaviest load and the most likely to notice.
HOST_GOVERNORS = {
    "www.nyc.gov": 20.0,
    "www1.nyc.gov": 20.0,
}

# --------------------------------------------------------------------------- identities
# From 2026-11-10 (issue #44) every vendor crawler identity comes from the checked list
# data/ai-readability/crawlers.csv, through crawler_list.build_identities(): labels copied word for
# word, never assembled, no version stand-in ever filled in. Only the CONTROLS below are defined in
# code, because they are not vendor crawlers: our own research crawler, the library default, and
# the desktop-Chrome mechanism probe (disclosed in the dataset).
import crawler_list  # noqa: E402  (pure: reads crawlers.csv)

CONTROL_IDENTITIES = [
    {
        "key": "baseline_default",
        "label": "unadorned client (Python-urllib default)",
        "ua": None,  # None => library default; an honest, unremarkable non-browser client
        "klass": "baseline",
        "string_verified": True,
        "label_kind": "control",
        "doc": "n/a — library default",
    },
    {
        "key": "research_betanyc",
        "label": "BetaNYC research crawler (attributable, contactable)",
        "ua": ("BetaNYC-agentic-access-study/1.0 "
               "(+https://github.com/BetaNYC/ny-gov-web-registry; noel@beta.nyc)"),
        "klass": "baseline_honest",
        "string_verified": True,
        "label_kind": "control",
        "doc": "ours",
    },
    {
        "key": "mechanism_probe_chrome",
        "label": "MECHANISM PROBE — desktop Chrome UA (measures how deep the check goes)",
        # NOT a way in. This isolates whether the gate reads only the UA string (as www.nyc.gov
        # does) or binds clearance to a TLS fingerprint (as a860-gpp.nyc.gov does). A site defeated
        # by a one-line header change did not make a considered decision about AI agents.
        "ua": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like "
               "Gecko) Chrome/126.0.0.0 Safari/537.36"),
        "klass": "mechanism_probe",
        "string_verified": True,
        "label_kind": "control",
        "doc": "n/a — deliberate control, disclosed in the dataset",
    },
]
CONTROL_KEYS = tuple(i["key"] for i in CONTROL_IDENTITIES)


def load_crawler_identities(path=None) -> "tuple[list, list, list]":
    """(rows, sent vendor identities, not-sent records) from crawlers.csv."""
    rows = crawler_list.load(path)
    sent, not_sent = crawler_list.build_identities(rows)
    return rows, sent, not_sent


CRAWLER_ROWS, VENDOR_IDENTITIES, NOT_SENT = load_crawler_identities()
_CTRL = {i["key"]: i for i in CONTROL_IDENTITIES}
# Controls first (research before anything else), then the list in its own order.
IDENTITIES = [_CTRL["baseline_default"], _CTRL["research_betanyc"]] + VENDOR_IDENTITIES + \
    [_CTRL["mechanism_probe_chrome"]]

# The legacy tiers (a, b, all: the 2026-08 bot-access study) keep their original twelve names so a
# re-run stays comparable and under HARD_REQUEST_CAP; the labels now come from crawlers.csv too.
LEGACY_IDENTITY_KEYS = ("baseline_default", "research_betanyc", "googlebot", "gptbot", "ccbot",
                        "claudebot", "oai_searchbot", "perplexitybot", "chatgpt_user",
                        "perplexity_user", "claude_user", "mechanism_probe_chrome")
LEGACY_IDENTITIES = [i for k in LEGACY_IDENTITY_KEYS for i in IDENTITIES if i["key"] == k]

# Tier B asks only "does this deep path differ from its host's apex?" Three identities answer that:
# an honest baseline, one honest AI agent, and the mechanism probe. Twelve would be waste.
TIER_B_IDENTITY_KEYS = ("research_betanyc", "claudebot", "mechanism_probe_chrome")

# Product tokens that are robots.txt CONTROL ONLY — they never issue a request. Scored in the
# stated-policy arm; sending them as a live UA would be an error.
ROBOTS_ONLY_TOKENS = tuple(r["name"] for r in CRAWLER_ROWS
                           if r["send"] == "rules-only" and r["ua_exact"] == crawler_list.TOKEN_ONLY)

# Tokens we look for when parsing robots.txt: every name in crawlers.csv (sent or rules-only) plus
# catalog 0.1's legacy read-only tokens (ai_readability.AI_TOKENS). Never a list kept here.
ROBOTS_AI_TOKENS = ar.AI_TOKENS
ROBOTS_SEARCH_TOKENS = ("Googlebot", "bingbot", "Slurp", "DuckDuckBot", "Baiduspider", "YandexBot")

# Body fingerprints for a challenge/block page served with a 2xx status. research-methods.md
# documents this trap explicitly: a status-code-only reading scores it as success.
BLOCK_PAGE_PATTERNS = (
    (r"access denied", "access_denied"),
    (r"you don'?t have permission to access", "akamai_denied"),
    (r"reference\s*#\d+\.", "akamai_reference"),
    (r"errors\.edgesuite\.net", "akamai_edgesuite"),
    (r"attention required.*cloudflare", "cloudflare_challenge"),
    (r"cf-browser-verification|cf_chl_", "cloudflare_challenge"),
    (r"incapsula incident id|_incapsula_resource", "imperva_incapsula"),
    (r"request unsuccessful.*incapsula", "imperva_incapsula"),
    (r"<title>\s*just a moment", "cloudflare_jschallenge"),
    (r"bot detection|are you a robot|verify you are human", "generic_bot_challenge"),
    (r"your request has been blocked", "generic_blocked"),
)

WAF_HEADER_HINTS = (
    ("akamai", ("x-akamai", "akamai-", "x-akamai-transformed")),
    ("cloudflare", ("cf-ray", "cf-cache-status")),
    ("imperva", ("x-iinfo", "x-cdn")),
    ("aws_cloudfront", ("x-amz-cf-id", "x-amz-cf-pop")),
    ("fastly", ("x-served-by", "x-fastly")),
)
WAF_COOKIE_HINTS = (
    ("akamai_botmanager", ("bm_so", "bm_lso", "bm_sc", "_abck", "ak_bmsc")),
    ("cloudflare", ("__cf_bm", "cf_clearance")),
    ("imperva_incapsula", ("incap_ses", "visid_incap", "nlbi_")),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify_body(body: str) -> "tuple[str, bool]":
    """Return (classification, is_block_page). A 200 carrying a challenge page is NOT success."""
    low = body[:6000].lower()
    for pattern, name in BLOCK_PAGE_PATTERNS:
        if re.search(pattern, low):
            return name, True
    if len(body.strip()) < 200:
        return "near_empty", False        # JS shell or stub — noteworthy, not a block
    return "content", False


def fingerprint_waf(headers, set_cookies: str) -> "list[str]":
    found = []
    hkeys = {k.lower() for k in headers.keys()}
    for name, hints in WAF_HEADER_HINTS:
        if any(any(h in k for k in hkeys) for h in hints):
            found.append(name)
    low_cookies = (set_cookies or "").lower()
    for name, hints in WAF_COOKIE_HINTS:
        if any(h in low_cookies for h in hints):
            found.append(name)
    server = (headers.get("Server") or "").lower()
    if "akamai" in server and "akamai" not in found:
        found.append("akamai")
    return sorted(set(found))


class CircuitBreaker:
    """Trips ONLY when our own network is down — NEVER on 403, and (since 2026-11-06) never on
    429s either, however many hosts send them.

    A 403 is the measurement. Treating it as failure (as crawl_about.py rightly does for its own
    purpose) would abort this run almost immediately and collect nothing.

    HISTORY OF THE 429 RULE
      2026-08-28 (first run): any single 429 stopped everything. Both passes died in ~11 minutes on
      one museum's 429 — and brooklynmuseum.org sent it on the FIRST request we ever made, so it was
      a refusal, not a rate limit. Fixed to quarantine-that-host.
      2026-08-28 (second run): a flat "5 hosts 429'd" global trip killed the run on six independent
      refusers. Fixed to a floor-plus-share rule.
      2026-11-06 (Noel, plan.md §4.3a): pushback never stops the run. A 429 — or a timeout, reset,
      TLS or DNS failure — quarantines THAT host for the rest of the run, and the run carries on.
      The only global stop is NetworkMonitor deciding that our network is down: a streak of
      transport failures across several distinct hosts AND no neutral reachability URL answering.
    """

    def __init__(self, monitor=None) -> None:
        self.monitor = monitor if monitor is not None else NetworkMonitor()
        self.quarantined = {}          # host -> reason
        self.hosts_contacted = set()

    @property
    def tripped(self) -> bool:
        return self.monitor.down

    @property
    def reason(self) -> str:
        return self.monitor.reason

    def record(self, status, transport_error: bool, host: str = "") -> None:
        if host:
            self.hosts_contacted.add(host)
        if status == 429:
            if host and host not in self.quarantined:
                self.quarantined[host] = "HTTP 429 — recorded as a refusal; remaining work dropped"
            self.monitor.record(host, False)
            return
        if transport_error and host and host not in self.quarantined:
            self.quarantined[host] = "transport failure — recorded; remaining work dropped"
        self.monitor.record(host, transport_error)

    def is_quarantined(self, host: str) -> bool:
        return host in self.quarantined


class Pacer:
    """Global pace + per-host floor + per-host governors for the concentrated hosts."""

    def __init__(self, rng: random.Random) -> None:
        self._last_global = 0.0
        self._last_host = {}
        self._rng = rng

    def wait(self, host: str) -> None:
        now = time.monotonic()
        gap = GLOBAL_PACE_S + self._rng.uniform(-GLOBAL_JITTER_S, GLOBAL_JITTER_S)
        waits = [self._last_global + gap - now]
        floor = HOST_GOVERNORS.get(host, MIN_HOST_DELAY_S)
        if host in self._last_host:
            waits.append(self._last_host[host] + floor - now)
        delay = max(waits)
        if delay > 0:
            time.sleep(delay)
        t = time.monotonic()
        self._last_global = t
        self._last_host[host] = t


def fetch(url: str, ua, pacer: Pacer, breaker: CircuitBreaker) -> dict:
    """One GET. Returns a result dict. Never raises for HTTP status."""
    host = urllib.parse.urlparse(url).netloc
    pacer.wait(host)

    headers = {"Accept": "*/*"}
    if ua:
        headers["User-Agent"] = ua

    attempt = 0
    while True:
        attempt += 1
        started = time.time()
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                raw = resp.read(120_000)
                body = raw.decode(resp.headers.get_content_charset() or "utf-8", "replace")
                cls, is_block = classify_body(body)
                breaker.record(resp.status, transport_error=False, host=host)
                return {
                    "status": resp.status, "final_url": resp.geturl(),
                    "bytes": len(raw), "body_class": cls, "is_block_page": is_block,
                    "waf": fingerprint_waf(resp.headers, resp.headers.get("Set-Cookie", "")),
                    "server": resp.headers.get("Server"),
                    "elapsed_s": round(time.time() - started, 2),
                    "transport_error": None, "body": body,
                }
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read(120_000)
            except Exception:
                pass
            body = raw.decode("utf-8", "replace")
            cls, is_block = classify_body(body)
            breaker.record(e.code, transport_error=False, host=host)
            # 403 is data. Never retry it. Retry only transient server-side failures.
            if e.code >= 500 and attempt == 1 and not breaker.tripped:
                time.sleep(RETRY_BACKOFF_S)
                continue
            return {
                "status": e.code, "final_url": e.url if hasattr(e, "url") else url,
                "bytes": len(raw), "body_class": cls, "is_block_page": is_block,
                "waf": fingerprint_waf(e.headers, e.headers.get("Set-Cookie", "")) if e.headers else [],
                "server": e.headers.get("Server") if e.headers else None,
                "elapsed_s": round(time.time() - started, 2),
                "transport_error": None, "body": body,
            }
        except Exception as e:  # timeout, DNS, TLS, connection reset
            breaker.record(None, transport_error=True, host=host)
            if attempt == 1 and not breaker.tripped:
                time.sleep(RETRY_BACKOFF_S)
                continue
            return {
                "status": None, "final_url": url, "bytes": 0,
                "body_class": None, "is_block_page": None, "waf": [], "server": None,
                "elapsed_s": round(time.time() - started, 2),
                "transport_error": "{}: {}".format(type(e).__name__, str(e)[:200]),
                "body": "",
            }


def parse_robots(text: str) -> dict:
    """Extract per-token rules. Deliberately simple: we report what is DECLARED, not resolve
    precedence for a given path. Precedence is a separate analysis question."""
    groups = defaultdict(lambda: {"disallow": [], "allow": [], "crawl_delay": None})
    current = []          # agents in the group currently being built
    starting_group = True  # consecutive User-agent lines share one group (RFC 9309 §2.2.1)
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if not starting_group:
                current = []          # a rule intervened, so this begins a NEW group
                starting_group = True
            current.append(value)
            groups[value]             # touch, so a group with no rules still registers
        elif field in ("disallow", "allow"):
            starting_group = False
            for agent in current:
                groups[agent][field].append(value)
        elif field == "crawl-delay":
            starting_group = False
            for agent in current:
                try:
                    groups[agent]["crawl_delay"] = float(value)
                except ValueError:
                    pass
    named = dict(groups)
    ai_named = [t for t in ROBOTS_AI_TOKENS
                if any(t.lower() == k.lower() for k in named)]
    search_named = [t for t in ROBOTS_SEARCH_TOKENS
                    if any(t.lower() == k.lower() for k in named)]
    return {
        "groups": named,
        "ai_tokens_named": ai_named,
        "search_tokens_named": search_named,
        "names_any_ai_token": bool(ai_named),
        "wildcard_crawl_delay": named.get("*", {}).get("crawl_delay"),
        "wildcard_disallow": named.get("*", {}).get("disallow", []),
    }


def build_work(targets: list, tier: str) -> list:
    """Emit the ordered work list. Tier A = full matrix, one target per host.
    Tier B = the concentrated deep paths at 3 identities."""
    by_host = defaultdict(list)
    for t in targets:
        url = t.get("start_url")
        if not url:
            continue
        by_host[urllib.parse.urlparse(url).netloc.lower()].append(t)

    ident_by_key = {i["key"]: i for i in LEGACY_IDENTITIES}
    work = []

    for host, items in sorted(by_host.items()):
        items_sorted = sorted(items, key=lambda x: x.get("entity_id") or x.get("id") or "")
        rep = items_sorted[0]

        if tier in ("a", "all"):
            work.append({"kind": "robots", "host": host,
                         "url": "https://{}/robots.txt".format(host), "identity": "research_betanyc"})
            work.append({"kind": "llms", "host": host,
                         "url": "https://{}/llms.txt".format(host), "identity": "research_betanyc"})
            for ident in LEGACY_IDENTITIES:
                work.append({"kind": "probe_tier_a", "host": host, "url": rep["start_url"],
                             "identity": ident["key"],
                             "entity_id": rep.get("entity_id") or rep.get("id"),
                             "entity_name": rep.get("name")})

        if tier in ("b", "all") and host in HOST_GOVERNORS:
            # Every OTHER target on this concentrated host, at the reduced identity set.
            for t in items_sorted[1:]:
                for key in TIER_B_IDENTITY_KEYS:
                    if key not in ident_by_key:
                        continue
                    work.append({"kind": "probe_tier_b", "host": host, "url": t["start_url"],
                                 "identity": key,
                                 "entity_id": t.get("entity_id") or t.get("id"),
                                 "entity_name": t.get("name")})
    return work


def interleave(work: list, rng: random.Random) -> list:
    """Round-robin across hosts so no host sees back-to-back requests, with the host order
    shuffled so the run does not march through *.nyc.gov as a contiguous block."""
    buckets = defaultdict(list)
    for w in work:
        buckets[w["host"]].append(w)
    hosts = list(buckets)
    rng.shuffle(hosts)
    for h in hosts:
        rng.shuffle(buckets[h])
    out, exhausted = [], False
    while not exhausted:
        exhausted = True
        for h in hosts:
            if buckets[h]:
                out.append(buckets[h].pop())
                exhausted = False
    return out


# =========================================================================================
# AI-READABILITY RUNNER (docs/ai-readability/tests.md, catalog v0.1; plan.md §4.1–§4.5)
#
# `--tier core` / `--tier full` run the test catalog. The legacy tiers (a, b, all) above are
# unchanged in what they request. Both share the identities, the body classifier, the WAF
# fingerprint and the pacing constants. Parsers and verdicts live in ai_readability.py (pure,
# offline-testable); this section only fetches, paces, quarantines, and writes.
#
# PUSHBACK NEVER STOPS THE RUN (Noel, 2026-11-06; plan.md §4.3a)
#   A host that pushes back — 429, a timeout, a reset, a TLS or DNS failure (after the single
#   retry) — is QUARANTINED for the rest of the run: its remaining requests are not sent, its
#   tests come back `na`, and a row goes to review.jsonl. A 403/401 or a challenge page is DATA:
#   it is recorded (and listed in review.jsonl) but the host is not quarantined, because the other
#   identities' answers are the measurement.
#
# THE RUN STOPS ONLY ON OUR OWN BREAKAGE
#   - a crash (any uncaught exception, including disk full on a write) -> status "broke"
#   - SIGTERM / SIGINT                                                  -> status "broke"
#   - our network down. Designed so that dead municipal sites can never trigger it:
#       1. count CONSECUTIVE transport failures across all requests (any success resets it);
#       2. only when that streak is >= NET_DOWN_STREAK *and* spans >= NET_DOWN_MIN_HOSTS distinct
#          hosts do we suspect ourselves (one dead host cannot do it: it is quarantined after its
#          first failure, so it never contributes twice);
#       3. then fetch neutral, non-government reachability URLs. ANY HTTP response from ANY of
#          them (whatever the status) proves our network is up -> the streak resets, the failed
#          hosts stay quarantined, the run continues. Only if every check fails, twice,
#          NET_DOWN_RECHECK_S apart, is the run stopped as "broke" with a resume command.
#   - the request cap (--max-requests)                                  -> status "broke"
# =========================================================================================

CORE_IDENTITY_KEYS = ("research_betanyc", "googlebot", "gptbot", "oai_searchbot",
                      "chatgpt_user", "mechanism_probe_chrome")
RESEARCH_KEY = "research_betanyc"

READ_CAPS = {                 # bytes read per request kind; bodies beyond this are truncated
    "robots": 512_000,        # RFC 9309 §2.5: parse at least 500 KiB
    "llms": 256_000,
    "llms_full": 64_000,      # M5 is presence + size only
    "sitemap": 5_000_000,
    "home": 2_000_000,
    "page": 2_000_000,        # --deep-pages: a page is read like a homepage
    "notfound": 64_000,
    "http": 8_000,
    "md_accept": 256_000,
    "md_index": 256_000,
    "link": 8_000,
    "api_catalog": 256_000,   # D4
    "wp_rest": 2_000_000,     # D3: a WordPress index lists every route
    "portal_catalog": 2_000_000,       # D5 (a DCAT-US feed can be large; truncated is fine)
    "portal_catalog_ident": 64_000,    # D7 reads status and block page only
    "portal_lookup": 256_000,          # D6 Socrata Discovery lookup
    "dataset": 2_000_000,              # D6 dataset page, read like a homepage
}
KIND_TEST = {"robots": "W1", "llms": "M4", "sitemap": "M2", "home": "W4", "page": "W4", "notfound": "F4",
             "http": "I5", "llms_full": "M5", "md_accept": "R2", "md_index": "R2", "link": "F3",
             "api_catalog": "D4", "wp_rest": "D3", "portal_catalog": "D5", "portal_lookup": "D6",
             "dataset": "D6", "portal_catalog_ident": "D7"}

NET_DOWN_STREAK = 8            # consecutive transport failures before we suspect ourselves
NET_DOWN_MIN_HOSTS = 4         # ...spread over at least this many distinct hosts
NET_DOWN_RECHECK_S = 60.0      # second reachability attempt this long after the first fails
# Neutral reachability checks: large, non-government endpoints that exist to be polled for
# connectivity (Apple's and Google's captive-portal checks). We do NOT read their bodies; any
# HTTP response at all means our network is up. Override with --reachability-url.
REACHABILITY_URLS = ("https://captive.apple.com/hotspot-detect.html",
                     "https://www.gstatic.com/generate_204")
DEFAULT_MAX_REQUESTS = 30_000
MAX_REDIRECTS = 5

# bingbot comes from crawlers.csv like every vendor name (its first published label is the same
# legacy desktop string the 2026-11-06 run sent).
BINGBOT = next(i for i in IDENTITIES if i["key"] == "bingbot")


def tier_identities(tier: str, identities=None) -> list:
    """core: the six of tests.md; full: the research crawler first, then every other identity:
    the controls and every name crawlers.csv sends."""
    identities = IDENTITIES if identities is None else identities
    by_key = {i["key"]: i for i in identities}
    if tier == "core":
        return [by_key[k] for k in CORE_IDENTITY_KEYS]
    return [by_key[RESEARCH_KEY]] + [i for i in identities if i["key"] != RESEARCH_KEY]


def _transport_kind(exc: BaseException) -> str:
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ssl.SSLError) or isinstance(exc, ssl.CertificateError):
        return "tls"
    if isinstance(exc, socket.gaierror):
        return "dns"
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
                        http.client.RemoteDisconnected)):
        return "reset"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    return "other"


# A redirect whose Location names a bot manager's own challenge is a block, whatever the final
# page says. Markers are the vendor's own strings as seen in a live Location header (2026-11-10:
# edc.nyc sends unverified bots to a challenge URL carrying `ssk=botmanager_support@radware.com`).
BLOCK_REDIRECT_MARKERS = (
    ("botmanager_support@radware.com", "radware_botmanager"),
)


def redirect_url(base: str, location: str) -> str:
    """The URL a browser would request for this Location: resolved against `base`, ASCII tab
    and newline removed, leading/trailing C0 controls and spaces stripped, and any other C0
    control, space or DEL percent-encoded (WHATWG URL Standard). http.client refuses a URL that
    still holds them (InvalidURL), which used to read as a transport failure."""
    loc = location.strip("".join(map(chr, range(0x21))))
    loc = "".join(c for c in loc if c not in "\t\n\r")
    loc = "".join("%{:02X}".format(ord(c)) if ord(c) <= 0x20 or ord(c) == 0x7F else c for c in loc)
    return urllib.parse.urljoin(base, loc)


def block_redirect(redirects: list):
    """The class name when any hop's Location carries a BLOCK_REDIRECT_MARKERS string, else None."""
    for hop in redirects:
        low = (hop.get("location") or "").lower()
        for marker, name in BLOCK_REDIRECT_MARKERS:
            if marker in low:
                return name
    return None


def http_request(url: str, ua, method: str = "GET", accept: str = "*/*", follow: str = "any",
                 max_bytes: int = 120_000, timeout: float = REQUEST_TIMEOUT_S) -> dict:
    """One request with explicit redirect control (urllib follows everything, including off-host,
    which W1/M4 must not do). follow: 'any' | 'same_host' | 'none'.

    Returns the response dict described in ai_readability's docstring. Never raises for HTTP
    status or transport failure. Certificates are verified (default SSL context), so an invalid
    certificate is a transport error of kind 'tls'."""
    start_host = urllib.parse.urlparse(url).netloc.lower()
    redirects, peercert, current = [], None, url
    started = time.time()
    while True:
        u = urllib.parse.urlparse(current)
        path = (u.path or "/") + ("?" + u.query if u.query else "")
        headers = {"Accept": accept, "Connection": "close"}
        if ua:
            headers["User-Agent"] = ua
        try:
            if u.scheme == "https":
                conn = http.client.HTTPSConnection(u.netloc, timeout=timeout,
                                                   context=ssl.create_default_context())
            else:
                conn = http.client.HTTPConnection(u.netloc, timeout=timeout)
            try:
                conn.request(method, path, headers=headers)
                # Read the certificate now: getresponse() closes conn.sock when the server
                # answers "Connection: close" (http.client hands the socket to the response).
                if u.scheme == "https" and peercert is None and conn.sock is not None:
                    try:
                        peercert = conn.sock.getpeercert()
                    except Exception:
                        peercert = None
                resp = conn.getresponse()
                raw = resp.read(max_bytes) if method != "HEAD" else b""
                truncated = method != "HEAD" and len(raw) >= max_bytes
                hdrs = {}
                for k, v in resp.getheaders():
                    k = k.lower()
                    hdrs[k] = (hdrs[k] + ("\n" if k == "set-cookie" else ", ") + v) if k in hdrs else v
                status = resp.status
            finally:
                conn.close()
        except Exception as e:  # timeout, DNS, TLS, reset, protocol error
            blocked = block_redirect(redirects)
            if blocked:  # the shield answered; failing on its challenge URL is not a dead host
                return {"url": url, "final_url": current, "status": redirects[-1]["status"],
                        "headers": {}, "body": "", "raw": b"", "bytes": 0, "content_type": "",
                        "is_block_page": True, "body_class": blocked, "waf": [blocked],
                        "redirects": redirects, "peercert": peercert, "truncated": False,
                        "offhost_redirect": False, "elapsed_s": round(time.time() - started, 2),
                        "transport_error": None, "transport_kind": None}
            return {"url": url, "final_url": current, "status": None, "headers": {}, "body": "",
                    "raw": b"", "bytes": 0, "content_type": "", "is_block_page": None,
                    "body_class": None, "waf": [], "redirects": redirects, "peercert": peercert,
                    "elapsed_s": round(time.time() - started, 2), "truncated": False,
                    "transport_error": "{}: {}".format(type(e).__name__, str(e)[:200]),
                    "transport_kind": _transport_kind(e)}
        location = hdrs.get("location")
        if status in (301, 302, 303, 307, 308) and location and follow != "none" \
                and len(redirects) < MAX_REDIRECTS:
            nxt = redirect_url(current, location)
            nhost = urllib.parse.urlparse(nxt).netloc.lower()
            if follow == "any" or nhost == start_host:
                redirects.append({"status": status, "location": nxt})
                current = nxt
                if status == 303:
                    method = "GET" if method != "HEAD" else "HEAD"
                continue
            offhost = True
        else:
            offhost = False
        ctype = hdrs.get("content-type", "")
        m = re.search(r"charset=([\w.-]+)", ctype, re.I)
        try:
            body = raw.decode(m.group(1) if m else "utf-8", "replace")
        except LookupError:
            body = raw.decode("utf-8", "replace")
        cls, is_block = classify_body(body)
        if method == "HEAD":
            cls, is_block = "head", False
        blocked = block_redirect(redirects)
        if blocked:
            cls, is_block = blocked, True
        return {"url": url, "final_url": current, "status": status, "headers": hdrs, "body": body,
                "raw": raw, "bytes": len(raw), "content_type": ctype, "is_block_page": is_block,
                "body_class": cls, "waf": fingerprint_waf(hdrs, hdrs.get("set-cookie", ""))
                + ([blocked] if blocked else []),
                "redirects": redirects, "peercert": peercert, "truncated": truncated,
                "offhost_redirect": offhost, "elapsed_s": round(time.time() - started, 2),
                "transport_error": None, "transport_kind": None}


def reachability_ok(urls=REACHABILITY_URLS, transport=None) -> bool:
    """True if ANY neutral URL gives ANY HTTP response. Bodies are ignored on purpose: the only
    question is whether packets leave this machine and come back."""
    transport = transport or (lambda u: http_request(u, None, follow="none", max_bytes=1024,
                                                     timeout=15))
    for u in urls:
        if transport(u).get("status") is not None:
            return True
    return False


class NetworkMonitor:
    """Decides 'is it us?' — see the block comment at the top of this section."""

    def __init__(self, check=None, sleep=time.sleep, streak=NET_DOWN_STREAK,
                 min_hosts=NET_DOWN_MIN_HOSTS, recheck_s=NET_DOWN_RECHECK_S):
        self._check = check or reachability_ok
        self._sleep = sleep
        self.streak_limit, self.min_hosts, self.recheck_s = streak, min_hosts, recheck_s
        self.streak = 0
        self.streak_hosts = set()
        self.down = False
        self.reason = ""
        self.checks = []          # [{ts, streak, hosts, ok}]
        self._lock = threading.Lock()

    def record(self, host: str, transport_error: bool) -> None:
        with self._lock:
            if not transport_error:
                self.streak, self.streak_hosts = 0, set()
                return
            self.streak += 1
            if host:
                self.streak_hosts.add(host)
            if self.streak < self.streak_limit or len(self.streak_hosts) < self.min_hosts:
                return
            ok = self._check()
            if not ok:
                self._sleep(self.recheck_s)
                ok = self._check()
            self.checks.append({"ts": now_iso(), "streak": self.streak,
                                "hosts": len(self.streak_hosts), "ok": ok})
            if ok:
                self.streak, self.streak_hosts = 0, set()
            else:
                self.down = True
                self.reason = ("{} consecutive transport failures across {} hosts, and no neutral "
                               "reachability URL answered (twice, {:.0f}s apart): our network "
                               "appears to be down".format(self.streak, len(self.streak_hosts),
                                                           self.recheck_s))


class HostClock:
    """Shared across workers: never two in-flight requests to one host, and the per-host floor
    (max of MIN_HOST_DELAY_S and any HOST_GOVERNORS entry) between them."""

    def __init__(self, floor_s: float, governors: dict, monotonic=time.monotonic, sleep=time.sleep):
        self.floor_s, self.governors = floor_s, governors
        self._mono, self._sleep = monotonic, sleep
        self._last, self._busy = {}, set()
        self._cv = threading.Condition()

    def floor(self, host: str) -> float:
        return max(self.floor_s, self.governors.get(host, 0.0))

    def ready_at(self, host: str) -> float:
        with self._cv:
            return self._last.get(host, -1e18) + self.floor(host)

    def acquire(self, host: str) -> None:
        with self._cv:
            while host in self._busy:
                self._cv.wait(timeout=1.0)
            self._busy.add(host)
        delay = self.ready_at(host) - self._mono()
        if delay > 0:
            self._sleep(delay)

    def release(self, host: str) -> None:
        with self._cv:
            self._last[host] = self._mono()
            self._busy.discard(host)
            self._cv.notify_all()


class RunContext:
    """Everything shared by the workers of one run."""

    def __init__(self, run_id, tier, vantage, git_sha, identities, transport=None,
                 pace_s=GLOBAL_PACE_S, jitter_s=GLOBAL_JITTER_S, host_floor_s=MIN_HOST_DELAY_S,
                 governors=None, monitor=None, max_requests=DEFAULT_MAX_REQUESTS, portals=None,
                 dotgov=None, today=None, seed=20260828, sleep=time.sleep,
                 monotonic=time.monotonic, retry_backoff_s=RETRY_BACKOFF_S, max_attempts=2):
        self.run_id, self.tier, self.vantage, self.git_sha = run_id, tier, vantage, git_sha
        self.identities = identities
        self.ident_by_key = {i["key"]: i for i in identities}
        self.transport = transport or http_request
        self.pace_s, self.jitter_s = pace_s, jitter_s
        self.sleep, self.monotonic = sleep, monotonic
        self.retry_backoff_s = retry_backoff_s
        self.max_attempts = max_attempts   # 2 = the single retry on 5xx/transport; 1 = none
        self.robots_cache = {}             # --deep-pages: page host -> robots.txt response
        self.clock = HostClock(host_floor_s, HOST_GOVERNORS if governors is None else governors,
                               monotonic=monotonic, sleep=sleep)
        self.monitor = monitor or NetworkMonitor(sleep=sleep)
        self.max_requests = max_requests
        self.portals = portals or {"hosts": set(), "host_suffixes": (), "url_prefixes": ()}
        self.dotgov = dotgov
        self.today = today or _date.today()
        self.seed = seed
        self.lock = threading.Lock()
        self.requests = 0
        self.quarantined = {}          # host -> reason
        self.stop = threading.Event()
        self.broke_reason = ""

    def count_request(self) -> bool:
        with self.lock:
            if self.requests >= self.max_requests:
                if not self.broke_reason:
                    self.broke_reason = "request cap reached ({})".format(self.max_requests)
                self.stop.set()
                return False
            self.requests += 1
            return True

    def quarantine(self, host: str, reason: str) -> None:
        with self.lock:
            self.quarantined.setdefault(host, reason)

    def is_quarantined(self, host: str) -> bool:
        with self.lock:
            return host in self.quarantined


def _pushback(resp: dict):
    """(what happened, quarantine?) for a response that is pushback, else None."""
    if resp.get("status") is None and resp.get("transport_error"):
        return "transport {}: {}".format(resp.get("transport_kind") or "error",
                                         resp["transport_error"]), True
    if resp.get("status") == 429:
        return "HTTP 429", True
    if resp.get("status") in (401, 403):
        return "HTTP {} (data; host not quarantined)".format(resp["status"]), False
    if resp.get("is_block_page"):
        return "challenge/block page ({}) with HTTP {} (data; host not quarantined)".format(
            resp.get("body_class"), resp.get("status")), False
    return None


def host_plan(job: dict, ctx: RunContext):
    """Generator: yields request specs for one host, receives responses, returns its result
    rows (pushback rows are collected by perform()). Order: robots, llms, sitemap, homepage × identities (research first),
    404 probe, http:// check, then the full-tier extras."""
    host = job["host"]
    rows = []

    def req(kind, url, identity=RESEARCH_KEY, method="GET", accept="*/*", follow="any"):
        return {"kind": kind, "url": url, "identity": identity, "method": method,
                "accept": accept, "follow": follow}

    def row(test, identity, verdict_ev):
        verdict, ev = verdict_ev
        rows.append({"catalog_version": ar.CATALOG_VERSION, "git_sha": ctx.git_sha,
                     "run_id": ctx.run_id, "vantage": ctx.vantage, "tier": ctx.tier,
                     "place": getattr(ctx, "place", None),
                     "ts": now_iso(), "host": host, "entity_id": job.get("entity_id"),
                     "entity_name": job.get("entity_name"), "entity_ids": job.get("entity_ids"),
                     "test": test, "identity": identity, "verdict": verdict, "evidence": ev,
                     "evidence_sha256": ar.sha256(json.dumps(ev, sort_keys=True, default=str))})

    base = "https://{}".format(host)
    home_url = job["url"]
    home_path = urllib.parse.urlparse(home_url).path or "/"

    robots = yield req("robots", base + "/robots.txt", follow="same_host")
    llms = yield req("llms", base + "/llms.txt", follow="same_host")
    rbody = ar.robots_body(robots)
    declared = ar.sitemap_urls_from_robots(rbody) if rbody is not None else []
    sm_url, sm_source = (declared[0], "robots") if declared else (base + "/sitemap.xml", "default")
    sitemap = yield req("sitemap", sm_url)

    homes = {}
    for ident in ctx.identities:
        homes[ident["key"]] = yield req("home", home_url, identity=ident["key"])
    notfound = yield req("notfound", "{}/ai-readability-probe-{}-does-not-exist".format(
        base, ctx.run_id))
    http_check = yield req("http", "http://{}/".format(host), follow="none")

    research = homes.get(RESEARCH_KEY)
    page = None
    if research and research.get("status") == 200 and not ar.refused(research):
        page = ar.parse_page(research.get("body"))

    # ---- W
    row("W1", RESEARCH_KEY, ar.test_w1(robots))
    row("W2", RESEARCH_KEY, ar.test_w2(robots))
    w3 = ar.test_w3(robots, home_path)
    row("W3", RESEARCH_KEY, w3)
    w4 = {}
    for ident in ctx.identities:
        w4[ident["key"]] = ar.w4_identity_verdict(homes[ident["key"]])
        row("W4", ident["key"], w4[ident["key"]])
    row("W4", ar.W4_AGGREGATE_IDENTITY, ar.w4_aggregate(
        {k: v[0] for k, v in w4.items()}, {i["key"]: i["klass"] for i in ctx.identities}))
    row("W5", "*", ar.test_w5(w3[1] if w3[0] == "reported" else None, w4, home_path))
    row("W6", RESEARCH_KEY, ar.test_w6(robots))
    # ---- M
    row("M1", RESEARCH_KEY, ar.test_m1(robots))
    row("M2", RESEARCH_KEY, ar.test_m2(sitemap, sm_source))
    row("M3", RESEARCH_KEY, ar.test_m3(sitemap, ctx.today))
    row("M4", RESEARCH_KEY, ar.test_m4(llms))
    # ---- R, I, D, F, P (all read the one research-UA homepage body)
    row("R1", RESEARCH_KEY, ar.test_r1(research, page))
    row("R3", RESEARCH_KEY, ar.test_r3(research, page))
    row("I1", RESEARCH_KEY, ar.test_i1(research, page))
    row("I2", RESEARCH_KEY, ar.test_i2(research, page))
    i3 = ar.test_i3(research, page)
    row("I3", RESEARCH_KEY, i3)
    row("I4", RESEARCH_KEY, ar.test_i4(research, job.get("entity_name"), page,
                                       i3[1] if i3[0] in ("pass", "partial", "fail") else None))
    row("I5", RESEARCH_KEY, ar.test_i5(host, research, http_check, ctx.dotgov))
    row("D1", RESEARCH_KEY, ar.test_d1(research, ctx.portals, page))
    row("F1", RESEARCH_KEY, ar.test_f1(research, ctx.today, page))
    row("F2", RESEARCH_KEY, ar.test_f2(research))
    row("F4", RESEARCH_KEY, ar.test_f4(notfound))
    for pid, fn in (("P1", ar.test_p1), ("P2", ar.test_p2), ("P3", ar.test_p3), ("P4", ar.test_p4)):
        row(pid, RESEARCH_KEY, fn(research, page))
    # ---- catalog 0.2, zero requests: website software and data/API links (pass 1)
    c1 = ar.test_c1(research, page, job.get("gsa_cms"))
    row("C1", RESEARCH_KEY, c1)
    row("D2", RESEARCH_KEY, ar.test_d2(research, page, ctx.portals, llms))

    if ctx.tier == "full":
        llms_full = yield req("llms_full", base + "/llms-full.txt", follow="same_host")
        row("M5", RESEARCH_KEY, ar.test_m5(llms_full))
        md_accept = yield req("md_accept", home_url, accept="text/markdown")
        md_index = yield req("md_index", base + "/index.md", follow="same_host")
        row("R2", RESEARCH_KEY, ar.test_r2(md_accept, md_index, research, page))
        # ---- data endpoints, pass 2 (documented paths only; tests.md § Data endpoints)
        api_cat = yield req("api_catalog", base + ar.API_CATALOG_PATH,
                            accept=ar.LINKSET_TYPE + ", application/json;q=0.5")
        row("D4", RESEARCH_KEY, ar.test_d4(api_cat))
        if ar.is_wordpress(c1[1]):
            root = ar.wp_api_root(research, page) if page else None
            wp_url = root if root and urllib.parse.urlparse(root).netloc.lower() == host \
                else base + "/wp-json/"
            wp = yield req("wp_rest", wp_url, accept="application/json", follow="same_host")
            row("D3", RESEARCH_KEY, ar.test_d3(wp))
        else:
            row("D3", RESEARCH_KEY, ("na", {"reason": "homepage does not show WordPress"}))
        if "data portal" in (job.get("kinds") or []):
            yield from portal_plan(job, ctx, research, page, req, row)
        links = ar.f3_pick_links(research, page) if page else []
        results = []
        for link in links:
            r = yield req("link", link, method="HEAD", follow="same_host")
            if r.get("status") == 405:
                r = yield req("link", link, method="GET", follow="same_host")
            if r.get("quarantined"):
                break
            results.append({"url": link, "status": r.get("status"),
                            "transport_error": r.get("transport_error")})
        row("F3", RESEARCH_KEY, ar.test_f3(results) if page else ar.homepage_gate(research) or
            ("na", {"reason": "no homepage"}))
    return rows


def portal_plan(job: dict, ctx, research, page, req, row):
    """D5–D7 for an address of kind `data portal` (full tier): the platform's documented catalog
    (research identity), one dataset page from it, then the catalog once per other identity.
    Requests: 1 + 1 (+1 Socrata Discovery lookup) + (identities − 1). Nothing when the platform
    is not recognized: no path is tried that its vendor does not document."""
    host = job["host"]
    plat = ar.portal_platform(host, research, page, job.get("platform"))
    platform = plat["platform"]
    cat_url = ar.portal_catalog_url(platform, host) if platform else None
    if not cat_url:
        why = ("na", {"reason": "platform not recognized ({}); no documented catalog path to "
                                "try".format(plat["signal"])})
        row("D5", RESEARCH_KEY, why)
        row("D6", RESEARCH_KEY, why)
        row("D7", ar.W4_AGGREGATE_IDENTITY, why)
        return
    catalog = yield req("portal_catalog", cat_url, accept="application/json")
    verdict, ev = ar.test_d5(platform, catalog)
    row("D5", RESEARCH_KEY, (verdict, dict(ev, platform_signal=plat["signal"])))
    lookup = None
    lk = ar.dataset_lookup_url(platform, host) if verdict == "pass" else None
    if lk:
        lookup = yield req("portal_lookup", lk, accept="application/json")
    ds_url = ar.dataset_page_url(platform, host, catalog, lookup) if verdict == "pass" else None
    if ds_url:
        ds = yield req("dataset", ds_url)
        ds_page = ar.parse_page(ds.get("body")) if ds.get("status") == 200 and not ar.refused(ds) \
            else None
        row("D6", RESEARCH_KEY, ar.test_d6(ds_url, ds, ds_page))
    else:
        row("D6", RESEARCH_KEY, ar.test_d6(None, None))
    per = {RESEARCH_KEY: catalog}
    for ident in ctx.identities:
        if ident["key"] != RESEARCH_KEY:
            per[ident["key"]] = yield req("portal_catalog_ident", cat_url, identity=ident["key"],
                                          accept="application/json")
    verdicts, agg = ar.test_d7(per, {i["key"]: i["klass"] for i in ctx.identities})
    for key, ve in verdicts.items():
        row("D7", key, ve)
    row("D7", ar.W4_AGGREGATE_IDENTITY, agg)


# Tests a deep page gets (issue #35). The host-level files (llms.txt, sitemap, the 404 probe,
# http://) and the tests that read them belong to the host run and are not repeated per page.
PAGE_TESTS = ("W3", "W4", "R1", "R3", "I1", "I2", "I3", "I4", "F1", "F2", "D1",
              "P1", "P2", "P3", "P4")


def page_identities(identities=None) -> list:
    """--deep-pages: the Tier B set. A page asks whether it differs from its host; an honest
    baseline, one honest AI agent and the mechanism probe answer that."""
    by_key = {i["key"]: i for i in (IDENTITIES if identities is None else identities)}
    return [by_key[k] for k in TIER_B_IDENTITY_KEYS]


def _i4_for_names(research, names, page, i3_ev):
    """I4 against every name the sources give the page (one page can carry two bodies). The
    first name's strings are kept; `per_name` records the agreement for each."""
    if len(names) <= 1:
        return ar.test_i4(research, names[0] if names else None, page, i3_ev)
    results = [(n, ar.test_i4(research, n, page, i3_ev)) for n in names]
    verdict, ev = results[0][1]
    if verdict != "reported":
        return verdict, ev
    ev = dict(ev, per_name={n: r[1].get("names_agree") for n, r in results})
    ev["names_agree"] = any(ev["per_name"].values())
    return verdict, ev


def page_plan(job: dict, ctx):
    """Generator for one deep page (--deep-pages): robots.txt of the page's own host, fetched
    once per run and shared by every page on it (ctx.robots_cache), then the page once per
    identity. Emits only page-level rows (PAGE_TESTS), each with `page_url` and scope "page".
    Every request is paced on job["host"] (`pace_host`), so `nyc.gov` and `www.nyc.gov` pages
    share one clock and one governor."""
    host, page_url = job["host"], job["url"]
    pu = urllib.parse.urlparse(page_url)
    page_host = (job.get("page_host") or pu.netloc).lower()
    page_path = (pu.path or "/") + ("?" + pu.query if pu.query else "")
    rows = []

    def req(kind, url, identity=RESEARCH_KEY, follow="any"):
        return {"kind": kind, "url": url, "identity": identity, "method": "GET",
                "accept": "*/*", "follow": follow, "pace_host": host}

    def row(test, identity, verdict_ev):
        verdict, ev = verdict_ev
        rows.append({"catalog_version": ar.CATALOG_VERSION, "git_sha": ctx.git_sha,
                     "run_id": ctx.run_id, "vantage": ctx.vantage, "tier": ctx.tier,
                     "place": getattr(ctx, "place", None),
                     "ts": now_iso(), "host": host, "scope": "page", "page_url": page_url,
                     "page_host": page_host, "entity_id": job.get("entity_id"),
                     "entity_name": job.get("entity_name"), "entity_ids": job.get("entity_ids"),
                     "source": job.get("source"),
                     "test": test, "identity": identity, "verdict": verdict, "evidence": ev,
                     "evidence_sha256": ar.sha256(json.dumps(ev, sort_keys=True, default=str))})

    cache = ctx.robots_cache
    if page_host in cache:
        robots = cache[page_host]
    else:
        robots = yield req("robots", "https://{}/robots.txt".format(page_host), follow="same_host")
        cache[page_host] = robots
    pages = {}
    for ident in ctx.identities:
        pages[ident["key"]] = yield req("page", page_url, identity=ident["key"])

    research = pages.get(RESEARCH_KEY)
    page = None
    if research and research.get("status") == 200 and not ar.refused(research):
        page = ar.parse_page(research.get("body"))

    row("W3", RESEARCH_KEY, ar.test_w3(robots, page_path))
    w4 = {}
    for ident in ctx.identities:
        w4[ident["key"]] = ar.w4_identity_verdict(pages[ident["key"]])
        row("W4", ident["key"], w4[ident["key"]])
    row("W4", ar.W4_AGGREGATE_IDENTITY, ar.w4_aggregate(
        {k: v[0] for k, v in w4.items()}, {i["key"]: i["klass"] for i in ctx.identities}))
    row("R1", RESEARCH_KEY, ar.test_r1(research, page))
    row("R3", RESEARCH_KEY, ar.test_r3(research, page))
    row("I1", RESEARCH_KEY, ar.test_i1(research, page))
    row("I2", RESEARCH_KEY, ar.test_i2(research, page))
    i3 = ar.test_i3(research, page)
    row("I3", RESEARCH_KEY, i3)
    row("I4", RESEARCH_KEY, _i4_for_names(
        research, job.get("entity_names") or ([job["entity_name"]] if job.get("entity_name") else []),
        page, i3[1] if i3[0] in ("pass", "partial", "fail") else None))
    row("F1", RESEARCH_KEY, ar.test_f1(research, ctx.today, page))
    row("F2", RESEARCH_KEY, ar.test_f2(research))
    row("D1", RESEARCH_KEY, ar.test_d1(research, ctx.portals, page))
    for pid, fn in (("P1", ar.test_p1), ("P2", ar.test_p2), ("P3", ar.test_p3), ("P4", ar.test_p4)):
        row(pid, RESEARCH_KEY, fn(research, page))
    return rows


def plan_for(job: dict):
    """The request plan for a job: page_plan for a --deep-pages job, host_plan otherwise."""
    return page_plan if job.get("page_url") else host_plan


def job_unit(job_or_row: dict) -> str:
    """Resume key: the page URL for a deep page (job or row), else the host."""
    return job_or_row.get("page_url") or job_or_row.get("host")


def clock_host(spec: dict) -> str:
    """The host whose clock paces a request: the plan's `pace_host`, else the URL's host."""
    return spec.get("pace_host") or urllib.parse.urlparse(spec["url"]).netloc.lower()


def page_jobs(targets: list) -> list:
    """deep_page_targets() rows as runner jobs, sorted by host then URL."""
    jobs = []
    for t in targets:
        url = t["url"]
        p = urllib.parse.urlparse(url)
        if p.scheme != "https":
            url = urllib.parse.urlunparse(p._replace(scheme="https"))
        jobs.append({"host": t["host"], "url": url, "page_url": url,
                     "page_host": t.get("page_host") or p.netloc.lower(),
                     "entity_id": t.get("entity_id"), "entity_ids": t.get("entity_ids") or [],
                     "entity_name": t.get("entity_name"), "entity_names": t.get("entity_names") or [],
                     "source": t.get("source")})
    return sorted(jobs, key=lambda j: (j["host"], j["url"]))


def load_deep_page_targets(websites_file=None, extra_csv=None) -> list:
    """--deep-pages targets: websites.json (or --targets-file in that layout) plus the extra
    page list, paired with owners by ai_readability_websites.page_owners()."""
    import ai_readability_websites as aw
    path = pathlib.Path(websites_file) if websites_file else aw.OUT_JSON
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not (isinstance(rows, list) and rows and isinstance(rows[0], dict)
            and "ny_page_urls" in rows[0]):
        raise ValueError("--deep-pages needs a websites.json-layout file (rows with "
                         "`ny_page_urls`): {}".format(path))
    return aw.deep_page_targets(rows, aw.page_owners(),
                                aw.DEEP_PAGES_EXTRA if extra_csv is None else extra_csv)


def page_request_estimate(jobs: list, n_identities: int, floor_s: float, governors: dict) -> dict:
    """Requests a --deep-pages run sends (before retries): one robots.txt per distinct page host
    plus pages × identities. `min_hours` is the floor set by the busiest host, whose requests
    are serialized at its per-host floor whatever the worker count."""
    per_host = defaultdict(int)
    robots_hosts = set()
    for j in jobs:
        per_host[j["host"]] += n_identities
        if j["page_host"] not in robots_hosts:
            robots_hosts.add(j["page_host"])
            per_host[j["host"]] += 1
    busiest = max(per_host, key=per_host.get) if per_host else None
    floor = max(floor_s, governors.get(busiest, 0.0)) if busiest else 0.0
    return {"pages": len(jobs), "hosts": len(per_host), "robots": len(robots_hosts),
            "requests": sum(per_host.values()), "per_host": dict(per_host), "busiest": busiest,
            "min_hours": round(max(0, per_host.get(busiest, 0) - 1) * floor / 3600, 1)}


QUARANTINED_RESP = {"status": None, "headers": {}, "body": "", "raw": b"", "bytes": 0,
                    "is_block_page": None, "body_class": None, "waf": [], "redirects": [],
                    "transport_error": None, "quarantined": True}


def perform(spec: dict, job: dict, ctx: RunContext, review: list, last_global: list) -> dict:
    """Send one request (paced, quarantine-aware, single retry on 5xx/transport), and log
    pushback to `review`. Returns a response dict (synthetic when the host is quarantined)."""
    host = job["host"]
    req_host = urllib.parse.urlparse(spec["url"]).netloc.lower()
    pace_host = clock_host(spec)
    if ctx.is_quarantined(host) or ctx.stop.is_set():
        return dict(QUARANTINED_RESP, url=spec["url"], final_url=spec["url"])
    ident = ctx.ident_by_key.get(spec["identity"]) or {"ua": None}
    attempt = 0
    while True:
        attempt += 1
        if not ctx.count_request():
            return dict(QUARANTINED_RESP, url=spec["url"], final_url=spec["url"])
        # global pace for this worker, then the shared per-host floor
        gap = ctx.pace_s + (random.uniform(-ctx.jitter_s, ctx.jitter_s) if ctx.jitter_s else 0)
        wait = last_global[0] + gap - ctx.monotonic()
        if wait > 0:
            ctx.sleep(wait)
        ctx.clock.acquire(pace_host)
        try:
            resp = ctx.transport(spec["url"], ident["ua"], method=spec["method"],
                                 accept=spec["accept"], follow=spec["follow"],
                                 max_bytes=READ_CAPS.get(spec["kind"], 120_000))
        finally:
            ctx.clock.release(pace_host)
            last_global[0] = ctx.monotonic()
        transport_fail = resp.get("status") is None and bool(resp.get("transport_error"))
        ctx.monitor.record(req_host, transport_fail)
        if ctx.monitor.down:
            ctx.broke_reason = ctx.broke_reason or ctx.monitor.reason
            ctx.stop.set()
        retryable = transport_fail or (resp.get("status") or 0) >= 500
        if retryable and attempt < ctx.max_attempts and not ctx.stop.is_set():
            ctx.sleep(ctx.retry_backoff_s)
            continue
        break
    pb = _pushback(resp)
    if pb:
        what, quarantine = pb
        on_own_host = req_host == host or spec.get("pace_host") == host
        if quarantine and on_own_host:
            ctx.quarantine(host, "{} on {} ({})".format(what, KIND_TEST.get(spec["kind"]),
                                                        spec["identity"]))
        rec = {"run_id": ctx.run_id, "ts": now_iso(), "host": host,
               "entity_id": job.get("entity_id"), "test": KIND_TEST.get(spec["kind"]),
               "kind": spec["kind"], "identity": spec["identity"], "url": spec["url"],
               "what": what, "status": resp.get("status"),
               "action": "quarantined" if (quarantine and on_own_host) else "recorded"}
        if job.get("page_url"):
            rec.update(scope="page", page_url=job["page_url"])
        review.append(rec)
    return resp


def plan_targets(targets: list) -> list:
    """Group targets by host; one job per host. targets: [{entity_id, entity_name, url}]."""
    by_host = defaultdict(list)
    for t in targets:
        url = t.get("url")
        if not url:
            continue
        host = urllib.parse.urlparse(url).netloc.lower()
        if host:
            by_host[host].append(t)
    jobs = []
    # Which address stands for a host (addresses.csv): a data portal or main site before a
    # sub-site, section or API, so a host's homepage is read from its own front door. A host
    # carrying only sections is still read at the first one. Rows without `kind` sort as before.
    kind_rank = {"data portal": 0, "main site": 1, "sub-site": 2, "API": 3, "section": 4}
    for host, items in sorted(by_host.items()):
        items = sorted(items, key=lambda x: (kind_rank.get(x.get("kind"), 0),
                                             x.get("entity_id") is None, x.get("entity_id") or ""))
        rep = items[0]
        url = rep["url"]
        if urllib.parse.urlparse(url).scheme != "https":
            url = "https://{}{}".format(host, urllib.parse.urlparse(url).path or "/")
        job = {"host": host, "url": url, "entity_id": rep.get("entity_id"),
               "entity_name": rep.get("entity_name"),
               "entity_ids": [i.get("entity_id") for i in items]}
        kinds = sorted({i["kind"] for i in items if i.get("kind")})
        if kinds:
            job["kinds"] = kinds
        for field in ("platform", "gsa_cms"):
            val = next((i[field] for i in items if i.get(field)), None)
            if val:
                job[field] = val
        jobs.append(job)
    return jobs


def website_row_as_target(row: dict) -> dict:
    """A row of data/ai-readability/websites.json (GSA target-list layout) as a probe target.
    entity_id is set only when exactly one registry body lists the host."""
    ids = row.get("ny_entity_ids") or []
    names = row.get("ny_names") or []
    return {"entity_id": ids[0] if len(ids) == 1 else None,
            "entity_name": row.get("agency") or (names[0] if names else None),
            "url": row.get("initial_url")}


ADDRESSES = ROOT / "data" / "ai-readability" / "addresses.csv"
WEBSITES = ROOT / "data" / "ai-readability" / "websites.json"
ADDRESS_KINDS = ("main site", "sub-site", "section", "data portal", "API")
# addresses.csv headers, normalized (lower case, spaces and hyphens to underscores) -> our field.
ADDRESS_FIELDS = {"address": "address", "owning_body": "owning_body", "kind": "kind",
                  "level": "level", "entity_type": "level", "level_or_entity_type": "level",
                  "source": "source", "date_found": "date_found", "notes": "notes",
                  "entity_id": "entity_id", "platform": "platform", "cms": "cms"}


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def load_addresses(path) -> list:
    """data/ai-readability/addresses.csv (issue #41) as probe targets. Columns: address, owning
    body, kind (main site | sub-site | section | data portal | API), level or entity_type,
    source, date found, notes. Optional: entity_id, platform (a data portal's software, if the
    list knows it), cms (GSA Site Scanning's value, kept as is by C1). An address with no scheme
    is read as https. Raises on an unknown kind or a missing address: the list is checked, not
    guessed around."""
    import csv
    out = []
    with pathlib.Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = {h: ADDRESS_FIELDS.get(_norm_header(h)) for h in reader.fieldnames or []}
        if "address" not in cols.values() or "kind" not in cols.values():
            raise ValueError("{}: needs `address` and `kind` columns, has {}".format(
                path, reader.fieldnames))
        for n, raw in enumerate(reader, start=2):
            r = {cols[k]: (v or "").strip() for k, v in raw.items() if cols.get(k)}
            if not r.get("address"):
                raise ValueError("{} line {}: empty address".format(path, n))
            if r.get("kind") not in ADDRESS_KINDS:
                raise ValueError("{} line {}: kind {!r} is not one of {}".format(
                    path, n, r.get("kind"), ", ".join(ADDRESS_KINDS)))
            url = r["address"] if "://" in r["address"] else "https://" + r["address"]
            out.append({"entity_id": r.get("entity_id") or None,
                        "entity_name": r.get("owning_body") or None, "url": url,
                        "kind": r["kind"], "level": r.get("level") or None,
                        "platform": r.get("platform") or None, "gsa_cms": r.get("cms") or None,
                        "source": r.get("source") or None})
    return out


def default_targets_file() -> "pathlib.Path":
    """--tier core|full with no --targets-file: the address inventory when it exists, else
    websites.json (until issue #41's addresses.csv lands)."""
    return ADDRESSES if ADDRESSES.exists() else WEBSITES


def load_targets(targets_file) -> list:
    """--targets-file: addresses.csv (any .csv, issue #41 layout), a JSON list of {entity_id,
    entity_name, url} (entity_id may be null), or {"targets": [...]}, or
    data/ai-readability/websites.json (rows with `initial_url`; rows with `ny_host_issue` are
    skipped). Without it: data/crawl_targets.json, mapped to the same shape (legacy tiers)."""
    if targets_file and str(targets_file).lower().endswith(".csv"):
        return load_addresses(targets_file)
    if targets_file:
        raw = json.loads(pathlib.Path(targets_file).read_text())
        items = raw.get("targets", raw) if isinstance(raw, dict) else raw
        out = []
        for t in items:
            if isinstance(t, dict) and "initial_url" in t:      # data/ai-readability/websites.json
                if t.get("ny_host_issue"):                       # not the body's site any more
                    continue
                t = website_row_as_target(t)
            if not isinstance(t, dict) or not t.get("url"):
                raise ValueError("every target needs a 'url': {!r}".format(t))
            out.append({"entity_id": t.get("entity_id"), "entity_name": t.get("entity_name"),
                        "url": t["url"], **{k: t[k] for k in ("kind", "platform", "gsa_cms")
                                            if t.get(k)}})
        return out
    raw = json.loads(TARGETS.read_text())
    items = raw.get("targets", raw) if isinstance(raw, dict) else raw
    return [{"entity_id": t.get("entity_id") or t.get("id"), "entity_name": t.get("name"),
             "url": t.get("start_url")} for t in items if t.get("start_url")]


def requests_per_host(tier: str, n_identities: int, job: dict = None) -> "tuple[int, int]":
    """(min, max) requests one host costs, before retries. core: robots, llms, sitemap,
    homepage × identities, 404 probe, http:// check (C1 and D2 cost nothing). full adds
    llms-full, two markdown probes, the RFC 9727 api-catalog (D4), the WordPress REST index when
    the homepage shows WordPress (D3, 0–1), up to 10 link checks (HEAD, GET on 405), and, for a
    `data portal` address, the portal tests (D5–D7): catalog, a Socrata Discovery lookup (0–1),
    one dataset page, and the catalog once per other identity — or nothing when the platform is
    not recognized."""
    core = 5 + n_identities
    if tier == "core":
        return core, core
    lo, hi = core + 3 + 1, core + 3 + 1 + 1 + 20
    if job and "data portal" in (job.get("kinds") or []):
        hi += 1 + 1 + 1 + (n_identities - 1)
    return lo, hi


EST_RESPONSE_S = 1.0   # assumed mean time a request takes, added to the pace (an estimate input)


def run_estimate(jobs: list, tier: str, n_identities: int, workers: int = 1,
                 pace_s: float = GLOBAL_PACE_S, floor_s: float = MIN_HOST_DELAY_S,
                 governors: dict = None) -> dict:
    """Requests and wall-clock hours for a host run, given the pacing. Each worker sends one
    request per (pace + response time), so the run takes requests × that ÷ workers; but one
    host's requests are serialized at its floor (≥ 30 s, or its governor if higher), so the
    busiest host sets a lower bound. Hours are the larger of the two, for the min and max
    request counts. Retries and quarantines are not modeled."""
    governors = HOST_GOVERNORS if governors is None else governors
    lo = hi = 0
    busiest, busiest_hi = None, 0
    for j in jobs:
        a, b = requests_per_host(tier, n_identities, j)
        lo, hi = lo + a, hi + b
        if b > busiest_hi:
            busiest, busiest_hi = j["host"], b
    per_req = pace_s + EST_RESPONSE_S
    workers = max(1, workers)

    def hours(n_req, host_max):
        floor = max(floor_s, governors.get(busiest, 0.0)) if busiest else floor_s
        return round(max(n_req * per_req / workers, max(0, host_max - 1) * floor) / 3600, 1)
    return {"hosts": len(jobs), "requests_min": lo, "requests_max": hi,
            "hours_min": hours(lo, requests_per_host(tier, n_identities,
                                                     next((j for j in jobs if j["host"] == busiest),
                                                          None))[0] if busiest else 0),
            "hours_max": hours(hi, busiest_hi), "busiest": busiest,
            "busiest_requests_max": busiest_hi, "workers": workers,
            "seconds_per_request": per_req,
            "portals": sum(1 for j in jobs if "data portal" in (j.get("kinds") or []))}


def run_worker(jobs: list, ctx: RunContext, writer, window: int = 8) -> None:
    """Round-robin over a window of hosts: always send the request whose host floor frees up
    first, so one host never sees back-to-back requests and the global pace stays busy."""
    rng = random.Random(ctx.seed)
    queue = list(jobs)
    rng.shuffle(queue)
    active = []          # [job, gen, pending_spec, review]
    last_global = [-1e18]

    def admit():
        # At most one active job per host: a host's requests are serialized by its clock anyway,
        # so a second job on it would only crowd other hosts out of the window. (In --deep-pages
        # it also lets the first page on a host fetch robots.txt before the next page asks.)
        while len(active) < window:
            busy = {a[0]["host"] for a in active}
            nxt = next((j for j in queue if j["host"] not in busy), None)
            if nxt is None:
                return
            queue.remove(nxt)
            gen = plan_for(nxt)(nxt, ctx)
            spec = next(gen)
            active.append([nxt, gen, spec, []])

    admit()
    while active and not ctx.stop.is_set():
        # the host whose floor frees soonest
        active.sort(key=lambda a: ctx.clock.ready_at(clock_host(a[2])))
        slot = active[0]
        job, gen, spec, review = slot
        resp = perform(spec, job, ctx, review, last_global)
        try:
            slot[2] = gen.send(resp)
        except StopIteration as fin:
            rows = fin.value
            if not ctx.stop.is_set():
                writer(job, rows, review)
            active.remove(slot)
            admit()


class RunWriter:
    """Appends a finished host's rows atomically-enough (one write per file per host), then marks
    the host done in progress.jsonl. A crash mid-host loses only that host, which resume redoes."""

    def __init__(self, run_dir: pathlib.Path, ctx: RunContext):
        self.dir, self.ctx = run_dir, ctx
        self.lock = threading.Lock()
        self.rows_written = 0
        self.review_written = 0
        self.hosts_done = 0

    def __call__(self, job, rows, review):
        with self.lock:
            with (self.dir / "results.jsonl").open("a") as f:
                f.write("".join(json.dumps(r, default=str) + "\n" for r in rows))
            if review:
                with (self.dir / "review.jsonl").open("a") as f:
                    f.write("".join(json.dumps(r, default=str) + "\n" for r in review))
            with (self.dir / "progress.jsonl").open("a") as f:
                f.write(json.dumps({"host": job["host"], "unit": job_unit(job), "ts": now_iso(),
                                    "requests_so_far": self.ctx.requests}) + "\n")
            self.rows_written += len(rows)
            self.review_written += len(review)
            self.hosts_done += 1


def read_jsonl(path: pathlib.Path) -> list:
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue          # a torn last line from a crash
    return out


def prepare_resume(run_dir: pathlib.Path) -> "tuple[set, int]":
    """Units (hosts; pages in --deep-pages) already finished, and requests already spent. Rows
    for a unit that was mid-flight at the crash are dropped, so the resumed run writes each unit
    exactly once."""
    progress = read_jsonl(run_dir / "progress.jsonl")
    done = {p.get("unit") or p["host"] for p in progress}
    spent = max([p.get("requests_so_far", 0) for p in progress] + [0])
    for name in ("results.jsonl", "review.jsonl"):
        path = run_dir / name
        if path.exists():
            keep = [r for r in read_jsonl(path) if job_unit(r) in done]
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("".join(json.dumps(r, default=str) + "\n" for r in keep))
            os.replace(tmp, path)
    return done, spent


def git_sha() -> str:
    try:
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "scripts"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return sha + ("-dirty" if dirty else "") if sha else "unknown"
    except Exception:
        return "unknown"


def write_status(run_dir: pathlib.Path, status: dict) -> pathlib.Path:
    path = run_dir / "status.json"
    tmp = run_dir / "status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2))
    os.replace(tmp, path)
    return path


def run_on_exit(cmd: str, status_path: pathlib.Path) -> None:
    """--on-exit: run the operator's command with the status file path as its last argument."""
    if not cmd:
        return
    try:
        subprocess.run("{} {}".format(cmd, shlex.quote(str(status_path))), shell=True, timeout=120)
    except Exception as e:
        print("on-exit hook failed: {}".format(e), file=sys.stderr)


def split_by_host(jobs: list, n: int) -> list:
    """Worker groups for --deep-pages: every page of a host goes to the same worker, so one
    host's robots.txt is fetched once and its pages never wait on another worker's clock."""
    by_host = defaultdict(list)
    for j in jobs:
        by_host[j["host"]].append(j)
    groups = [[] for _ in range(n)]
    for host in sorted(by_host, key=lambda h: -len(by_host[h])):
        min(groups, key=len).extend(by_host[host])
    return groups


VPN_MAX_HOSTS = 25   # --vantage vpn: a small named set only (issue #44), never a full run


def vpn_guard(args, jobs: list) -> list:
    """Reasons to refuse a VPN run. VPN is a disclosed third vantage used only to ask "is this
    block about our network?" for a few named hosts: never a full run, never a way to retry
    until we get in."""
    if (getattr(args, "vantage", "") or "").strip().lower() != "vpn":
        return []
    if not args.only_hosts:
        return ["--vantage vpn needs --only-hosts (a small named set); a full run over a VPN "
                "is refused"]
    if len(jobs) > VPN_MAX_HOSTS:
        return ["--vantage vpn is limited to {} hosts; --only-hosts matched {}".format(
            VPN_MAX_HOSTS, len(jobs))]
    if not jobs:
        return ["--vantage vpn: --only-hosts matched no address"]
    return []


def pre_run_guard(identities: list, rows: list, today) -> list:
    """The pre-run guard (issue #42/#44): crawlers.csv must pass its own checks, every label we
    send must be in it (controls excepted), and no row may be checked more than 30 days ago."""
    return crawler_list.guard(rows, identities, today, controls=CONTROL_KEYS)


def run_readability(args, transport=None, monitor=None, sleep=time.sleep,
                    monotonic=time.monotonic, pacing=None, today=None, netlookup=None) -> int:
    """Entry point for --tier core|full and --deep-pages. Returns 0 (done), 1 (broke) or 2
    (refused before starting: pre-run guard or VPN guard)."""
    import network_record
    today = today or _date.today()
    deep = bool(getattr(args, "deep_pages", False))
    crawlers_path = getattr(args, "crawlers", None)
    if crawlers_path:
        crawler_rows, vendor, not_sent = load_crawler_identities(crawlers_path)
        all_idents = [_CTRL["baseline_default"], _CTRL["research_betanyc"]] + vendor + \
            [_CTRL["mechanism_probe_chrome"]]
    else:
        crawler_rows, not_sent, all_idents = CRAWLER_ROWS, NOT_SENT, IDENTITIES
    if deep:
        args.tier = "pages"
        jobs = page_jobs(load_deep_page_targets(args.targets_file, args.deep_pages_extra))
    else:
        if not args.targets_file:
            args.targets_file = str(default_targets_file())
        jobs = plan_targets(load_targets(args.targets_file))
    if args.only_hosts:
        only = ({h.strip().lower() for h in pathlib.Path(args.only_hosts[1:]).read_text().splitlines()
                 if h.strip()} if args.only_hosts.startswith("@") else
                {h.strip().lower() for h in args.only_hosts.split(",") if h.strip()})
        jobs = [j for j in jobs if j["host"] in only]
    if args.limit:
        jobs = jobs[:args.limit]
    identities = page_identities(all_idents) if deep else tier_identities(args.tier, all_idents)
    refusals = pre_run_guard(identities, crawler_rows, today) + vpn_guard(args, jobs)
    if refusals:
        print("REFUSED: the run did not start.", file=sys.stderr)
        for r in refusals:
            print("  - " + r, file=sys.stderr)
        return 2
    if deep:
        floor_s = (pacing or {}).get("host_floor_s", MIN_HOST_DELAY_S)
        governors = (pacing or {}).get("governors", HOST_GOVERNORS)
        est = page_request_estimate(jobs, len(identities), floor_s, governors)
        lo = hi = est["requests"]
        if args.dry_run:
            print("deep pages  pages={}  hosts={}  identities={}  requests≈{} (robots.txt × {} "
                  "page hosts + pages × {} identities, before retries)".format(
                      est["pages"], est["hosts"], len(identities), est["requests"], est["robots"],
                      len(identities)))
            print("identities: " + ", ".join(i["key"] for i in identities))
            print("busiest host: {} ({} requests, ≥ {} h at its per-host floor)".format(
                est["busiest"], est["per_host"].get(est["busiest"], 0), est["min_hours"]))
            for h in sorted(est["per_host"], key=lambda h: -est["per_host"][h]):
                print("  {:<28} {:>4} requests".format(h, est["per_host"][h]))
            unpaired = [j["url"] for j in jobs if not j.get("entity_name")]
            print("pages with no owner name: {}".format(len(unpaired)))
            for u in unpaired:
                print("  " + u)
            for j in jobs[:args.dry_run_show]:
                print("\n{}  ({})".format(j["url"], j.get("entity_name")))
                print("  GET https://{}/robots.txt   [research; once per run for this host]".format(
                    j["page_host"]))
                for i in identities:
                    print("  GET {}  [{}]".format(j["url"], i["key"]))
            if len(jobs) > args.dry_run_show:
                print("\n... {} more pages".format(len(jobs) - args.dry_run_show))
            print("\nno network used (dry run)")
            return 0
    else:
        pc = pacing or {}
        est = run_estimate(jobs, args.tier, len(identities), args.max_workers,
                           pc.get("pace_s", GLOBAL_PACE_S), pc.get("host_floor_s", MIN_HOST_DELAY_S),
                           pc.get("governors", HOST_GOVERNORS))
        lo, hi = est["requests_min"], est["requests_max"]

    if args.dry_run:
        print("tier={}  hosts={}  identities={}  requests≈{}–{} (before retries)".format(
            args.tier, len(jobs), len(identities), lo, hi))
        print("estimated wall clock: {}–{} h with {} worker(s) at ≈{:.0f} s per request "
              "(pace {:.0f} s + ≈{:.0f} s response); busiest host {} ({} requests, ≥ 30 s apart)"
              .format(est["hours_min"], est["hours_max"], est["workers"],
                      est["seconds_per_request"], est["seconds_per_request"] - EST_RESPONSE_S,
                      EST_RESPONSE_S, est["busiest"], est["busiest_requests_max"]))
        print("data portals (D5–D7): {}  ·  targets: {}".format(est["portals"], args.targets_file))
        print("network record: up to 9 requests to stat.ripe.net at the start and 9 at the end (IPv4 and IPv6 each)")
        print("identities: " + ", ".join(i["key"] for i in identities))
        if not_sent:
            print("read in robots.txt only, not sent: " + ", ".join(n["name"] for n in not_sent))
        for j in jobs[:args.dry_run_show]:
            print("\n{}  (entity {})".format(j["host"], j["entity_id"]))
            print("  GET https://{}/robots.txt            [research]".format(j["host"]))
            print("  GET https://{}/llms.txt              [research]".format(j["host"]))
            print("  GET <first Sitemap: in robots.txt, else https://{}/sitemap.xml>".format(j["host"]))
            for i in identities:
                print("  GET {}  [{}]".format(j["url"], i["key"]))
            print("  GET https://{}/ai-readability-probe-<run-id>-does-not-exist".format(j["host"]))
            print("  GET http://{}/  (no redirect followed)".format(j["host"]))
            if args.tier == "full":
                print("  GET /llms-full.txt · GET / Accept: text/markdown · GET /index.md · "
                      "≤10 HEAD same-host links")
                print("  GET /.well-known/api-catalog (D4) · GET the WordPress REST root if the "
                      "homepage shows WordPress (D3)")
                if "data portal" in (j.get("kinds") or []):
                    print("  data portal: the platform's documented catalog, one dataset page, "
                          "and the catalog once per identity (D5–D7)")
        if len(jobs) > args.dry_run_show:
            print("\n... {} more hosts".format(len(jobs) - args.dry_run_show))
        print("\nno network used (dry run)")
        return 0

    started_local = datetime.now()
    if args.run_dir:
        run_dir = pathlib.Path(args.run_dir)
    else:
        run_dir = pathlib.Path(args.out_root) / "{}-{}".format(
            started_local.strftime("%Y-%m-%d"), args.vantage)
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_dir.name
    done, spent = prepare_resume(run_dir)
    pending = [j for j in jobs if job_unit(j) not in done]

    portals = ar.load_portals(args.portals) if args.portals and pathlib.Path(args.portals).exists() \
        else None
    dotgov = None
    if args.dotgov_file:
        dotgov = {ln.strip().lower().split(",")[0] for ln in
                  pathlib.Path(args.dotgov_file).read_text().splitlines() if ln.strip()}
    pacing = pacing or {}
    ctx = RunContext(run_id, args.tier, args.vantage, git_sha(), identities, transport=transport,
                     monitor=monitor or NetworkMonitor(
                         check=(lambda: reachability_ok(args.reachability_url or REACHABILITY_URLS)),
                         sleep=sleep),
                     max_requests=args.max_requests, portals=portals, dotgov=dotgov,
                     seed=args.seed, sleep=sleep, monotonic=monotonic, **pacing)
    ctx.requests = spent
    ctx.place = network_record.place_of(args.vantage)
    if deep:  # the estimate is recomputed for what is still pending (robots per page host)
        lo = hi = page_request_estimate(pending, len(identities), ctx.clock.floor_s,
                                        ctx.clock.governors)["requests"]
    else:
        pe = run_estimate(pending, args.tier, len(identities), args.max_workers, ctx.pace_s,
                          ctx.clock.floor_s, ctx.clock.governors)
        lo, hi = pe["requests_min"], pe["requests_max"]
    netlookup = netlookup or network_record.lookup_all
    network = network_record.start_record(args.vantage, netlookup())
    writer = RunWriter(run_dir, ctx)
    started = now_iso()
    argv = list(sys.argv[1:]) if not getattr(args, "_argv", None) else list(args._argv)
    if "--run-dir" not in argv:
        argv += ["--run-dir", str(run_dir)]
    resume_cmd = shlex.join([sys.executable, str(pathlib.Path(__file__).resolve())] + argv)

    manifest = {
        "catalog_version": ar.CATALOG_VERSION, "git_sha": ctx.git_sha, "run_id": run_id,
        "tier": args.tier, "vantage": args.vantage, "machine": args.machine,
        "targets_source": (args.targets_file or str(TARGETS)) if not deep else {
            "websites": args.targets_file or "data/ai-readability/websites.json",
            "extra": args.deep_pages_extra or "data/ai-readability/deep-pages-extra.csv"},
        "mode": "deep_pages" if deep else "hosts", "hosts": len({j["host"] for j in jobs}),
        "units": len(jobs),
        "hosts_already_done": len(done), "started": started, "ended": None,
        "identities": identities, "robots_only_tokens_not_sent": list(ROBOTS_ONLY_TOKENS),
        "identities_not_sent": not_sent,
        "crawlers_csv": {"path": str(crawlers_path or crawler_list.CRAWLERS_CSV),
                         "sha256": ar.sha256(pathlib.Path(crawlers_path or
                                                          crawler_list.CRAWLERS_CSV).read_bytes()),
                         "checked_oldest": min((r["checked"] for r in crawler_rows), default=None)},
        "network": network,
        "pacing": {"global_pace_s": ctx.pace_s, "jitter_s": ctx.jitter_s,
                   "min_host_delay_s": ctx.clock.floor_s, "host_governors": ctx.clock.governors,
                   "max_workers": args.max_workers},
        "network_down_rule": {"streak": ctx.monitor.streak_limit, "min_hosts": ctx.monitor.min_hosts,
                              "recheck_s": ctx.monitor.recheck_s,
                              "reachability_urls": list(args.reachability_url or REACHABILITY_URLS)},
        "max_requests": args.max_requests,
    }
    (run_dir / "run.json").write_text(json.dumps(manifest, indent=2, default=str))
    if deep:
        print("run_dir={}  pages={} (pending {})  identities={}  ≈{} requests".format(
            run_dir, len(jobs), len(pending), len(identities), lo))
    else:
        print("run_dir={}  hosts={} (pending {})  identities={}  ≈{}–{} requests".format(
            run_dir, len(jobs), len(pending), len(identities), lo, hi))
    print("network at start: place={} provider={} asn={} ip={}{}".format(
        network["place"], network["start"].get("provider"), network["start"].get("asn"),
        network["start"].get("public_ip"),
        "  (lookup failed: {})".format(network["start"]["error"]) if network["start"].get("error")
        else ""))

    state, error = "broke", None

    def _on_signal(signum, _frame):
        raise KeyboardInterrupt("signal {}".format(signal.Signals(signum).name))

    old_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for s in (signal.SIGTERM, signal.SIGINT):
            old_handlers[s] = signal.signal(s, _on_signal)
    worker_errors = []
    try:
        n = max(1, args.max_workers)
        if n == 1:
            run_worker(pending, ctx, writer)
        else:
            groups = split_by_host(pending, n) if deep else [pending[i::n] for i in range(n)]

            def _guard(group):
                try:
                    run_worker(group, ctx, writer)
                except BaseException as e:  # a worker crash breaks the run, it is not swallowed
                    worker_errors.append("{}: {}".format(type(e).__name__, e))
                    ctx.stop.set()

            threads = [threading.Thread(target=_guard, args=(g,), daemon=True) for g in groups if g]
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(timeout=0.5)
        if worker_errors:
            error = "worker crashed: " + "; ".join(worker_errors)
        elif ctx.broke_reason:
            error = ctx.broke_reason
        else:
            state = "done"
    except KeyboardInterrupt as e:
        ctx.stop.set()
        error = "interrupted ({})".format(e or "SIGINT")
    except BaseException as e:
        ctx.stop.set()
        error = "crash: {}: {}".format(type(e).__name__, e)
    finally:
        for s, h in old_handlers.items():
            signal.signal(s, h)
        ended = now_iso()
        review_count = len(read_jsonl(run_dir / "review.jsonl"))
        status = {"state": state, "started": started, "ended": ended,
                  "requests": ctx.requests, "hosts": len(jobs),
                  "hosts_done": len(done) + writer.hosts_done, "review_count": review_count,
                  "mode": "deep_pages" if deep else "hosts",
                  **({"pages": len(jobs), "pages_done": len(done) + writer.hosts_done}
                     if deep else {}),
                  "quarantined_hosts": len(ctx.quarantined), "run_dir": str(run_dir)}
        if state == "broke":
            status["error"] = error
            status["resume_cmd"] = resume_cmd
        status_path = run_dir / "status.json"
        try:
            status_path = write_status(run_dir, status)
            try:
                network_record.finish_record(network, netlookup())
            except Exception as e:  # recorded, never guessed
                network_record.finish_record(network, {"error": "{}: {}".format(type(e).__name__, e)})
            manifest.update({"ended": ended, "requests": ctx.requests,
                             "quarantined_hosts": ctx.quarantined,
                             "network_checks": ctx.monitor.checks, "state": state})
            (run_dir / "run.json").write_text(json.dumps(manifest, indent=2, default=str))
            rows = read_jsonl(run_dir / "results.jsonl")
            (run_dir / "summary.md").write_text(ar.summarize(rows, review_count, {
                "run_id": run_id, "date": started_local.strftime("%Y-%m-%d"),
                "git_sha": ctx.git_sha, "tier": args.tier, "vantage": args.vantage,
                "machine": args.machine, "started": started, "ended": ended,
                "requests": ctx.requests}))
        except BaseException as e:  # disk full while closing out: still try the hook
            print("could not finish writing the run folder: {}".format(e), file=sys.stderr)
        print("\nstate={}  requests={}  hosts_done={}/{}  review={}  {}".format(
            state, ctx.requests, status["hosts_done"], len(jobs), review_count,
            ("error: " + error) if error else ""))
        if state == "broke":
            print("resume: " + resume_cmd)
        run_on_exit(args.on_exit, status_path)
    return 0 if state == "done" else 1



def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "data" / "probe"),
                    help="legacy tiers a|b|all only: output folder")
    ap.add_argument("--tier", choices=("a", "b", "all", "core", "full"), default="all",
                    help="a|b|all: the 2026-08 bot-access study. core|full: the AI-readability "
                         "test catalog (docs/ai-readability/tests.md)")
    ap.add_argument("--targets-file", default=None,
                    help="core|full: addresses.csv (issue #41 layout), websites.json, or a JSON "
                         "list of {entity_id, entity_name, url}. Default: "
                         "data/ai-readability/addresses.csv, else websites.json")
    ap.add_argument("--crawlers", default=None,
                    help="core|full|deep pages: the checked crawler list (default "
                         "data/ai-readability/crawlers.csv). The run refuses to start if it fails "
                         "its own checks or any row was checked more than 30 days ago")
    ap.add_argument("--out-root", default=str(ROOT / "data" / "ai-readability" / "runs"),
                    help="core|full: run folders are created here as <YYYY-MM-DD>-<vantage>/")
    ap.add_argument("--run-dir", default=None,
                    help="core|full: use (or resume) this exact run folder")
    ap.add_argument("--on-exit", default=None,
                    help="core|full: shell command run at exit with status.json's path appended")
    ap.add_argument("--max-workers", type=int, default=1,
                    help="core|full: parallel workers, each on its own hosts (default 1)")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                    help="core|full: hard request cap for the run")
    ap.add_argument("--machine", default=socket.gethostname(),
                    help="core|full: run-host label for run.json (use whichmachine.sh's name)")
    ap.add_argument("--portals", default=str(ROOT / "data" / "ai-readability" / "portals.json"))
    ap.add_argument("--dotgov-file", default=None,
                    help="core|full: local copy of the CISA .gov domain list (first CSV column) for I5")
    ap.add_argument("--reachability-url", action="append", default=None,
                    help="core|full: override the neutral network check URLs (repeatable)")
    ap.add_argument("--dry-run-show", type=int, default=3,
                    help="core|full dry run: how many hosts to list request by request")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit — no network at all")
    ap.add_argument("--limit", type=int, default=None, help="cap work units (smoke test)")
    ap.add_argument("--only-hosts", default=None,
                    help="comma-separated hosts, or @path to a newline-delimited file. Restricts "
                         "the run to these hosts — used for the second-vantage confirmation pass, "
                         "where the point is re-measuring the SAME hosts from another network.")
    ap.add_argument("--deep-pages", action="store_true",
                    help="score each page on hosts shared by several registry bodies (issue #35): "
                         "pages from websites.json (or --targets-file in that layout) plus "
                         "--deep-pages-extra, at the Tier B identities, page-level tests only")
    ap.add_argument("--deep-pages-extra", default=None,
                    help="--deep-pages: extra page list (url,name,source,checked); default "
                         "data/ai-readability/deep-pages-extra.csv")
    ap.add_argument("--seed", type=int, default=20260828)
    ap.add_argument("--vantage", default=os.environ.get("PROBE_VANTAGE", "unknown"),
                    help="the network this ran from: office | home | vpn (recorded as `place` in "
                         "run.json and on every row). vpn needs --only-hosts and at most {} "
                         "hosts".format(VPN_MAX_HOSTS))
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args._argv = list(argv) if argv is not None else list(sys.argv[1:])

    if args.deep_pages:
        return run_readability(args)
    if args.tier in ("core", "full"):
        if not args.targets_file and not default_targets_file().exists():
            print("FATAL: neither {} nor {} exists, and no --targets-file given.".format(
                ADDRESSES, WEBSITES), file=sys.stderr)
            return 2
        return run_readability(args)

    if not TARGETS.exists():
        print("FATAL: {} not found. Run scripts/crawl_targets.py first.".format(TARGETS),
              file=sys.stderr)
        return 2

    raw = json.loads(TARGETS.read_text())
    targets = raw.get("targets", raw) if isinstance(raw, dict) else raw

    only = None
    if args.only_hosts:
        if args.only_hosts.startswith("@"):
            only = {h.strip().lower() for h in
                    pathlib.Path(args.only_hosts[1:]).read_text().splitlines() if h.strip()}
        else:
            only = {h.strip().lower() for h in args.only_hosts.split(",") if h.strip()}

    rng = random.Random(args.seed)
    built = build_work(targets, args.tier)
    if only:
        built = [w for w in built if w["host"] in only]
        missing = only - {w["host"] for w in built}
        if missing:
            print("WARNING: no targets for {}".format(", ".join(sorted(missing))), file=sys.stderr)
        if not built:
            print("FATAL: --only-hosts matched nothing", file=sys.stderr)
            return 2
    work = interleave(built, rng)
    if args.limit:
        work = work[:args.limit]

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_path = outdir / "{}-{}-run-log.jsonl".format(stamp, args.vantage)
    res_path = outdir / "{}-{}-results.json".format(stamp, args.vantage)

    done = set()
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            try:
                rec = json.loads(line)
                done.add((rec["url"], rec["identity"]))
            except Exception:
                continue
        print("resuming: {} work units already logged".format(len(done)))

    pending = [w for w in work if (w["url"], w["identity"]) not in done]
    hosts = len({w["host"] for w in work})

    est_min = int(len(pending) * (GLOBAL_PACE_S + 0.4) / 60)
    print("vantage={}  tier={}  hosts={}  work_units={}  pending={}  est≈{}h{:02d}m".format(
        args.vantage, args.tier, hosts, len(work), len(pending), est_min // 60, est_min % 60))
    print("identities: {} ({} with operator-verified strings)".format(
        len(LEGACY_IDENTITIES), sum(1 for i in LEGACY_IDENTITIES if i["string_verified"])))
    print("cap={} requests | global pace≈{}s | governed hosts: {}".format(
        HARD_REQUEST_CAP, GLOBAL_PACE_S, ", ".join(HOST_GOVERNORS)))

    if args.dry_run:
        by_kind = defaultdict(int)
        for w in work:
            by_kind[w["kind"]] += 1
        print("\nplan by kind:")
        for k, v in sorted(by_kind.items()):
            print("  {:<16} {}".format(k, v))
        print("\nfirst 8 units:")
        for w in work[:8]:
            print("  {:<14} {:<48} {}".format(w["kind"], w["url"][:48], w["identity"]))
        return 0

    if len(pending) > HARD_REQUEST_CAP:
        print("FATAL: {} pending exceeds cap {}. Narrow the tier.".format(
            len(pending), HARD_REQUEST_CAP), file=sys.stderr)
        return 2

    ident_by_key = {i["key"]: i for i in LEGACY_IDENTITIES}
    pacer, breaker = Pacer(rng), CircuitBreaker()
    sent = 0
    skipped = 0
    started_at = now_iso()

    with log_path.open("a") as log:
        for i, w in enumerate(pending, 1):
            if breaker.tripped:
                print("\nCIRCUIT BREAKER: {}".format(breaker.reason), file=sys.stderr)
                break
            if breaker.is_quarantined(w["host"]):
                skipped += 1
                continue
            if sent >= HARD_REQUEST_CAP:
                print("\nrequest cap reached ({}); stopping".format(HARD_REQUEST_CAP))
                break

            ident = ident_by_key[w["identity"]]
            r = fetch(w["url"], ident["ua"], pacer, breaker)
            sent += 1

            rec = dict(w)
            rec.update({
                "ts": now_iso(), "vantage": args.vantage,
                "identity_class": ident["klass"],
                "identity_string_verified": ident["string_verified"],
                "result": {k: v for k, v in r.items() if k != "body"},
            })
            # The stated-policy arm: parse the body we already have. Never re-fetch.
            if w["kind"] == "robots" and r["status"] == 200 and not r["is_block_page"]:
                rec["robots"] = parse_robots(r.get("body") or "")
                rec["robots_raw"] = (r.get("body") or "")[:8000]
            elif w["kind"] == "robots":
                rec["robots"] = None
                rec["robots_unreadable_reason"] = (
                    "block_page" if r["is_block_page"] else "status_{}".format(r["status"]))
            elif w["kind"] == "llms":
                rec["llms_present"] = bool(
                    r["status"] == 200 and not r["is_block_page"] and (r.get("bytes") or 0) > 0)
                if rec["llms_present"]:
                    rec["llms_raw"] = (r.get("body") or "")[:8000]
            log.write(json.dumps(rec) + "\n")
            log.flush()

            if i % 25 == 0 or i == len(pending):
                pct = 100.0 * i / len(pending)
                print("  [{:>5}/{}] {:5.1f}%  last: {} {} -> {}".format(
                    i, len(pending), pct, w["identity"][:22], w["host"][:28], r["status"]))

    summary = {
        "started_at": started_at, "finished_at": now_iso(), "vantage": args.vantage,
        "tier": args.tier, "requests_sent": sent,
        "work_units_total": len(work), "hosts": hosts,
        "circuit_breaker_tripped": breaker.tripped, "circuit_breaker_reason": breaker.reason,
        "quarantined_hosts": breaker.quarantined, "units_skipped_quarantined": skipped,
        "identities": [{k: v for k, v in i.items() if k != "ua"} for i in LEGACY_IDENTITIES],
        "robots_only_tokens_not_sent": list(ROBOTS_ONLY_TOKENS),
        "pacing": {"global_pace_s": GLOBAL_PACE_S, "min_host_delay_s": MIN_HOST_DELAY_S,
                   "host_governors": HOST_GOVERNORS},
        "log": str(log_path),
    }
    res_path.write_text(json.dumps(summary, indent=2))
    print("\nsent={}  log={}\nsummary={}".format(sent, log_path, res_path))
    if breaker.tripped:
        print("NOTE: run stopped early by the circuit breaker; rerun the same command to resume.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
