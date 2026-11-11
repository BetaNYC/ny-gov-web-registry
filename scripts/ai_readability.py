"""ai_readability.py — the pure half of the AI-readability study: parsers, verdicts, scoring.

The spec is docs/ai-readability/tests.md (catalog v0.1). Every function here is pure: it takes a
response (or a body) that the runner in probe_bot_access.py already fetched, and returns a verdict
plus evidence. Nothing in this module touches the network, so every rule can be tested offline
against fixtures (tests/test_ai_readability.py).

VERDICTS
  pass | partial | fail | blocked | na   — scored tests
  reported                               — report-only tests (W3, W5, W6, M3, M5, R2, R3, I4, I5,
                                           F2, P*, C1, D2, D3, D4)

  `blocked` means the edge refused us (401/403/429 or a challenge page). `na` means we could not
  measure (transport failure, host quarantined, or the input the test needs does not exist).
  The two are kept apart because "the edge said no" and "we never got an answer" have different
  owners.

A RESPONSE (`resp`) is the dict the runner builds per request:
  {url, final_url, status, headers (lower-cased keys), body (str), raw (bytes, may be absent),
   content_type, is_block_page, body_class, transport_error, redirects [..], peercert (dict|None),
   quarantined (bool, optional)}
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import statistics
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime
from html.parser import HTMLParser

CATALOG_VERSION = "0.2"   # 0.2 (2026-11-10): crawlers.csv identities; C1, D2–D7 (#43, #44)

# ------------------------------------------------------------------------------------ tokens
# W2's token list: the AI tokens already matched by probe_bot_access.ROBOTS_AI_TOKENS plus the
# robots-only and "also matched" tokens listed in tests.md § Identities. Matching is by product
# token, case-insensitive (RFC 9309 §2.2.1), never by full UA string.
#
# From catalog 0.2 the list is data/ai-readability/crawlers.csv (every name, sent or rules-only;
# issue #44), read through crawler_list. Two kinds of name are kept on top of it, both outside
# the checked list on purpose:
#   - SEARCH_CONTROL_TOKENS are scored separately in W3 (search crawlers, not AI);
#   - LEGACY_ROBOTS_TOKENS were matched by catalog 0.1 and have no row in crawlers.csv (no
#     company page was checked for them). They are only ever READ in robots.txt, never sent, and
#     are kept so W2 stays comparable with the 2026-11-06 run.
import crawler_list as _crawlers  # noqa: E402  (pure: reads one CSV)

SEARCH_CONTROL_TOKENS = ("Googlebot", "bingbot")
LEGACY_ROBOTS_TOKENS = ("anthropic-ai", "Claude-Web", "cohere-ai", "Diffbot", "omgili")
_ROWS = _crawlers.load()
AI_TOKENS = tuple(t for t in _crawlers.robots_tokens(_ROWS) if t not in SEARCH_CONTROL_TOKENS) \
    + LEGACY_ROBOTS_TOKENS

# Identity key -> the robots.txt product token that identity answers to (W5 pairs them).
IDENTITY_TOKENS = {_crawlers.key_for(t): t for t in _crawlers.robots_tokens(_ROWS)}

REFUSAL_STATUSES = (401, 403, 429)


def sha256(text) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8", "replace")
    return hashlib.sha256(text or b"").hexdigest()


def refused(resp: dict) -> bool:
    return bool(resp) and (resp.get("status") in REFUSAL_STATUSES or bool(resp.get("is_block_page")))


def unreachable(resp) -> bool:
    return (not resp) or bool(resp.get("quarantined")) or (
        resp.get("status") is None and bool(resp.get("transport_error")))


def _unmeasured(resp, what: str):
    """Common verdict for a response we could not read: blocked if refused, na if unreachable."""
    if unreachable(resp):
        reason = "host quarantined earlier in this run" if (resp or {}).get("quarantined") else \
            "transport error: {}".format((resp or {}).get("transport_error") or "no response")
        return "na", {"reason": reason, "what": what}
    if refused(resp):
        return "blocked", {"status": resp.get("status"), "body_class": resp.get("body_class"),
                           "what": what}
    return None


def looks_like_html(body: str) -> bool:
    head = (body or "").lstrip()[:512].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<head" in head \
        or "<body" in head


def content_type_of(resp: dict) -> str:
    return ((resp.get("headers") or {}).get("content-type") or resp.get("content_type") or "").lower()


# ==================================================================================== robots
def parse_robots_full(text: str) -> dict:
    """RFC 9309 parse that keeps what W3/W6/M1 need: ordered groups with their Allow/Disallow
    rules, Sitemap lines, Content-Signal / Content-Usage lines.

    Group semantics follow RFC 9309 §2.2.1: consecutive user-agent lines share one group; a rule
    line ends the run of user-agent lines. Groups naming the same token are merged at lookup time
    (§2.2.1: "If there are multiple groups ... matching the user-agent, the matching groups' rules
    MUST be combined into one group").

    Sitemap is not part of RFC 9309 groups; it is the sitemaps.org extension and may appear
    anywhere (https://www.sitemaps.org/protocol.html#submit_robots).
    """
    groups = []            # [{"agents": [..], "rules": [(kind, path)], "signals": [...]}]
    current = None
    in_agent_run = False
    sitemaps, content_signals, content_usage = [], [], []
    for raw_line in (text or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if not in_agent_run or current is None:
                current = {"agents": [], "rules": [], "content_signal": []}
                groups.append(current)
                in_agent_run = True
            current["agents"].append(value)
        elif field in ("allow", "disallow"):
            in_agent_run = False
            if current is not None:
                current["rules"].append((field, value))
        elif field == "sitemap":
            sitemaps.append(value)
        elif field == "content-signal":
            # Cloudflare Content Signals Policy (https://blog.cloudflare.com/content-signals-policy/)
            # A Content-Signal line sits inside a user-agent group in Cloudflare's examples; we keep
            # the raw value and the group it was in. Not a standard (tests.md W6).
            content_signals.append({"value": value,
                                    "agents": list(current["agents"]) if current else []})
            if current is not None:
                current["content_signal"].append(value)
        elif field == "content-usage":
            # IETF draft-ietf-aipref-attach (robots.txt Content-Usage). Kept RAW: the draft's
            # vocabulary is still moving, so we do not interpret values (tests.md W6).
            content_usage.append({"value": value,
                                  "agents": list(current["agents"]) if current else []})
        else:
            # crawl-delay and unknown fields do not end the agent run per RFC 9309 they are simply
            # ignored, but a non-agent line does mean the next user-agent starts a new group.
            in_agent_run = False
    return {"groups": groups, "sitemaps": sitemaps,
            "content_signals": content_signals, "content_usage": content_usage}


def _rules_for(parsed: dict, token: str):
    """RFC 9309 §2.2.1 group selection: groups whose user-agent equals the token (case-insensitive)
    are combined; if none, the '*' group(s); if none, no rules (everything allowed)."""
    low = token.lower()
    own = [g for g in parsed["groups"] if any(a.lower() == low for a in g["agents"])]
    if own:
        return "own", [r for g in own for r in g["rules"]]
    star = [g for g in parsed["groups"] if any(a == "*" for a in g["agents"])]
    if star:
        return "*", [r for g in star for r in g["rules"]]
    return "none", []


def _pattern_regex(pattern: str):
    # RFC 9309 §2.2.3: '*' matches any sequence, '$' at the end anchors. Everything else literal.
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile("^" + rx + ("$" if anchored else ""))


def _norm_path(path: str) -> str:
    # RFC 9309 §2.2.2: compare percent-encoded forms; uppercase the hex of existing escapes so
    # "%2f" and "%2F" compare equal.
    return re.sub(r"%[0-9a-fA-F]{2}", lambda m: m.group(0).upper(), path or "/")


def robots_decision(parsed: dict, token: str, path: str = "/") -> dict:
    """RFC 9309 §2.2.2 longest match: the rule with the most octets in its path wins; on a tie
    between Allow and Disallow, Allow wins ('SHOULD use the least restrictive rule'). An empty
    Disallow matches nothing. /robots.txt itself is always allowed (§2.2.2)."""
    group, rules = _rules_for(parsed, token)
    path = _norm_path(path)
    if path == "/robots.txt":
        return {"token": token, "path": path, "group": group, "decision": "allow",
                "matched_rule": None}
    best = None
    for kind, pat in rules:
        if pat == "":
            continue
        pat_n = _norm_path(pat)
        if _pattern_regex(pat_n).match(path):
            length = len(pat_n)
            if best is None or length > best[0] or (length == best[0] and kind == "allow"):
                best = (length, kind, pat)
    decision = "allow" if best is None or best[1] == "allow" else "disallow"
    return {"token": token, "path": path, "group": group, "decision": decision,
            "matched_rule": None if best is None else "{}: {}".format(best[1].capitalize(), best[2])}


# ---------------------------------------------------------------------------------- W tests
def test_w1(resp: dict):
    """W1 robots.txt reachable: 200, text/plain, parses as robots (not HTML)."""
    um = _unmeasured(resp, "robots.txt")
    if um:
        return um
    body = resp.get("body") or ""
    ctype = content_type_of(resp)
    ev = {"status": resp.get("status"), "content_type": ctype, "final_url": resp.get("final_url"),
          "redirects": resp.get("redirects") or [], "first_8kb": body[:8192], "sha256": sha256(body)}
    if resp.get("status") != 200:
        if resp.get("offhost_redirect"):
            ev["note"] = "redirected off-host; not followed (tests.md W1)"
            return "partial", ev
        return "fail", ev
    if looks_like_html(body):
        ev["note"] = "200 but the body is HTML, not robots"
        return "fail", ev
    parsed = parse_robots_full(body)
    is_robots = bool(parsed["groups"] or parsed["sitemaps"]) or body.strip() == ""
    if not is_robots:
        ev["note"] = "200 but no robots directives found"
        return "fail", ev
    if not ctype.startswith("text/plain"):
        ev["note"] = "parses as robots but content-type is not text/plain"
        return "partial", ev
    return "pass", ev


def robots_body(resp: dict):
    """The robots body if W1 could read one (pass or partial), else None."""
    if not resp or resp.get("status") != 200 or refused(resp) or looks_like_html(resp.get("body") or ""):
        return None
    return resp.get("body") or ""


def test_w2(resp: dict, legacy_parse: dict = None):
    um = _unmeasured(resp, "robots.txt")
    if um:
        return um
    body = robots_body(resp)
    if body is None:
        return "fail", {"reason": "no readable robots.txt (see W1)", "status": resp.get("status")}
    parsed = parse_robots_full(body)
    named = []
    for g in parsed["groups"]:
        for a in g["agents"]:
            for t in AI_TOKENS:
                if a.lower() == t.lower() and t not in [n["token"] for n in named]:
                    named.append({"token": t, "rules": ["{}: {}".format(k.capitalize(), v)
                                                        for k, v in g["rules"]]})
    ev = {"tokens_named": [n["token"] for n in named], "groups": named}
    return ("pass" if named else "fail"), ev


def test_w3(resp: dict, homepage_path: str = "/"):
    """W3 — reported: per AI token, allow/disallow at / and at the homepage path."""
    um = _unmeasured(resp, "robots.txt")
    if um:
        return um
    body = robots_body(resp)
    if body is None:
        # No robots.txt = no restrictions (RFC 9309 §2.3.1.3 for 4xx "unavailable"). We still
        # report, because "allowed by absence" is a real answer.
        parsed = {"groups": []}
        basis = "no readable robots.txt (status {}); RFC 9309 §2.3.1.3 treats 4xx as allow-all".format(
            resp.get("status"))
        if resp.get("status") and resp["status"] >= 500:
            basis = "robots.txt 5xx; RFC 9309 §2.3.1.4 treats it as disallow-all"
            return "reported", {"basis": basis, "tokens": {t: {"/": "disallow"} for t in AI_TOKENS}}
    else:
        parsed = parse_robots_full(body)
        basis = "parsed"
    tokens = {}
    paths = ["/"] + ([homepage_path] if homepage_path and homepage_path != "/" else [])
    for t in AI_TOKENS + ("Googlebot", "bingbot"):
        tokens[t] = {p: robots_decision(parsed, t, p) for p in paths}
    return "reported", {"basis": basis, "tokens": tokens}


def w4_identity_verdict(resp: dict):
    """W4 per identity: admitted = 200 and not a challenge/block page."""
    if unreachable(resp):
        return "na", {"reason": (resp or {}).get("transport_error") or "quarantined"}
    ev = {"status": resp.get("status"), "body_class": resp.get("body_class"),
          "waf": resp.get("waf") or [], "server": (resp.get("headers") or {}).get("server"),
          "final_url": resp.get("final_url"), "snippet_2kb": (resp.get("body") or "")[:2048]}
    if resp.get("status") == 200 and not resp.get("is_block_page"):
        return "pass", ev
    if refused(resp):
        return "blocked", ev
    return "fail", ev


def w4_aggregate(per_identity: dict, identity_classes: dict):
    """Abhi row 'Homepage reachable by an agent': pass if every AI identity admitted, partial if
    some, blocked if none admitted and at least one was refused, fail otherwise."""
    ai = {k: v for k, v in per_identity.items() if identity_classes.get(k, "").startswith("ai_")}
    if not ai:
        return "na", {"reason": "no AI identities in this tier"}
    verdicts = {k: v for k, v in ai.items()}
    admitted = [k for k, v in verdicts.items() if v == "pass"]
    ev = {"ai_identities": verdicts, "admitted": admitted}
    if len(admitted) == len(verdicts):
        return "pass", ev
    if admitted:
        return "partial", ev
    if any(v == "blocked" for v in verdicts.values()):
        return "blocked", ev
    if all(v == "na" for v in verdicts.values()):
        return "na", ev
    return "fail", ev


def test_w5(w3_evidence: dict, w4_by_identity: dict, homepage_path: str = "/"):
    """W5 — derived: robots says allow but the edge refuses (or the reverse), per token."""
    if not w3_evidence or "tokens" not in w3_evidence:
        return "na", {"reason": "W3 not available"}
    pairs, divergent = {}, []
    for ident, (verdict, ev) in w4_by_identity.items():
        token = IDENTITY_TOKENS.get(ident)
        if not token or token not in w3_evidence["tokens"]:
            continue
        per_path = w3_evidence["tokens"][token]
        d = per_path.get(homepage_path) or per_path.get("/")
        stated = d["decision"] if isinstance(d, dict) else d
        if verdict == "na":
            continue
        enforced = "admit" if verdict == "pass" else ("refuse" if verdict == "blocked" else "error")
        flag = None
        if stated == "allow" and enforced == "refuse":
            flag = "stated_allow_enforced_refuse"
        elif stated == "disallow" and enforced == "admit":
            flag = "stated_disallow_enforced_admit"
        pairs[ident] = {"token": token, "stated": stated, "enforced": enforced, "status":
                        ev.get("status"), "flag": flag,
                        "transient": bool(ev.get("status") and ev["status"] >= 500)}
        if flag:
            divergent.append(ident)
    return "reported", {"pairs": pairs, "divergent": divergent, "any_divergence": bool(divergent)}


def test_w6(resp: dict):
    """W6 — reported: Content-Signal, Content-Usage, and __usage_* user-agent groups."""
    um = _unmeasured(resp, "robots.txt")
    if um:
        return um
    body = robots_body(resp)
    if body is None:
        return "reported", {"present": False, "reason": "no readable robots.txt"}
    parsed = parse_robots_full(body)
    signals = []
    for s in parsed["content_signals"]:
        kv = {}
        for part in s["value"].split(","):
            k, sep, v = part.partition("=")
            if sep:
                kv[k.strip().lower()] = v.strip().lower()
        signals.append(dict(s, pairs=kv))
    # draft-kelley-aipref-training-preference-signal-00 uses user-agent groups whose names begin
    # with "__usage_". We record any such group and its rules; we do not interpret them.
    usage_groups = [{"agents": g["agents"], "rules": ["{}: {}".format(k.capitalize(), v)
                                                      for k, v in g["rules"]]}
                    for g in parsed["groups"] if any(a.lower().startswith("__usage_")
                                                     for a in g["agents"])]
    present = bool(signals or parsed["content_usage"] or usage_groups)
    return "reported", {"present": present, "content_signal": signals,
                        "content_usage": parsed["content_usage"], "usage_groups": usage_groups}


# ---------------------------------------------------------------------------------- M tests
def sitemap_urls_from_robots(body: str) -> list:
    """Absolute http(s) URLs from Sitemap: lines (M1)."""
    out = []
    for s in parse_robots_full(body or "")["sitemaps"]:
        u = urllib.parse.urlparse(s)
        if u.scheme in ("http", "https") and u.netloc:
            out.append(s)
    return out


def test_m1(robots_resp: dict):
    um = _unmeasured(robots_resp, "robots.txt")
    if um:
        return um
    body = robots_body(robots_resp)
    if body is None:
        return "fail", {"reason": "no readable robots.txt", "status": robots_resp.get("status")}
    declared = parse_robots_full(body)["sitemaps"]
    absolute = sitemap_urls_from_robots(body)
    ev = {"declared": declared, "absolute": absolute}
    return ("pass" if absolute else "fail"), ev


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_sitemap(raw: bytes) -> dict:
    """Parse a sitemap body (sitemaps.org protocol, https://www.sitemaps.org/protocol.html).
    Handles gzip by magic bytes (a .xml.gz is served as the gzip file itself). Returns
    {kind: urlset|sitemapindex|invalid, count, lastmods[], error}. Children are never fetched."""
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception as e:  # truncated gzip (we cap reads) still fails here; record it
            return {"kind": "invalid", "count": 0, "lastmods": [], "gzip": True,
                    "error": "gzip: {}".format(type(e).__name__)}
        gz = True
    else:
        gz = False
    if b"<!doctype html" in raw[:1024].lower() or b"<html" in raw[:1024].lower():
        return {"kind": "invalid", "count": 0, "lastmods": [], "gzip": gz, "error": "html body"}
    try:
        # ElementTree does not fetch external entities; the body is capped by the runner.
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return {"kind": "invalid", "count": 0, "lastmods": [], "gzip": gz,
                "error": "xml: {}".format(str(e)[:120])}
    kind = _localname(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        return {"kind": "invalid", "count": 0, "lastmods": [], "gzip": gz,
                "error": "root element <{}>".format(kind)}
    child = "url" if kind == "urlset" else "sitemap"
    count, lastmods = 0, []
    for el in root:
        if _localname(el.tag) != child:
            continue
        count += 1
        lm = None
        for sub in el:
            if _localname(sub.tag) == "lastmod" and (sub.text or "").strip():
                lm = sub.text.strip()
        lastmods.append(lm)
    return {"kind": kind, "count": count, "lastmods": lastmods, "gzip": gz, "error": None}


def test_m2(resp: dict, source: str):
    um = _unmeasured(resp, "sitemap")
    if um:
        um[1]["source"] = source
        return um
    raw = resp.get("raw")
    if raw is None:
        raw = (resp.get("body") or "").encode("utf-8")
    ev = {"url": resp.get("url"), "source": source, "status": resp.get("status"),
          "content_type": content_type_of(resp), "sha256": sha256(raw),
          "truncated": bool(resp.get("truncated"))}
    if resp.get("status") != 200:
        return "fail", ev
    p = parse_sitemap(raw)
    ev.update({"kind": p["kind"], "gzip": p.get("gzip"), "error": p["error"]})
    if p["kind"] == "urlset":
        ev["url_count"] = p["count"]
    elif p["kind"] == "sitemapindex":
        ev["child_count"] = p["count"]
    return ("pass" if p["kind"] in ("urlset", "sitemapindex") else "fail"), ev


def _lastmod_date(s):
    # W3C Datetime (sitemaps.org): YYYY, YYYY-MM, YYYY-MM-DD, or with a time part.
    if not s:
        return None
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", s.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
    except ValueError:
        return None


def test_m3(sitemap_resp: dict, today: date):
    """M3 — reported: lastmod coverage, newest and median lastmod, and the one-date-stamp flag."""
    if not sitemap_resp or sitemap_resp.get("status") != 200 or refused(sitemap_resp):
        return "na", {"reason": "no working sitemap (see M2)"}
    raw = sitemap_resp.get("raw") or (sitemap_resp.get("body") or "").encode("utf-8")
    p = parse_sitemap(raw)
    if p["kind"] not in ("urlset", "sitemapindex"):
        return "na", {"reason": "sitemap did not parse"}
    dates = [d for d in (_lastmod_date(x) for x in p["lastmods"]) if d]
    ev = {"kind": p["kind"], "entries": p["count"], "with_lastmod": len(dates),
          "share_with_lastmod": round(len(dates) / p["count"], 3) if p["count"] else 0.0}
    if p["kind"] == "sitemapindex":
        ev["note"] = "index: dates are the child sitemaps' lastmod, children not fetched"
    if dates:
        newest = max(dates)
        ordinals = sorted(d.toordinal() for d in dates)
        ev["newest"] = newest.isoformat()
        ev["median"] = date.fromordinal(int(statistics.median_low(ordinals))).isoformat()
        ev["newest_age_days"] = (today - newest).days
        top = max(set(dates), key=dates.count)
        share = dates.count(top) / len(dates)
        ev["one_date_share"] = round(share, 3)
        ev["one_date_flag"] = len(dates) >= 10 and share > 0.9
    return "reported", ev


def llms_shape(body: str) -> dict:
    """llmstxt.org shape: first non-blank line is an H1 ('# '), and the next non-blank line is a
    blockquote summary ('> '). The spec makes only the H1 mandatory; the catalog's pass rule asks
    for both."""
    lines = [ln for ln in (body or "").splitlines() if ln.strip()]
    h1 = bool(lines) and lines[0].startswith("# ")
    quote = h1 and len(lines) > 1 and lines[1].startswith("> ")
    return {"h1": h1, "summary_blockquote": quote}


def test_m4(resp: dict):
    um = _unmeasured(resp, "llms.txt")
    if um:
        return um
    body = resp.get("body") or ""
    ev = {"status": resp.get("status"), "content_type": content_type_of(resp),
          "first_8kb": body[:8192], "sha256": sha256(body)}
    if resp.get("status") != 200 or resp.get("offhost_redirect"):
        return "fail", ev
    if looks_like_html(body) or "text/html" in ev["content_type"]:
        ev["note"] = "HTML served at /llms.txt (soft 404)"
        return "fail", ev
    shape = llms_shape(body)
    ev.update(shape)
    if shape["h1"] and shape["summary_blockquote"]:
        return "pass", ev
    if shape["h1"]:
        ev["note"] = "H1 present, no '> ' summary right after it (valid per llmstxt.org, " \
                     "short of the catalog's pass rule)"
        return "partial", ev
    return "fail", ev


def test_m5(resp: dict):
    um = _unmeasured(resp, "llms-full.txt")
    if um:
        return um
    body = resp.get("body") or ""
    present = resp.get("status") == 200 and not looks_like_html(body)
    return "reported", {"status": resp.get("status"), "present": present,
                        "bytes": resp.get("bytes"), "content_type": content_type_of(resp)}


# ==================================================================================== HTML
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
SHELL_PHRASES = ("loading...", "loading…", "enable javascript", "javascript is required",
                 "requires javascript", "javascript must be enabled", "turn on javascript",
                 "javascript is disabled")
EMPTY_ROOT_RX = re.compile(
    r"<div[^>]*\bid=[\"'](?:root|app|__next|__nuxt|q-app)[\"'][^>]*>\s*</div>", re.I)
WORD_RX = re.compile(r"\w*[^\W\d_]\w*")


class PageParser(HTMLParser):
    """One pass over the homepage, collecting everything R, I, D, F1 and P need."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.title = ""
        self.h1 = 0
        self.headings = 0
        self.has_main = False
        self.landmarks = set()
        self.meta = {}             # name/property (lower) -> content (first wins)
        self.links = []            # [{"href", "text", "hreflang"}]
        self.alternates = []       # <link rel=alternate hreflang>
        self.link_tags = []        # every <link>: {"rel", "type", "href"} (C1, D2, R2)
        self.scripts = []          # script src values
        self.jsonld = []           # raw JSON-LD strings
        self.itemtypes = []
        self.text_all = []
        self.text_main = []
        self._skip = 0
        self._main = 0
        self._in_title = False
        self._in_jsonld = False
        self._jsonld_buf = []
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        if a.get("itemtype"):
            self.itemtypes.append(a["itemtype"])
        if a.get("role") in ("main", "navigation", "banner", "contentinfo", "search"):
            self.landmarks.add(a["role"])
            if a["role"] == "main":
                self.has_main = True
        if tag == "main":
            self.has_main = True
            self.landmarks.add("main")
            self._main += 1
        elif tag in ("nav", "header", "footer"):
            self.landmarks.add(tag)
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "")
        elif tag == "link":
            rel = a.get("rel", "").lower()
            self.link_tags.append({"rel": rel, "type": a.get("type", "").lower(),
                                   "href": a.get("href", "")})
            if "alternate" in rel and a.get("hreflang"):
                self.alternates.append({"hreflang": a["hreflang"], "href": a.get("href", "")})
        elif tag == "script":
            if a.get("src"):
                self.scripts.append(a["src"])
            if "ld+json" in a.get("type", "").lower():
                self._in_jsonld = True
                self._jsonld_buf = []
        elif tag == "a":
            self._anchor = {"href": a.get("href", ""), "text": [], "hreflang": a.get("hreflang")}
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings += 1
            if tag == "h1":
                self.h1 += 1
        if tag in SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "main" and self._main:
            self._main -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jsonld_buf))
            self._in_jsonld = False
        elif tag == "a" and self._anchor is not None:
            self._anchor["text"] = " ".join("".join(self._anchor["text"]).split())
            self.links.append(self._anchor)
            self._anchor = None

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.text_all.append(data)
        if self._main:
            self.text_main.append(data)
        if self._anchor is not None:
            self._anchor["text"].append(data)


