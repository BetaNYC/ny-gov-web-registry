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
# EVERY string below is either verified verbatim against the operator's own documentation (fetched
# 2026-08-28, `doc` names the source) or explicitly flagged `string_verified: False`.
# Per platform/system/engineering-standards.md §0: a guessed UA would silently measure a bot that
# does not exist and produce confident garbage. Do not add an entry without a doc URL.
IDENTITIES = [
    {
        "key": "baseline_default",
        "label": "unadorned client (Python-urllib default)",
        "ua": None,  # None => library default; an honest, unremarkable non-browser client
        "klass": "baseline",
        "string_verified": True,
        "doc": "n/a — library default",
    },
    {
        "key": "research_betanyc",
        "label": "BetaNYC research crawler (attributable, contactable)",
        "ua": ("BetaNYC-agentic-access-study/1.0 "
               "(+https://github.com/BetaNYC/ny-gov-web-registry; noel@beta.nyc)"),
        "klass": "baseline_honest",
        "string_verified": True,
        "doc": "ours",
    },
    {
        "key": "googlebot",
        "label": "Googlebot (search)",
        "ua": ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; "
               "+http://www.google.com/bot.html) Chrome/126.0.0.0 Safari/537.36"),
        "klass": "search",
        "string_verified": True,
        "doc": "https://developers.google.com/search/docs/crawling-indexing/google-common-crawlers",
    },
    {
        "key": "gptbot",
        "label": "GPTBot (AI training)",
        "ua": ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.4; "
               "+https://openai.com/gptbot"),
        "klass": "ai_training",
        "string_verified": True,
        "doc": "https://developers.openai.com/api/docs/bots",
    },
    {
        "key": "ccbot",
        "label": "CCBot (Common Crawl, AI training corpus)",
        "ua": "CCBot/2.0 (https://commoncrawl.org/faq/)",
        "klass": "ai_training",
        "string_verified": True,
        "doc": "https://commoncrawl.org/ccbot",
    },
    {
        "key": "claudebot",
        "label": "ClaudeBot (AI training)",
        # Anthropic documents the robots.txt TOKEN but does not publish a full UA string. The token
        # is what robots.txt and most WAF rules match on, so the token is embedded in the
        # conventional wrapper and the limitation is recorded rather than hidden.
        "ua": ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ClaudeBot/1.0; "
               "+claudebot@anthropic.com"),
        "klass": "ai_training",
        "string_verified": False,
        "doc": "https://support.claude.com/en/articles/8896518 (token documented; full string not published)",
    },
    {
        "key": "oai_searchbot",
        "label": "OAI-SearchBot (AI answer-engine index)",
        "ua": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like "
               "Gecko) Chrome/131.0.0.0 Safari/537.36; compatible; OAI-SearchBot/1.4; "
               "+https://openai.com/searchbot"),
        "klass": "ai_search",
        "string_verified": True,
        "doc": "https://developers.openai.com/api/docs/bots",
    },
    {
        "key": "perplexitybot",
        "label": "PerplexityBot (AI answer-engine index)",
        "ua": ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; "
               "+https://perplexity.ai/perplexitybot)"),
        "klass": "ai_search",
        "string_verified": True,
        "doc": "https://docs.perplexity.ai/guides/bots",
    },
    {
        "key": "chatgpt_user",
        "label": "ChatGPT-User (AGENTIC — a person asked, right now)",
        "ua": ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; "
               "+https://openai.com/bot"),
        "klass": "ai_agentic",
        "string_verified": True,
        "doc": "https://developers.openai.com/api/docs/bots",
    },
    {
        "key": "perplexity_user",
        "label": "Perplexity-User (AGENTIC — a person asked, right now)",
        "ua": ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; "
               "+https://perplexity.ai/perplexity-user)"),
        "klass": "ai_agentic",
        "string_verified": True,
        "doc": "https://docs.perplexity.ai/guides/bots",
    },
    {
        "key": "claude_user",
        "label": "Claude-User (AGENTIC — a person asked, right now)",
        "ua": ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; Claude-User/1.0; "
               "+claudebot@anthropic.com"),
        "klass": "ai_agentic",
        "string_verified": False,
        "doc": "https://support.claude.com/en/articles/8896518 (token documented; full string not published)",
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
        "doc": "n/a — deliberate control, disclosed in the dataset",
    },
]

# Tier B asks only "does this deep path differ from its host's apex?" Three identities answer that:
# an honest baseline, one honest AI agent, and the mechanism probe. Twelve would be waste.
TIER_B_IDENTITY_KEYS = ("research_betanyc", "claudebot", "mechanism_probe_chrome")

