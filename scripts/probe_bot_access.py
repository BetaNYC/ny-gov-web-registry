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
  them. A 429 quarantines that host; enough distinct hosts 429ing stops everything (see
  CircuitBreaker, which was corrected on 2026-08-28 after over-stopping killed the first run).

Run:  python3 scripts/probe_bot_access.py --out data/probe/  [--tier a|b|all] [--dry-run]
Resume: same command; completed work units are read from the checkpoint and skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

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


# Tripping globally on 429 needs to distinguish "some hosts refuse this way" from "everyone
# suddenly refuses US". A flat count cannot: across 126 hosts, a handful of 429-refusers is normal.
# Corrected 2026-08-28 (second failure) — a flat 5 tripped on six hosts that were each answering
# independently, most of them on FIRST contact:
#   brooklynmuseum.org (robots.txt), javitscenter.com (llms.txt), metmuseum.org (Googlebot),
#   cirsplans.org + dos.ny.gov (GPTBot), nycsci.org (CCBot)
# You cannot be rate-limited on request one. Those are refusals, i.e. DATA — not evidence about us.
# So the global trip is now proportional: it needs both an absolute floor AND a large share of the
# hosts actually contacted, which only a systemic block against our egress can produce.
GLOBAL_429_HOSTS_FLOOR = 15    # never trip below this many distinct hosts
GLOBAL_429_HOSTS_SHARE = 0.25  # ...and not until they are this share of hosts contacted


class CircuitBreaker:
    """Trips on signals that mean 'we are hurting the origin or the network' — NEVER on 403.

    A 403 is the measurement. Treating it as failure (as crawl_about.py rightly does for its own
    purpose) would abort this run almost immediately and collect nothing.

    ON 429 — CORRECTED 2026-08-28 AFTER THE FIRST RUN FAILED THIS WAY.
      The first build stopped the ENTIRE run on any single 429. Both passes duly died in ~11
      minutes: residential at 110 of 2,214 units on a 429 from brooklynmuseum.org, office at 108 of
      280 on one from esd.ny.gov. One museum's rate limit says nothing about nyc.gov, so killing all
      126 hosts over it threw away the study to protect a host we were no longer going to touch.

      Worse, the premise was wrong. brooklynmuseum.org returned 429 on the FIRST request we ever
      sent it — a robots.txt GET from our honest research UA. That is not "you are going too fast."
      That is a WAF using 429 as its refusal code. Conflating the two meant a host's *answer* was
      read as our misbehavior.

      So: a 429 now QUARANTINES that host (drop its remaining work, record why) and the run
      continues elsewhere. Only when GLOBAL_429_HOSTS_TRIP distinct hosts have 429'd is the problem
      plausibly on our side, and only then does everything stop.
    """

    def __init__(self) -> None:
        self.tripped = False
        self.reason = ""
        self.consecutive_transport_failures = 0
        self.quarantined = {}          # host -> reason
        self.hosts_contacted = set()

    def record(self, status, transport_error: bool, host: str = "") -> None:
        if host:
            self.hosts_contacted.add(host)
        if status == 429:
            if host and host not in self.quarantined:
                self.quarantined[host] = "HTTP 429 — recorded as a refusal; remaining work dropped"
            n, contacted = len(self.quarantined), max(1, len(self.hosts_contacted))
            if n >= GLOBAL_429_HOSTS_FLOOR and n >= GLOBAL_429_HOSTS_SHARE * contacted:
                self.tripped = True
                self.reason = ("{} of {} contacted hosts returned 429 ({:.0%}) — that share "
                               "implicates our egress rather than any one host; stopping".format(
                                   n, contacted, n / contacted))
            return
        if transport_error:
            self.consecutive_transport_failures += 1
            if self.consecutive_transport_failures >= 8:
                self.tripped = True
                self.reason = ("8 consecutive transport failures — network trouble or we are being "
                               "dropped; stopping rather than hammering")
        else:
            self.consecutive_transport_failures = 0

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "data" / "probe"))
    ap.add_argument("--tier", choices=("a", "b", "all"), default="all")
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
    args = ap.parse_args()

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