def parse_page(html: str) -> PageParser:
    p = PageParser()
    try:
        p.feed(html or "")
        p.close()
    except Exception:  # HTMLParser is lenient; never let a malformed page kill the run
        pass
    if p._anchor is not None:
        p._anchor["text"] = " ".join("".join(p._anchor["text"]).split())
        p.links.append(p._anchor)
        p._anchor = None
    return p


def visible_text(p: PageParser, main_only: bool = False) -> str:
    parts = p.text_main if (main_only and p.has_main and p.text_main) else p.text_all
    return " ".join(" ".join(parts).split())


def homepage_gate(resp: dict):
    """Content tests read only the research-UA homepage. Returns (verdict, ev) when the body is
    not usable, or None when it is."""
    um = _unmeasured(resp, "homepage (research identity)")
    if um:
        return um
    if resp.get("status") != 200:
        return "fail", {"reason": "homepage status {}".format(resp.get("status"))}
    return None


# ---------------------------------------------------------------------------------- R tests
def test_r1(resp: dict, page: PageParser = None):
    g = homepage_gate(resp)
    if g:
        return g
    page = page or parse_page(resp.get("body"))
    text = visible_text(page, main_only=True)
    words = len(WORD_RX.findall(text))
    low_all = visible_text(page).lower()
    shell = [s for s in SHELL_PHRASES if s in low_all]
    if EMPTY_ROOT_RX.search(resp.get("body") or ""):
        shell.append("empty root div")
    ev = {"words": words, "scope": "main" if (page.has_main and page.text_main) else "body",
          "shell_phrases": shell, "text_1kb": text[:1024]}
    if words >= 150 and not shell:
        return "pass", ev
    if words >= 150 and shell:
        ev["note"] = "enough text, but a JS-shell phrase is present"
        return "partial", ev
    if not shell and page.headings and len(page.links) >= 1:
        ev["note"] = "short by design? under 150 words, but has headings and links"
        return "partial", ev
    return "fail", ev