# Product tokens that are robots.txt CONTROL ONLY — they never issue a request. Scored in the
# stated-policy arm; sending them as a live UA would be an error.
ROBOTS_ONLY_TOKENS = ("Google-Extended", "Applebot-Extended")

# Tokens we look for when parsing robots.txt.
ROBOTS_AI_TOKENS = (
    "GPTBot", "OAI-SearchBot", "ChatGPT-User",
    "ClaudeBot", "Claude-User", "Claude-SearchBot", "anthropic-ai", "Claude-Web",
    "PerplexityBot", "Perplexity-User",
    "CCBot", "Bytespider", "Meta-ExternalAgent", "Amazonbot", "Applebot",
    "Google-Extended", "Applebot-Extended", "cohere-ai", "Diffbot", "omgili",
)
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

    ident_by_key = {i["key"]: i for i in IDENTITIES}
    work = []

    for host, items in sorted(by_host.items()):
        items_sorted = sorted(items, key=lambda x: x.get("entity_id") or x.get("id") or "")
        rep = items_sorted[0]

        if tier in ("a", "all"):
            work.append({"kind": "robots", "host": host,
                         "url": "https://{}/robots.txt".format(host), "identity": "research_betanyc"})
            work.append({"kind": "llms", "host": host,
                         "url": "https://{}/llms.txt".format(host), "identity": "research_betanyc"})
            for ident in IDENTITIES:
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
    "notfound": 64_000,
    "http": 8_000,
    "md_accept": 256_000,
    "md_index": 256_000,
    "link": 8_000,
}
KIND_TEST = {"robots": "W1", "llms": "M4", "sitemap": "M2", "home": "W4", "notfound": "F4",
             "http": "I5", "llms_full": "M5", "md_accept": "R2", "md_index": "R2", "link": "F3"}

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

# Bing's legacy desktop bingbot string, verbatim from
# https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0 (accessed 2026-11-06).
BINGBOT = {
    "key": "bingbot",
    "label": "bingbot (search)",
    "ua": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "klass": "search",
    "string_verified": True,
    "doc": "https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0",
}


def tier_identities(tier: str) -> list:
    """core: the six of tests.md; full: every identity the legacy probe sends, plus bingbot."""
    by_key = {i["key"]: i for i in IDENTITIES}
    if tier == "core":
        return [by_key[k] for k in CORE_IDENTITY_KEYS]
    ids = [by_key[RESEARCH_KEY]] + [i for i in IDENTITIES if i["key"] != RESEARCH_KEY]
    return ids + [BINGBOT]


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
            return {"url": url, "final_url": current, "status": None, "headers": {}, "body": "",
                    "raw": b"", "bytes": 0, "content_type": "", "is_block_page": None,
                    "body_class": None, "waf": [], "redirects": redirects, "peercert": peercert,
                    "elapsed_s": round(time.time() - started, 2), "truncated": False,
                    "transport_error": "{}: {}".format(type(e).__name__, str(e)[:200]),
                    "transport_kind": _transport_kind(e)}
        location = hdrs.get("location")
        if status in (301, 302, 303, 307, 308) and location and follow != "none" \
                and len(redirects) < MAX_REDIRECTS:
            nxt = urllib.parse.urljoin(current, location)
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
        return {"url": url, "final_url": current, "status": status, "headers": hdrs, "body": body,
                "raw": raw, "bytes": len(raw), "content_type": ctype, "is_block_page": is_block,
                "body_class": cls, "waf": fingerprint_waf(hdrs, hdrs.get("set-cookie", "")),
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
                 monotonic=time.monotonic, retry_backoff_s=RETRY_BACKOFF_S):
        self.run_id, self.tier, self.vantage, self.git_sha = run_id, tier, vantage, git_sha
        self.identities = identities
        self.ident_by_key = {i["key"]: i for i in identities}
        self.transport = transport or http_request
        self.pace_s, self.jitter_s = pace_s, jitter_s
        self.sleep, self.monotonic = sleep, monotonic
        self.retry_backoff_s = retry_backoff_s
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

    if ctx.tier == "full":
        llms_full = yield req("llms_full", base + "/llms-full.txt", follow="same_host")
        row("M5", RESEARCH_KEY, ar.test_m5(llms_full))
        md_accept = yield req("md_accept", home_url, accept="text/markdown")
        md_index = yield req("md_index", base + "/index.md", follow="same_host")
        row("R2", RESEARCH_KEY, ar.test_r2(md_accept, md_index))
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


QUARANTINED_RESP = {"status": None, "headers": {}, "body": "", "raw": b"", "bytes": 0,
                    "is_block_page": None, "body_class": None, "waf": [], "redirects": [],
                    "transport_error": None, "quarantined": True}


def perform(spec: dict, job: dict, ctx: RunContext, review: list, last_global: list) -> dict:
    """Send one request (paced, quarantine-aware, single retry on 5xx/transport), and log
    pushback to `review`. Returns a response dict (synthetic when the host is quarantined)."""
    host = job["host"]
    req_host = urllib.parse.urlparse(spec["url"]).netloc.lower()
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
        ctx.clock.acquire(req_host)
        try:
            resp = ctx.transport(spec["url"], ident["ua"], method=spec["method"],
                                 accept=spec["accept"], follow=spec["follow"],
                                 max_bytes=READ_CAPS.get(spec["kind"], 120_000))
        finally:
            ctx.clock.release(req_host)
            last_global[0] = ctx.monotonic()
        transport_fail = resp.get("status") is None and bool(resp.get("transport_error"))
        ctx.monitor.record(req_host, transport_fail)
        if ctx.monitor.down:
            ctx.broke_reason = ctx.broke_reason or ctx.monitor.reason
            ctx.stop.set()
        retryable = transport_fail or (resp.get("status") or 0) >= 500
        if retryable and attempt == 1 and not ctx.stop.is_set():
            ctx.sleep(ctx.retry_backoff_s)
            continue
        break
    pb = _pushback(resp)
    if pb:
        what, quarantine = pb
        on_own_host = req_host == host
        if quarantine and on_own_host:
            ctx.quarantine(host, "{} on {} ({})".format(what, KIND_TEST.get(spec["kind"]),
                                                        spec["identity"]))
        review.append({"run_id": ctx.run_id, "ts": now_iso(), "host": host,
                       "entity_id": job.get("entity_id"), "test": KIND_TEST.get(spec["kind"]),
                       "kind": spec["kind"], "identity": spec["identity"], "url": spec["url"],
                       "what": what, "status": resp.get("status"),
                       "action": "quarantined" if (quarantine and on_own_host) else "recorded"})
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
    for host, items in sorted(by_host.items()):
        items = sorted(items, key=lambda x: (x.get("entity_id") is None, x.get("entity_id") or ""))
        rep = items[0]
        url = rep["url"]
        if urllib.parse.urlparse(url).scheme != "https":
            url = "https://{}{}".format(host, urllib.parse.urlparse(url).path or "/")
        jobs.append({"host": host, "url": url, "entity_id": rep.get("entity_id"),
                     "entity_name": rep.get("entity_name"),
                     "entity_ids": [i.get("entity_id") for i in items]})
    return jobs


def load_targets(targets_file) -> list:
    """--targets-file: a JSON list of {entity_id, entity_name, url} (entity_id may be null), or
    {"targets": [...]}. Without it: data/crawl_targets.json, mapped to the same shape."""
    if targets_file:
        raw = json.loads(pathlib.Path(targets_file).read_text())
        items = raw.get("targets", raw) if isinstance(raw, dict) else raw
        out = []
        for t in items:
            if not isinstance(t, dict) or not t.get("url"):
                raise ValueError("every target needs a 'url': {!r}".format(t))
            out.append({"entity_id": t.get("entity_id"), "entity_name": t.get("entity_name"),
                        "url": t["url"]})
        return out
    raw = json.loads(TARGETS.read_text())
    items = raw.get("targets", raw) if isinstance(raw, dict) else raw
    return [{"entity_id": t.get("entity_id") or t.get("id"), "entity_name": t.get("name"),
             "url": t.get("start_url")} for t in items if t.get("start_url")]


def requests_per_host(tier: str, n_identities: int) -> "tuple[int, int]":
    """(min, max) requests one host costs, before retries. core: robots, llms, sitemap,
    homepage × identities, 404 probe, http:// check. full adds llms-full, two markdown
    probes and up to 10 link checks (HEAD, GET on 405)."""
    core = 5 + n_identities
    if tier == "core":
        return core, core
    return core + 3, core + 3 + 20