def markdown_alternates(home_resp: dict, page: "PageParser" = None) -> list:
    """Markdown versions the homepage itself advertises, read from what was already fetched: a
    `Link:` header or `<link>` with rel=alternate and type text/markdown (llmstxt.org)."""
    out = []
    for target, params in link_header((home_resp or {}).get("headers") or {}):
        if "alternate" in params.get("rel", "").split() and "text/markdown" in params.get("type", ""):
            out.append({"href": target, "via": "Link header"})
    for t in (page.link_tags if page else []):
        if "alternate" in t["rel"].split() and "text/markdown" in t["type"]:
            out.append({"href": t["href"], "via": "<link>"})
    return out


def test_r2(accept_resp: dict, index_md_resp: dict, home_resp: dict = None, page=None):
    out = {}
    for name, r in (("accept_markdown", accept_resp), ("index_md", index_md_resp)):
        if unreachable(r) or refused(r):
            out[name] = {"status": (r or {}).get("status"), "markdown": False,
                         "note": "refused or unreachable"}
            continue
        ctype = content_type_of(r)
        md = r.get("status") == 200 and "text/markdown" in ctype and not looks_like_html(
            r.get("body") or "")
        out[name] = {"status": r.get("status"), "content_type": ctype, "markdown": md,
                     "vary": (r.get("headers") or {}).get("vary")}
    out["markdown_served"] = any(v.get("markdown") for v in out.values() if isinstance(v, dict))
    if home_resp is not None:
        out["advertised_alternates"] = markdown_alternates(home_resp, page)
    return "reported", out