def run_worker(jobs: list, ctx: RunContext, writer, window: int = 8) -> None:
    """Round-robin over a window of hosts: always send the request whose host floor frees up
    first, so one host never sees back-to-back requests and the global pace stays busy."""
    rng = random.Random(ctx.seed)
    queue = list(jobs)
    rng.shuffle(queue)
    active = []          # [job, gen, pending_spec, review]
    last_global = [-1e18]

    def admit():
        while queue and len(active) < window:
            job = queue.pop(0)
            gen = host_plan(job, ctx)
            spec = next(gen)
            active.append([job, gen, spec, []])

    admit()
    while active and not ctx.stop.is_set():
        # the host whose floor frees soonest
        active.sort(key=lambda a: ctx.clock.ready_at(
            urllib.parse.urlparse(a[2]["url"]).netloc.lower()))
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
                f.write(json.dumps({"host": job["host"], "ts": now_iso(),
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
    """Hosts already finished, and requests already spent. Rows for a host that was mid-flight
    at the crash are dropped, so the resumed run writes each host exactly once."""
    progress = read_jsonl(run_dir / "progress.jsonl")
    done = {p["host"] for p in progress}
    spent = max([p.get("requests_so_far", 0) for p in progress] + [0])
    for name in ("results.jsonl", "review.jsonl"):
        path = run_dir / name
        if path.exists():
            keep = [r for r in read_jsonl(path) if r.get("host") in done]
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


def run_readability(args, transport=None, monitor=None, sleep=time.sleep,
                    monotonic=time.monotonic, pacing=None) -> int:
    """Entry point for --tier core|full. Returns 0 (done) or 1 (broke)."""
    targets = load_targets(args.targets_file)
    jobs = plan_targets(targets)
    if args.only_hosts:
        only = ({h.strip().lower() for h in pathlib.Path(args.only_hosts[1:]).read_text().splitlines()
                 if h.strip()} if args.only_hosts.startswith("@") else
                {h.strip().lower() for h in args.only_hosts.split(",") if h.strip()})
        jobs = [j for j in jobs if j["host"] in only]
    if args.limit:
        jobs = jobs[:args.limit]
    identities = tier_identities(args.tier)
    lo, hi = requests_per_host(args.tier, len(identities))

    if args.dry_run:
        print("tier={}  hosts={}  identities={}  requests≈{}–{} ({}–{} per host, before retries)"
              .format(args.tier, len(jobs), len(identities), lo * len(jobs), hi * len(jobs), lo, hi))
        print("identities: " + ", ".join(i["key"] for i in identities))
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
    pending = [j for j in jobs if j["host"] not in done]

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
    writer = RunWriter(run_dir, ctx)
    started = now_iso()
    argv = list(sys.argv[1:]) if not getattr(args, "_argv", None) else list(args._argv)
    if "--run-dir" not in argv:
        argv += ["--run-dir", str(run_dir)]
    resume_cmd = shlex.join([sys.executable, str(pathlib.Path(__file__).resolve())] + argv)

    manifest = {
        "catalog_version": ar.CATALOG_VERSION, "git_sha": ctx.git_sha, "run_id": run_id,
        "tier": args.tier, "vantage": args.vantage, "machine": args.machine,
        "targets_source": args.targets_file or str(TARGETS), "hosts": len(jobs),
        "hosts_already_done": len(done), "started": started, "ended": None,
        "identities": identities, "robots_only_tokens_not_sent": list(ROBOTS_ONLY_TOKENS),
        "pacing": {"global_pace_s": ctx.pace_s, "jitter_s": ctx.jitter_s,
                   "min_host_delay_s": ctx.clock.floor_s, "host_governors": ctx.clock.governors,
                   "max_workers": args.max_workers},
        "network_down_rule": {"streak": ctx.monitor.streak_limit, "min_hosts": ctx.monitor.min_hosts,
                              "recheck_s": ctx.monitor.recheck_s,
                              "reachability_urls": list(args.reachability_url or REACHABILITY_URLS)},
        "max_requests": args.max_requests,
    }
    (run_dir / "run.json").write_text(json.dumps(manifest, indent=2, default=str))
    print("run_dir={}  hosts={} (pending {})  identities={}  ≈{}–{} requests".format(
        run_dir, len(jobs), len(pending), len(identities), lo * len(pending), hi * len(pending)))

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
            groups = [pending[i::n] for i in range(n)]

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
                  "quarantined_hosts": len(ctx.quarantined), "run_dir": str(run_dir)}
        if state == "broke":
            status["error"] = error
            status["resume_cmd"] = resume_cmd
        status_path = run_dir / "status.json"
        try:
            status_path = write_status(run_dir, status)
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
                    help="core|full: JSON list of {entity_id, entity_name, url} to run instead of "
                         "data/crawl_targets.json (entity_id may be null)")
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
    ap.add_argument("--seed", type=int, default=20260828)
    ap.add_argument("--vantage", default=os.environ.get("PROBE_VANTAGE", "unknown"),
                    help="label for the network this ran from, e.g. 'residential' or 'office'")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args._argv = list(argv) if argv is not None else list(sys.argv[1:])

    if args.tier in ("core", "full"):
        if not args.targets_file and not TARGETS.exists():
            print("FATAL: {} not found and no --targets-file given.".format(TARGETS),
                  file=sys.stderr)
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
        len(IDENTITIES), sum(1 for i in IDENTITIES if i["string_verified"])))
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

    ident_by_key = {i["key"]: i for i in IDENTITIES}
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
        "identities": [{k: v for k, v in i.items() if k != "ua"} for i in IDENTITIES],
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