def test_r3(resp: dict, page: PageParser = None):
    g = homepage_gate(resp)
    if g:
        return g
    page = page or parse_page(resp.get("body"))
    return "reported", {"lang": page.lang, "has_lang": bool(page.lang), "h1_count": page.h1,
                        "one_h1": page.h1 == 1, "has_main_or_landmark": bool(page.landmarks),
                        "landmarks": sorted(page.landmarks), "title": page.title.strip()[:300],
                        "has_title": bool(page.title.strip())}


# ---------------------------------------------------------------------------------- I tests
DEFAULT_DESCRIPTIONS = (
    "just another wordpress site", "just another wordpress weblog", "just another wordpress blog",
    "site description", "your site description", "description", "home", "default description",
)


def test_i1(resp: dict, page: PageParser = None):
    g = homepage_gate(resp)
    if g:
        return g
    page = page or parse_page(resp.get("body"))
    desc = " ".join((page.meta.get("description") or "").split())
    ev = {"description": desc[:500], "length": len(desc)}
    if not desc:
        return "fail", ev
    if desc.lower().strip(" .") in DEFAULT_DESCRIPTIONS:
        ev["note"] = "CMS default text"
        return "fail", ev
    if len(desc) < 50:
        ev["note"] = "present but under 50 characters"
        return "partial", ev
    return "pass", ev


def test_i2(resp: dict, page: PageParser = None):
    g = homepage_gate(resp)
    if g:
        return g
    page = page or parse_page(resp.get("body"))
    vals = {k: page.meta.get(k) for k in ("og:title", "og:description", "og:site_name")}
    n = sum(1 for v in vals.values() if v and v.strip())
    return ("pass" if n == 3 else "partial" if n else "fail"), {"values": vals, "present": n}


GOV_TYPES = {"governmentorganization", "governmentoffice", "city", "administrativearea", "state"}
GOV_NAME_RX = re.compile(
    r"\b(city|town|village|county|state) of\b|\bcounty\b|\bdepartment\b|\bdept\b|\boffice of\b|"
    r"\bauthority\b|\bcommission\b|\bboard of\b|\bborough\b|\bcouncil\b|\bgovernment\b|\bagency\b|"
    r"\bmayor\b|\bcommunity board\b", re.I)


def _jsonld_nodes(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _jsonld_nodes(x)
    elif isinstance(obj, dict):
        yield obj
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                yield from _jsonld_nodes(v)


def _types(node) -> list:
    t = node.get("@type")
    t = t if isinstance(t, list) else [t] if t else []
    return [str(x).rsplit("/", 1)[-1] for x in t]


def test_i3(resp: dict, page: PageParser = None):
    g = homepage_gate(resp)
    if g:
        return g
    page = page or parse_page(resp.get("body"))
    found, invalid = [], 0
    for block in page.jsonld:
        try:
            data = json.loads(block)
        except ValueError:
            invalid += 1
            continue
        for node in _jsonld_nodes(data):
            for t in _types(node):
                found.append({"type": t, "name": node.get("name") if isinstance(
                    node.get("name"), str) else None, "url": node.get("url") if isinstance(
                    node.get("url"), str) else None, "sameAs": node.get("sameAs"),
                    "via": "json-ld"})
    for it in page.itemtypes:
        for t in it.split():
            found.append({"type": t.rsplit("/", 1)[-1], "name": None, "url": None,
                          "sameAs": None, "via": "microdata"})
    ev = {"types": found[:40], "invalid_jsonld_blocks": invalid}
    gov = [f for f in found if f["type"].lower() in GOV_TYPES]
    org_gov_name = [f for f in found if f["type"].lower() == "organization" and f["name"]
                    and GOV_NAME_RX.search(f["name"])]
    if gov or org_gov_name:
        return "pass", ev
    if any(f["type"].lower() in ("organization", "website", "localbusiness", "webpage")
           for f in found):
        return "partial", ev
    return "fail", ev


def normalize_name(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(the|official website of|official site of|website|homepage|home)\b", " ", s)
    return " ".join(s.split())


def test_i4(resp: dict, registry_name: str = None, page: PageParser = None, i3_ev: dict = None):
    g = homepage_gate(resp)
    if g:
        return g
    page = page or parse_page(resp.get("body"))
    strings = {"title": page.title.strip()[:300],
               "meta_description": (page.meta.get("description") or "")[:300],
               "og_site_name": page.meta.get("og:site_name"),
               "og_title": page.meta.get("og:title"),
               "jsonld_name": next((t["name"] for t in (i3_ev or {}).get("types", [])
                                    if t.get("name")), None),
               "registry_name": registry_name}
    reg = normalize_name(registry_name)
    agree = {}
    if reg:
        for k in ("title", "og_site_name", "og_title", "jsonld_name"):
            v = normalize_name(strings.get(k))
            agree[k] = bool(v) and (reg in v or v in reg)
    return "reported", {"strings": strings, "registry_name_found_in": agree,
                        "names_agree": bool(agree) and any(agree.values())}


def test_i5(host: str, https_resp: dict, http_resp: dict, dotgov_hosts: set = None):
    """I5 — reported per item. CISA list membership is checked only when the operator passes a
    local copy (--dotgov-file); otherwise recorded as not checked."""
    h = (host or "").lower()
    cert = (https_resp or {}).get("peercert") or {}
    issuer = None
    for rdn in cert.get("issuer", ()) or ():
        for k, v in rdn:
            if k in ("organizationName", "commonName") and not issuer:
                issuer = v
    loc = ((http_resp or {}).get("headers") or {}).get("location") or ""
    redirect_https = bool(http_resp and http_resp.get("status") in (301, 302, 303, 307, 308)
                          and loc.lower().startswith("https://"))
    domain = h[4:] if h.startswith("www.") else h
    ev = {"dot_gov": h.endswith(".gov"),
          "ny_gov_or_nyc_gov": h.endswith("ny.gov") or h.endswith("nyc.gov"),
          "cisa_dotgov_list": (None if dotgov_hosts is None else
                               (domain in dotgov_hosts or h in dotgov_hosts)),
          "https_ok": bool(https_resp and https_resp.get("status") is not None),
          "cert_issuer": issuer, "cert_not_after": cert.get("notAfter"),
          "http_status": (http_resp or {}).get("status"),
          "http_location": loc or None, "http_redirects_to_https": redirect_https,
          "http_transport_error": (http_resp or {}).get("transport_error")}
    if dotgov_hosts is None:
        ev["cisa_note"] = "not checked (no --dotgov-file given)"
    return "reported", ev


# ---------------------------------------------------------------------------------- D test
def load_portals(path) -> dict:
    d = json.loads(open(path, encoding="utf-8").read())
    return {"hosts": set(h.lower() for h in d.get("hosts", [])),
            "host_suffixes": tuple(s.lower() for s in d.get("host_suffixes", [])),
            "url_prefixes": tuple(p.lower().rstrip("/") for p in d.get("url_prefixes", []))}


def _abs(base: str, href: str) -> str:
    try:
        return urllib.parse.urljoin(base, href)
    except ValueError:
        return ""


def test_d1(resp: dict, portals: dict, page: PageParser = None):
    g = homepage_gate(resp)
    if g:
        return g
    page = page or parse_page(resp.get("body"))
    base = resp.get("final_url") or resp.get("url") or ""
    hits = []
    for link in page.links:
        url = _abs(base, link["href"])
        u = urllib.parse.urlparse(url)
        host = (u.netloc or "").lower().split("@")[-1].split(":")[0]
        hostpath = (host + u.path).lower().rstrip("/")
        why = None
        if host and (host in portals["hosts"] or host.endswith(portals["host_suffixes"])):
            why = "portal host"
        elif host and any(hostpath.startswith(p) for p in portals["url_prefixes"]):
            why = "portal page"
        elif "open data" in (link.get("text") or "").lower() or "opendata" in (
                link.get("text") or "").lower().replace(" ", ""):
            why = "link text"
        if why:
            hits.append({"href": url[:300], "text": (link.get("text") or "")[:120], "why": why})
    return ("pass" if hits else "fail"), {"matches": hits[:20], "count": len(hits)}


# ---------------------------------------------------------------------------------- F tests
COPYRIGHT_RX = re.compile(
    r"(?:©|&copy;|\(c\)|copyright)\s*(?:\w+\s){0,3}?((?:19|20)\d{2})(?:\s*[-–—]\s*((?:19|20)\d{2}))?",
    re.I)
UPDATED_RX = re.compile(r"(?:last\s+)?updated\b[^0-9]{0,40}?(\d{1,2}[/.-]\d{1,2}[/.-]((?:19|20)\d{2})|"
                        r"(?:[A-Za-z]+\.?\s+\d{1,2},?\s+)((?:19|20)\d{2})|((?:19|20)\d{2})-\d{2}-\d{2})",
                        re.I)


def test_f1(resp: dict, today: date, page: PageParser = None):
    g = homepage_gate(resp)
    if g:
        return g
    page = page or parse_page(resp.get("body"))
    text = visible_text(page)
    years = []
    for m in COPYRIGHT_RX.finditer(text):
        years += [int(y) for y in m.groups() if y]
    upd = []
    for m in UPDATED_RX.finditer(text):
        upd += [int(y) for y in m.groups()[1:] if y]
    allyears = [y for y in years + upd if 1990 <= y <= today.year + 1]
    ev = {"copyright_years": sorted(set(years)), "updated_years": sorted(set(upd))}
    if not allyears:
        ev["note"] = "no year printed (reported 'none', not a fail)"
        return "na", ev
    newest = max(allyears)
    ev["newest"] = newest
    return ("pass" if newest >= today.year - 1 else "fail"), ev


def test_f2(resp: dict):
    if unreachable(resp):
        return "na", {"reason": "homepage unreachable"}
    h = resp.get("headers") or {}
    return "reported", {"last_modified": h.get("last-modified"), "date": h.get("date"),
                        "age": h.get("age")}


def f3_pick_links(resp: dict, page: PageParser = None, n: int = 10) -> list:
    """Deterministic: the first n distinct same-host http(s) links in document order."""
    page = page or parse_page(resp.get("body"))
    base = resp.get("final_url") or resp.get("url") or ""
    host = urllib.parse.urlparse(base).netloc.lower()
    out = []
    for link in page.links:
        url = _abs(base, link["href"]).split("#", 1)[0]
        u = urllib.parse.urlparse(url)
        if u.scheme in ("http", "https") and u.netloc.lower() == host and url not in out \
                and url.rstrip("/") != base.rstrip("/"):
            out.append(url)
        if len(out) >= n:
            break
    return out


def test_f3(link_results: list):
    """link_results: [{url, status, transport_error}]. Pass if ≤ 1 of the checked links is 4xx/5xx."""
    if not link_results:
        return "na", {"reason": "no same-host links found"}
    bad = [r for r in link_results if r.get("status") and r["status"] >= 400]
    ev = {"checked": len(link_results), "broken": len(bad),
          "links": [{"url": r["url"][:300], "status": r.get("status"),
                     "transport_error": r.get("transport_error")} for r in link_results]}
    return ("pass" if len(bad) <= 1 else "fail"), ev


def test_f4(resp: dict):
    um = _unmeasured(resp, "404 probe")
    if um:
        return um
    ev = {"status": resp.get("status"), "body_class": resp.get("body_class"),
          "final_url": resp.get("final_url"), "redirects": resp.get("redirects") or []}
    if resp.get("status") in (404, 410):
        return "pass", ev
    if resp.get("status") == 200:
        ev["note"] = "soft 404: an unknown path answered 200" + (
            " after a redirect" if resp.get("redirects") else "")
    return "fail", ev


# ---------------------------------------------------------------------------------- P tests
P1_SIGNALS = (
    # (platform, where, needle) — a needle found in that place is evidence, recorded verbatim.
    ("WordPress", "html", "/wp-content/"),
    ("WordPress", "html", "/wp-includes/"),
    ("Drupal", "html", "/sites/default/files"),
    ("Drupal", "header", "x-drupal-cache"),
    ("Drupal", "header", "x-drupal-dynamic-cache"),
    ("Sitecore", "html", "/-/media/"),
    ("CivicPlus", "html", "civicplus"),
    ("CivicPlus", "html", "/civicalerts.aspx"),
    ("CivicPlus (CivicEngage)", "html", "civicengage"),
    ("Granicus", "html", "granicus"),
    ("Revize", "html", "revize"),
    ("Municode", "html", "municode"),
    ("Squarespace", "html", "static1.squarespace.com"),
    ("Wix", "html", "static.wixstatic.com"),
    ("Joomla", "html", "/media/jui/"),
    ("Adobe Experience Manager", "html", "/etc.clientlibs/"),
)


def test_p1(resp: dict, page: PageParser = None):
    if unreachable(resp) or refused(resp) or resp.get("status") != 200:
        return "na", {"reason": "no readable homepage"}
    page = page or parse_page(resp.get("body"))
    html = (resp.get("body") or "").lower()
    headers = {k.lower(): v for k, v in (resp.get("headers") or {}).items()}
    hits = []
    gen = page.meta.get("generator")
    if gen:
        hits.append({"platform": gen, "signal": "meta generator"})
    for platform, where, needle in P1_SIGNALS:
        if (where == "html" and needle in html) or (where == "header" and needle in headers):
            hits.append({"platform": platform, "signal": "{}: {}".format(where, needle)})
    if headers.get("x-powered-by"):
        hits.append({"platform": headers["x-powered-by"], "signal": "header: x-powered-by"})
    platforms = sorted({h["platform"] for h in hits})
    return "reported", {"platforms": platforms, "signals": hits}


P2_VENDORS = (
    ("Legistar", ("legistar.com",)),
    ("Granicus", ("granicus.com",)),
    ("eCode360", ("ecode360.com",)),
    ("ArcGIS Hub", ("hub.arcgis.com", "opendata.arcgis.com")),
    ("ArcGIS", ("arcgis.com",)),
    ("Laserfiche", ("laserfiche.com", "/weblink/")),
    ("PrimeGov", ("primegov.com",)),
    ("BoardDocs", ("boarddocs.com",)),
    ("SeeClickFix", ("seeclickfix.com",)),
)


def test_p2(resp: dict, page: PageParser = None):
    if unreachable(resp) or refused(resp) or resp.get("status") != 200:
        return "na", {"reason": "no readable homepage"}
    page = page or parse_page(resp.get("body"))
    base = resp.get("final_url") or resp.get("url") or ""
    found = {}
    for link in page.links:
        url = _abs(base, link["href"]).lower()
        for vendor, needles in P2_VENDORS:
            if any(n in url for n in needles):
                found.setdefault(vendor, []).append(url[:300])
                break
    return "reported", {"vendors": sorted(found), "links": {k: v[:5] for k, v in found.items()}}


TRANSLATE_SIGNALS = ("translate.google.com/translate_a", "google_translate_element", "gtranslate",
                     "weglot", "localizejs", "translate.google")
# NYC Local Law 30 (2017) designated languages plus Spanish; names as they usually appear in the
# language itself on a language picker.
LANGUAGE_NAMES = ("español", "中文", "русский", "kreyòl", "kreyol", "한국어", "বাংলা", "العربية",
                  "polski", "français", "italiano", "اردو", "繁體中文", "简体中文", "ייִדיש", "יידיש")


def test_p3(resp: dict, page: PageParser = None):
    if unreachable(resp) or refused(resp) or resp.get("status") != 200:
        return "na", {"reason": "no readable homepage"}
    page = page or parse_page(resp.get("body"))
    html = (resp.get("body") or "").lower()
    widgets = [s for s in TRANSLATE_SIGNALS if s in html]
    lang_links = [l["text"] for l in page.links
                  if l.get("hreflang") or (l.get("text") or "").strip().lower() in LANGUAGE_NAMES]
    return "reported", {"translation_widget": bool(widgets), "widget_signals": widgets,
                        "hreflang_alternates": len(page.alternates),
                        "language_links": len(lang_links), "language_link_texts": lang_links[:20]}


def test_p4(resp: dict, page: PageParser = None):
    """Count of distinct third-party script hosts. Results are private (git-ignored); the host
    list is kept for review, and only the count is meant for publication (tests.md P4)."""
    if unreachable(resp) or refused(resp) or resp.get("status") != 200:
        return "na", {"reason": "no readable homepage"}
    page = page or parse_page(resp.get("body"))
    base = resp.get("final_url") or resp.get("url") or ""
    own = urllib.parse.urlparse(base).netloc.lower()
    hosts = set()
    for src in page.scripts:
        h = urllib.parse.urlparse(_abs(base, src)).netloc.lower()
        if h and h != own:
            hosts.add(h)
    return "reported", {"third_party_script_hosts": len(hosts), "hosts_private": sorted(hosts)}


# =========================================================================== catalog 0.2 tests
# C1 and D2 read only what the host run already fetched (zero requests). D3–D7 each cost a few
# documented requests, full tier only; the request plan lives in probe_bot_access.host_plan.
# Every path below is one its spec documents (docs/ai-readability/tests.md § "Data endpoints").

def link_header(headers: dict) -> list:
    """Parse an HTTP `Link:` header (RFC 8288) into [(target, {param: value})]."""
    raw = (headers or {}).get("link") or ""
    out = []
    for m in re.finditer(r"<([^>]*)>((?:\s*;\s*[^;,]+=(?:\"[^\"]*\"|[^;,]*))*)", raw):
        params = {}
        for pm in re.finditer(r";\s*([^=;,\s]+)\s*=\s*(\"[^\"]*\"|[^;,]*)", m.group(2)):
            params[pm.group(1).lower()] = pm.group(2).strip().strip('"').lower() \
                if pm.group(1).lower() in ("rel", "type") else pm.group(2).strip().strip('"')
        out.append((m.group(1).strip(), params))
    return out


# --------------------------------------------------------------------------- C1 website software
WP_API_REL = "https://api.w.org/"   # WordPress REST API handbook, "Discovery"


def wp_api_root(resp: dict, page: PageParser = None):
    """The REST API root a WordPress site advertises (Link header or <link>, rel
    https://api.w.org/), resolved against the page, else None. Read from the fetched homepage."""
    base = (resp or {}).get("final_url") or (resp or {}).get("url") or ""
    for target, params in link_header((resp or {}).get("headers") or {}):
        if WP_API_REL in params.get("rel", "").split():
            return _abs(base, target)
    for t in (page.link_tags if page else []):
        if WP_API_REL in t["rel"].split():
            return _abs(base, t["href"])
    return None


def test_c1(resp: dict, page: PageParser = None, gsa_cms: str = None):
    """C1 — website software. The GSA Site Scanning `cms` value, when the address list carries
    one, is kept as is. Only a gap is filled, and only from the homepage already fetched (meta
    generator, known asset paths, the WordPress REST link): no extra request."""
    gsa = (gsa_cms or "").strip() or None
    ev = {"gsa_cms": gsa, "cms": gsa, "source": "gsa" if gsa else None,
          "homepage_platforms": [], "homepage_signals": []}
    if unreachable(resp) or refused(resp) or (resp or {}).get("status") != 200:
        ev["homepage_note"] = "no readable homepage"
        return "reported", ev
    page = page or parse_page(resp.get("body"))
    _, p1 = test_p1(resp, page)
    platforms, signals = list(p1.get("platforms") or []), list(p1.get("signals") or [])
    if wp_api_root(resp, page):
        signals.append({"platform": "WordPress", "signal": "REST API link rel=https://api.w.org/"})
        if "WordPress" not in platforms:
            platforms = sorted(platforms + ["WordPress"])
    ev["homepage_platforms"], ev["homepage_signals"] = platforms, signals
    if not gsa and platforms:
        ev["cms"], ev["source"] = ", ".join(platforms), "homepage"
    return "reported", ev


def is_wordpress(c1_ev: dict) -> bool:
    return any("wordpress" in (p or "").lower() for p in
               (c1_ev.get("homepage_platforms") or []) + [c1_ev.get("gsa_cms") or ""])


# --------------------------------------------------------------- D2 data and API links (pass 1)
# Patterns that sort a link into a kind. These are reading aids for a reported test, not a
# claim that the link works: D2 never follows a link (pass 2 does that, where documented).
D2_PATTERNS = (
    ("catalog", re.compile(r"/data\.json$|/api/catalog/v1|/api/feed/dcat|/api/3/action/|"
                           r"/\.well-known/api-catalog|/api/views/metadata/v1", re.I)),
    ("gis_service", re.compile(r"/arcgis/rest/services|/(?:map|feature|image)server\b|"
                               r"[?&]service=w[mf]s|\.hub\.arcgis\.com|\.opendata\.arcgis\.com", re.I)),
    ("api", re.compile(r"(?:^|[/.])api(?:[/.]|$)|/wp-json/|swagger|openapi|graphql", re.I)),
    ("developer", re.compile(r"(?:^|[/.])developers?(?:[/.]|$)", re.I)),
    ("feed", re.compile(r"/feed/?$|/rss(?:\.xml)?/?$|/atom(?:\.xml)?/?$|\.rss$", re.I)),
)
D2_TEXT = (("open_data", re.compile(r"\bopen\s*data\b", re.I)),
           ("api", re.compile(r"\bAPIs?\b")),
           ("developer", re.compile(r"\bdevelopers?\b", re.I)))
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json")


def _d2_kinds(url: str, text: str, portals: dict) -> list:
    kinds = []
    u = urllib.parse.urlparse(url)
    host = (u.netloc or "").lower().split("@")[-1].split(":")[0]
    hostpath = (host + u.path).lower().rstrip("/")
    if host and portals and (host in portals["hosts"] or host.endswith(portals["host_suffixes"])
                             or any(hostpath.startswith(p) for p in portals["url_prefixes"])):
        kinds.append("open_data")
    for kind, rx in D2_PATTERNS:
        if rx.search(u.netloc + u.path + ("?" + u.query if u.query else "")) and kind not in kinds:
            kinds.append(kind)
    for kind, rx in D2_TEXT:
        if rx.search(text or "") and kind not in kinds:
            kinds.append(kind)
    return kinds


def test_d2(resp: dict, page: PageParser = None, portals: dict = None, llms_resp: dict = None):
    """D2 — links to open data, APIs, developer pages, catalogs, feeds and GIS services in files
    already fetched: the homepage's anchors and <link> tags, its Link header, and llms.txt.
    Reported (no pass rule): it is pass 1 of the data-endpoint census."""
    g = homepage_gate(resp)
    if g:
        return g
    page = page or parse_page(resp.get("body"))
    base = resp.get("final_url") or resp.get("url") or ""
    found, seen = [], set()

    def add(url, text, via, extra_kinds=()):
        url = (url or "").strip()
        if not url or url in seen or not url.lower().startswith(("http://", "https://")):
            return
        kinds = list(extra_kinds) + [k for k in _d2_kinds(url, text, portals) if k not in extra_kinds]
        if kinds:
            seen.add(url)
            found.append({"url": url[:300], "text": (text or "")[:120], "kinds": kinds, "via": via})

    for link in page.links:
        add(_abs(base, link["href"]), link.get("text"), "a")
    for t in page.link_tags:
        extra = ("feed",) if t["type"] in FEED_TYPES else (
            ("api",) if WP_API_REL in t["rel"].split() else ())
        add(_abs(base, t["href"]), t["rel"], "link", extra)
    for target, params in link_header(resp.get("headers") or {}):
        extra = ("api",) if WP_API_REL in params.get("rel", "").split() else ()
        add(_abs(base, target), params.get("rel", ""), "Link header", extra)
    if llms_resp and llms_resp.get("status") == 200 and not refused(llms_resp) \
            and not looks_like_html(llms_resp.get("body") or ""):
        for m in re.finditer(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", llms_resp.get("body") or ""):
            add(m.group(2), m.group(1), "llms.txt")
    counts = {}
    for f in found:
        for k in f["kinds"]:
            counts[k] = counts.get(k, 0) + 1
    return "reported", {"count": len(found), "by_kind": counts, "links": found[:60]}


# ---------------------------------------------------------------- D3 WordPress REST index
def test_d3(resp: dict):
    """D3 — the WordPress REST API index, asked only when C1 says WordPress. Reported:
    `rest_index` is true when the root answers 200 with JSON carrying `namespaces` (the handbook:
    "The index of the API exposes the namespaces item")."""
    um = _unmeasured(resp, "WordPress REST index")
    if um:
        return um
    ev = {"url": resp.get("url"), "final_url": resp.get("final_url"), "status": resp.get("status"),
          "content_type": content_type_of(resp), "rest_index": False}
    if resp.get("status") == 200:
        try:
            doc = json.loads(resp.get("body") or "")
        except ValueError:
            doc = None
        if isinstance(doc, dict) and isinstance(doc.get("namespaces"), list):
            ev.update(rest_index=True, namespaces=doc["namespaces"][:50],
                      name=doc.get("name") if isinstance(doc.get("name"), str) else None,
                      routes=len(doc["routes"]) if isinstance(doc.get("routes"), dict) else None)
    return "reported", ev


# ---------------------------------------------------------------- D4 RFC 9727 api-catalog
API_CATALOG_PATH = "/.well-known/api-catalog"            # RFC 9727 §2
LINKSET_TYPE = "application/linkset+json"                # RFC 9727 §4.2
RFC9727_PROFILE = "https://www.rfc-editor.org/info/rfc9727"


def test_d4(resp: dict):
    """D4 — RFC 9727 API catalog at /.well-known/api-catalog, on every site. Reported:
    `present` = 200 with a Linkset (top-level `linkset` array); `media_type_ok` = served as
    application/linkset+json (a MUST in §4.2); `items` = links with relation `item` (§3.1)."""
    um = _unmeasured(resp, "api-catalog")
    if um:
        return um
    ctype = content_type_of(resp)
    ev = {"status": resp.get("status"), "content_type": ctype, "final_url": resp.get("final_url"),
          "present": False, "media_type_ok": LINKSET_TYPE in ctype,
          "profile_rfc9727": RFC9727_PROFILE in ctype}
    if resp.get("status") != 200:
        return "reported", ev
    try:
        doc = json.loads(resp.get("body") or "")
    except ValueError:
        doc = None
    if isinstance(doc, dict) and isinstance(doc.get("linkset"), list):
        items, rels = 0, set()
        for ctx in doc["linkset"]:
            if isinstance(ctx, dict):
                for rel, targets in ctx.items():
                    if rel == "anchor":
                        continue
                    rels.add(rel)
                    if rel == "item" and isinstance(targets, list):
                        items += len(targets)
        ev.update(present=True, items=items, relations=sorted(rels)[:30])
    else:
        ev["note"] = "200 but not a Linkset JSON document"
    return "reported", ev


# --------------------------------------------------------------------- D5–D7 portal tests
# Only for addresses whose kind is `data portal`. The platform is read from the homepage already
# fetched (or the address list's own `platform` value); each platform's catalog is asked for at
# the path its vendor documents, and nowhere else. An unrecognized platform is not guessed at.
PORTAL_PLATFORMS = ("socrata", "arcgis_hub", "ckan")
SOCRATA_DISCOVERY = "https://api.us.socrata.com/api/catalog/v1"   # North American domains


def registry_platform(text: str):
    """The portal registry's own `platform` words (BetaNYC/ny-data-registry-mcp), mapped only
    where they name one of PORTAL_PLATFORMS outright: Socrata (not retired), ArcGIS Hub, CKAN.
    ArcGIS Enterprise "Sites", ArcGIS Online apps and ArcGIS Server are not Hub, so they map to
    nothing: Esri documents the DCAT-US feed for Hub sites."""
    t = (text or "").strip().lower()
    if not t or "retired" in t:
        return None
    if t.startswith("socrata"):
        return "socrata"
    if t.startswith("arcgis hub") or t == "arcgis online / arcgis hub":
        return "arcgis_hub"
    if t.startswith("ckan"):
        return "ckan"
    return None


def portal_platform(host: str, resp: dict, page: PageParser = None, hint: str = None) -> dict:
    """{platform, signal}. platform is None when nothing on the homepage names one."""
    h = (hint or "").strip().lower().replace(" ", "_")
    if h in PORTAL_PLATFORMS:
        return {"platform": h, "signal": "address list"}
    named = registry_platform(hint)
    if named:
        return {"platform": named, "signal": "portal registry: " + hint.strip()[:80]}
    host = (host or "").lower()
    if host.endswith((".hub.arcgis.com", ".opendata.arcgis.com")):
        return {"platform": "arcgis_hub", "signal": "host name"}
    if unreachable(resp) or refused(resp) or (resp or {}).get("status") != 200:
        return {"platform": None, "signal": "no readable homepage"}
    page = page or parse_page(resp.get("body"))
    gen = (page.meta.get("generator") or "").lower()
    html = (resp.get("body") or "").lower()
    if gen.startswith("ckan"):
        return {"platform": "ckan", "signal": "meta generator: " + gen[:60]}
    if "arcgis hub" in gen:
        return {"platform": "arcgis_hub", "signal": "meta generator: " + gen[:60]}
    if "socrata" in html:
        return {"platform": "socrata", "signal": "homepage names socrata"}
    return {"platform": None, "signal": "no platform named on the homepage"}


def portal_catalog_url(platform: str, host: str):
    """The documented machine-readable catalog for a platform, on the portal's own host:
      socrata    /api/views/metadata/v1?limit=1&page=1   ("accessible on every Socrata domain";
                 list form "list all metadata for all public assets on a domain")
      arcgis_hub /api/feed/dcat-us/1.1.json             (Esri, "Federate data")
      ckan       /api/3/action/package_list?limit=1     (CKAN API guide)"""
    base = "https://{}".format(host)
    return {"socrata": base + "/api/views/metadata/v1?limit=1&page=1",
            "arcgis_hub": base + "/api/feed/dcat-us/1.1.json",
            "ckan": base + "/api/3/action/package_list?limit=1"}.get(platform)


def dataset_lookup_url(platform: str, host: str):
    """Socrata's metadata list has no documented response schema, so the dataset page comes from
    the Discovery API, whose `results[].permalink` is documented. Other platforms need nothing
    more: the catalog itself names a dataset."""
    if platform == "socrata":
        return SOCRATA_DISCOVERY + "?" + urllib.parse.urlencode({"domains": host, "limit": 1})
    return None


def _json(resp):
    try:
        return json.loads((resp or {}).get("body") or "")
    except ValueError:
        return None


def test_d5(platform: str, resp: dict):
    """D5 — a machine-readable catalog at the platform's documented path.
    pass: 200 and the documented shape (socrata: JSON; arcgis_hub: DCAT-US `conformsTo` and a
    `dataset` array; ckan: `success` true and a `result` array). fail: answered but not that.
    blocked / na as everywhere. na also when the platform was not recognized."""
    if not platform:
        return "na", {"reason": "platform not recognized from the homepage; no documented "
                                "catalog path to try"}
    um = _unmeasured(resp, "portal catalog")
    if um:
        return um
    doc = _json(resp)
    ev = {"platform": platform, "url": resp.get("url"), "status": resp.get("status"),
          "content_type": content_type_of(resp), "json": doc is not None}
    if resp.get("status") != 200 or doc is None:
        return "fail", ev
    if platform == "socrata":
        ok = isinstance(doc, (list, dict))
        ev["entries"] = len(doc) if isinstance(doc, list) else None
    elif platform == "arcgis_hub":
        ok = isinstance(doc, dict) and isinstance(doc.get("dataset"), list) and bool(doc.get("conformsTo"))
        ev.update(conforms_to=doc.get("conformsTo") if isinstance(doc, dict) else None,
                  datasets=len(doc["dataset"]) if ok else None)
    else:
        ok = isinstance(doc, dict) and doc.get("success") is True and isinstance(doc.get("result"), list)
        ev["datasets"] = len(doc["result"]) if ok else None
    return ("pass" if ok else "fail"), ev


def dataset_page_url(platform: str, host: str, catalog_resp: dict, lookup_resp: dict = None):
    """One dataset's web page, from a documented field: DCAT-US `dataset[0].landingPage`
    (arcgis_hub), Discovery `results[0].permalink` (socrata), CKAN `/dataset/<name>` for
    `result[0]` of package_list. None when the response does not name one."""
    if platform == "arcgis_hub":
        doc = _json(catalog_resp)
        ds = doc.get("dataset") if isinstance(doc, dict) else None
        if isinstance(ds, list) and ds and isinstance(ds[0], dict):
            lp = ds[0].get("landingPage")
            return lp if isinstance(lp, str) and lp.startswith(("http://", "https://")) else None
    elif platform == "socrata":
        doc = _json(lookup_resp)
        res = doc.get("results") if isinstance(doc, dict) else None
        if isinstance(res, list) and res and isinstance(res[0], dict):
            pl = res[0].get("permalink")
            return pl if isinstance(pl, str) and pl.startswith(("http://", "https://")) else None
    elif platform == "ckan":
        doc = _json(catalog_resp)
        res = doc.get("result") if isinstance(doc, dict) else None
        if isinstance(res, list) and res and isinstance(res[0], str) and res[0]:
            return "https://{}/dataset/{}".format(host, urllib.parse.quote(res[0], safe=""))
    return None


def test_d6(url: str, resp: dict, page: PageParser = None):
    """D6 — one dataset page readable without JavaScript: R1's rule on the dataset page."""
    if not url:
        return "na", {"reason": "no dataset page named by the catalog"}
    verdict, ev = test_r1(resp, page)
    ev = dict(ev, dataset_url=url)
    return verdict, ev


def test_d7(per_identity: dict, identity_classes: dict):
    """D7 — the catalog reached by each crawler name: W4's rule per identity, and W4's
    all-AI aggregate over them. per_identity: {key: response}."""
    verdicts = {k: w4_identity_verdict(r) for k, r in per_identity.items()}
    agg = w4_aggregate({k: v[0] for k, v in verdicts.items()}, identity_classes)
    return verdicts, agg


# ==================================================================================== scoring
ABHI_ROWS = (
    ("Has robots.txt", "W1"),
    ("Names AI crawlers", "W2"),
    ("Homepage reachable by an agent", "W4"),
    ("Sitemap listed in robots.txt", "M1"),
    ("Working sitemap.xml", "M2"),
    ("Has llms.txt", "M4"),
    ("Text readable without JavaScript", "R1"),
    ("Meta description", "I1"),
    ("Open Graph tags", "I2"),
    ("Structured data (schema.org)", "I3"),
    ("Open data linked from the homepage", "D1"),
    ("No stale or broken content", "F1+F3"),
)
POINTS = {"pass": 1.0, "partial": 0.5, "fail": 0.0, "blocked": 0.0, "na": 0.0}
W4_AGGREGATE_IDENTITY = "all_ai"


def host_row_verdicts(rows: list) -> dict:
    """{abhi_test_id: verdict} for one host's result rows."""
    by = {}
    for r in rows:
        if r["test"] in ("W4", "D7"):
            if r["identity"] == W4_AGGREGATE_IDENTITY:
                by[r["test"]] = r["verdict"]
        elif r["test"] not in by:
            by[r["test"]] = r["verdict"]
    f1, f3 = by.get("F1"), by.get("F3")
    if f3 is None:                       # core tier: F1 alone decides (partial evidence)
        by["F1+F3"] = f1 if f1 is not None else "na"
    elif f1 == "pass" and f3 == "pass":
        by["F1+F3"] = "pass"
    elif "blocked" in (f1, f3):
        by["F1+F3"] = "blocked"
    elif "fail" in (f1, f3):
        by["F1+F3"] = "fail"
    elif "pass" in (f1, f3):             # one passed, the other could not be measured
        by["F1+F3"] = "partial"
    else:
        by["F1+F3"] = "na"
    return by


def abhi_score(verdicts: dict) -> float:
    return sum(POINTS.get(verdicts.get(test, "na"), 0.0) for _, test in ABHI_ROWS)


def betanyc_view(verdicts: dict) -> dict:
    """Blocked kept separate: score over the rows we could actually measure."""
    measured = [t for _, t in ABHI_ROWS if verdicts.get(t) in ("pass", "partial", "fail")]
    score = sum(POINTS[verdicts[t]] for t in measured)
    return {"score": score, "measured_rows": len(measured),
            "blocked_rows": sum(1 for _, t in ABHI_ROWS if verdicts.get(t) == "blocked"),
            "na_rows": sum(1 for _, t in ABHI_ROWS if verdicts.get(t, "na") == "na")}


def summarize(rows: list, review_count: int, meta: dict) -> str:
    """Render summary.md from results rows (counts per test, Abhi-12 view, BetaNYC view)."""
    from collections import Counter, defaultdict
    by_test = defaultdict(Counter)
    by_host = defaultdict(list)
    for r in rows:
        key = r["test"] if r["test"] not in ("W4", "D7") else (
            r["test"] + " (all AI)" if r["identity"] == W4_AGGREGATE_IDENTITY
            else r["test"] + " · " + r["identity"])
        by_test[key][r["verdict"]] += 1
        by_host[r.get("page_url") or r["host"]].append(r)   # a --deep-pages row is per page
    cols = ("pass", "partial", "fail", "blocked", "na", "reported")
    lines = ["---", "title: AI-readability run summary — {}".format(meta.get("run_id")),
             "created: {}".format(meta.get("date")), "type: run-summary", "status: private",
             "tags: [ai-readability, run]", "---", "",
             "# AI-readability run — {}".format(meta.get("run_id")), "",
             "- **Catalog version:** {}".format(CATALOG_VERSION),
             "- **Git SHA:** {}".format(meta.get("git_sha")),
             "- **Tier:** {} · **Vantage:** {} · **Machine:** {}".format(
                 meta.get("tier"), meta.get("vantage"), meta.get("machine")),
             "- **Started:** {} · **Ended:** {}".format(meta.get("started"), meta.get("ended")),
             "- **{}:** {} · **Requests:** {} · **Review rows:** {}".format(
                 "Pages" if any(r.get("page_url") for r in rows) else "Hosts",
                 len(by_host), meta.get("requests"), review_count), "",
             "## Verdict counts per test", "",
             "| test | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for t in sorted(by_test):
        lines.append("| {} | {} |".format(t, " | ".join(str(by_test[t].get(c, 0)) for c in cols)))
    lines += ["", "## Abhi-12 view (Pass 1 · Partial 0.5 · Fail 0 · Blocked 0, out of 12)", "",
              "Rebuilds the 12 rows of Abhi Nemani's scorecard. In the core tier the last row "
              "(stale or broken) rests on F1 alone, so it is partial evidence.", "",
              "| row | test | pass | partial | fail | blocked | na |", "|---|---|---|---|---|---|---|"]
    host_verdicts = {h: host_row_verdicts(rs) for h, rs in by_host.items()}
    for label, test in ABHI_ROWS:
        c = Counter(v.get(test, "na") for v in host_verdicts.values())
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            label, test, c["pass"], c["partial"], c["fail"], c["blocked"], c["na"]))
    scores = sorted(abhi_score(v) for v in host_verdicts.values())
    if scores:
        lines += ["", "Hosts: {} · mean score {:.2f} / 12 · median {:.1f} · min {:.1f} · max {:.1f}".format(
            len(scores), sum(scores) / len(scores), statistics.median(scores), scores[0], scores[-1])]
    lines += ["", "## BetaNYC view (Blocked kept separate from Fail)", "",
              "Score counts only the rows we could measure. \"The edge refused us\" and \"the page "
              "is bad\" have different owners.", "",
              "| host | score | measured rows | blocked rows | na rows | W5 divergence |",
              "|---|---|---|---|---|---|"]
    for h in sorted(host_verdicts):
        bv = betanyc_view(host_verdicts[h])
        w5 = next((r for r in by_host[h] if r["test"] == "W5"), None)
        div = ", ".join((w5 or {}).get("evidence", {}).get("divergent", []) or []) or "—"
        lines.append("| {} | {:.1f} | {} | {} | {} | {} |".format(
            h, bv["score"], bv["measured_rows"], bv["blocked_rows"], bv["na_rows"], div))
    lines += ["", "Results are private (git-ignored). Review list: `review.jsonl`.", ""]
    return "\n".join(lines)
