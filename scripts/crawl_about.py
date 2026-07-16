"""crawl_about.py — the registry's ONE networked component: politely fetch each entity's about
page and extract its self-description into data/descriptions.json.

This is issue #1 phase 5. Descriptions are the AGENCY'S OWN WORDS — the crawler NEVER summarizes,
paraphrases, or generates. It stores the extracted about-page text verbatim (whitespace/boilerplate
trimmed only). A downstream consumer (the BetaNYC workspace's team/contacts Mission drafting) reads
descriptions.json as a file; this crawler is the boundary that produces it.

CRAWL SET: data/crawl_targets.json (produced offline by crawl_targets.py — never re-derived here).

DISCOVERY (bounded, <= MAX_FETCHES_PER_ENTITY fetches per entity, homepage included):
  - path-probe: deterministic about-URL guesses from the start URL. For nyc.gov `/site/<slug>/`
    subsites the about page is path-relative (`/site/<slug>/about/about.page`); generic sites get
    `<dir>/about`, apex `/about`, `/about-us`. (method = "path-probe")
  - link-scan: parse the homepage for <a> whose visible text matches about / mission / who-we-are /
    our-agency and follow those. (method = "link-scan")

ETIQUETTE (non-negotiable — this is the only code in the repo that touches the network):
  - Identified User-Agent naming the repo + contact (USER_AGENT below).
  - robots.txt honored per host (stdlib urllib.robotparser). A Disallow is recorded as
    `robots_disallowed` and the page is NEVER fetched — never bypassed.
  - >= MIN_HOST_DELAY_S between requests to the SAME host.
  - Timeout per request; ONE retry with backoff on 5xx / timeout.
  - HTTP 429 -> back off hard (HARD_BACKOFF_S) and record the entity as fetch_failed.
  - WAF circuit-breaker: after CONSEC_403_TRIP consecutive 403s from a host (nyc.gov sits behind
    Akamai and is known to 403 automation), the host is abandoned and its remaining targets are
    recorded fetch_failed with a host_waf_blocked note — we record and report, never fight the WAF.

OUTPUT: data/descriptions.json, keyed by entity id:
  { "<id>": {text, source_url, fetched_at, method, status, truncated} }  on success
  { "<id>": {text: null, source_url, fetched_at, method: null, status,   truncated: false} } on
  failure, where `status` is one of the FAILURE reasons:
    no_url | robots_disallowed | fetch_failed | no_about_found | extraction_empty
Stored text is capped at MAX_TEXT_CHARS (truncated flag set). Idempotent + resumable: an entity
already present in descriptions.json is skipped unless --force; the file is rewritten after each
entity so an interrupted run keeps its progress.

ACCESS GATE: refuses to fetch unless invoked with --operator-authorized (mirrors the
run_harvest_live.py --operator-authorized pattern; configured != authorized). Without the flag it
prints the crawl plan and exits 0 without touching the network.

Pure functions (discover_about_candidates, find_about_links, extract_main_text, robots_allows,
truncate) are unit-tested with in-memory HTML/robots fixtures — NO test touches the network.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import time
import urllib.error
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = ROOT / "data" / "crawl_targets.json"
OUT = ROOT / "data" / "descriptions.json"

# --- etiquette / budget constants (module-level so tests and operators can see them) ---
USER_AGENT = ("ny-gov-web-registry crawler; "
              "https://github.com/BetaNYC/ny-gov-web-registry; noel@beta.nyc")
MIN_HOST_DELAY_S = 1.5      # >= 1.5s between requests to the same host
GLOBAL_PACE_S = 0.3        # modest global pacing between any two requests
REQUEST_TIMEOUT_S = 20
RETRY_BACKOFF_S = 3.0      # backoff before the single retry on 5xx / timeout
HARD_BACKOFF_S = 60.0      # 429: back off hard (matches the workspace's IA 429 practice)
CONSEC_403_TRIP = 4        # abandon a host after this many consecutive 403s (WAF circuit-breaker)
MAX_FETCHES_PER_ENTITY = 4  # homepage + up to 3 about-page candidates
MAX_TEXT_CHARS = 5000      # cap stored description; set `truncated` when hit

FAILURE_REASONS = {"no_url", "robots_disallowed", "fetch_failed", "no_about_found", "extraction_empty"}

# Anchor text that signals an "about"-type link during the homepage link-scan.
_ABOUT_TEXT_HINTS = ("about", "mission", "who we are", "who-we-are", "our agency",
                     "about us", "about the", "overview", "what we do")
# Tags whose text is boilerplate/chrome, never the entity's descriptive prose.
_SKIP_TEXT_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript",
                   "form", "button", "svg", "select", "option"}
# Block tags whose text we collect as candidate description prose.
_BLOCK_TAGS = {"p", "h1", "h2", "h3", "li", "blockquote"}


# --------------------------------------------------------------------------- pure helpers

def truncate(text: str, limit: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    """Trim whitespace and cap length. Returns (text, truncated)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True


def discover_about_candidates(start_url: str) -> list[str]:
    """Deterministic about-page URL guesses for `start_url`, best-first, deduped.

    Handles the dominant nyc.gov `/site/<slug>/` FSE convention (about at
    `/site/<slug>/about/about.page`) plus generic directory/apex probes. Pure — no fetch."""
    p = urlparse(start_url)
    if not p.scheme or not p.netloc:
        return []
    origin = f"{p.scheme}://{p.netloc}"
    path = p.path or "/"
    candidates: list[str] = []

    # nyc.gov /site/<slug>/ subsites (and /content/<slug>/): about is path-relative to the subsite.
    for marker in ("/site/", "/content/"):
        if p.netloc.endswith("nyc.gov") and marker in path:
            slug = path.split(marker, 1)[1].split("/", 1)[0]
            if slug:
                base = f"{origin}{marker}{slug}"
                candidates += [f"{base}/about/about.page", f"{base}/about.page",
                               f"{base}/about/index.page", f"{base}/about-us.page"]
            break

    # Generic directory-relative probes (directory of the start path).
    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    if directory and directory != "":
        dbase = f"{origin}{directory}"
        candidates += [f"{dbase}/about", f"{dbase}/about-us"]

    # Apex probes.
    candidates += [f"{origin}/about", f"{origin}/about-us", f"{origin}/about.page",
                   f"{origin}/about/about.page"]

    # Dedupe, preserve order, drop the start URL itself.
    seen, out = {start_url.rstrip("/")}, []
    for c in candidates:
        key = c.rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


class _LinkFinder(HTMLParser):
    """Collect (href, visible_text) for <a> elements, so we can score about-type links."""

    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._buf: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._buf).split())))
            self._href = None
            self._buf = []


def _about_link_score(hint: str, last_seg: str) -> int:
    """Confidence that an anchor points at the entity's OWN about page (not a program page whose
    path merely contains 'about', e.g. '/About-Energy-Management/...'). Higher = better; 0 = skip."""
    score = 0
    if hint in ("about", "about us", "about-us", "who we are", "our mission"):
        score += 6
    elif hint.startswith("about") and len(hint.split()) <= 5:  # "About NYC Health + Hospitals"
        score += 4
    elif any(h in hint for h in _ABOUT_TEXT_HINTS):
        score += 1
    if last_seg in ("about", "about-us", "aboutus", "about.page", "about-us.page"):
        score += 5
    elif last_seg.startswith(("about.", "about-us")):
        score += 3
    elif last_seg.startswith("about"):
        score += 1
    if "mission" in last_seg or "who-we-are" in last_seg:
        score += 2
    return score


def find_about_links(html: str, base_url: str) -> list[str]:
    """Absolute about-candidate URLs discovered by link-scanning the homepage, ranked best-first.

    Ranking is by (path depth ASC, score DESC): a shallow generic '/about' beats a deep program
    page like '/PutEnergyToWork/About-Energy-Management/...' even though both contain 'about'.
    Deduped, same-site only (never wanders off-host). Pure — no fetch."""
    finder = _LinkFinder()
    try:
        finder.feed(html)
    except Exception:  # noqa: BLE001 — malformed HTML must never crash the crawl
        pass
    base_host = urlparse(base_url).netloc.lower()
    scored: list[tuple[int, int, str]] = []  # (depth, -score, url)
    seen = set()
    for href, text in finder.links:
        if not href:
            continue
        absolute = urljoin(base_url, href)
        pa = urlparse(absolute)
        if pa.scheme not in ("http", "https"):
            continue
        # stay on-site: same host or a subdomain of the base host
        if not (pa.netloc.lower() == base_host or pa.netloc.lower().endswith("." + base_host)):
            continue
        segs = [s for s in pa.path.split("/") if s]
        last_seg = segs[-1].lower() if segs else ""
        score = _about_link_score(text.lower().strip(), last_seg)
        if score <= 0:
            continue
        key = absolute.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        scored.append((len(segs), -score, absolute))
    scored.sort()
    return [url for _depth, _negscore, url in scored]


class _TextExtractor(HTMLParser):
    """Extract descriptive prose. Skips chrome tags (nav/header/footer/script/...). If the page has
    a <main> or <article>, ONLY that region's block text is kept (drops sidebars/boilerplate); else
    all block-tag text is kept. Collapses whitespace."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._main_depth = 0
        self._in_block = False
        self._buf: list[str] = []
        self._seg: list[str] = []
        self.segments: list[tuple[bool, str]] = []  # (inside_main, text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1
        elif tag in ("main", "article"):
            self._main_depth += 1
        elif tag in _BLOCK_TAGS and self._skip_depth == 0:
            self._in_block = True
            self._seg = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in ("main", "article"):
            self._main_depth = max(0, self._main_depth - 1)
        elif tag in _BLOCK_TAGS and self._in_block:
            text = " ".join(" ".join(self._seg).split())
            if text:
                self.segments.append((self._main_depth > 0, text))
            self._in_block = False
            self._seg = []

    def handle_data(self, data: str) -> None:
        if self._in_block and self._skip_depth == 0:
            self._seg.append(data)


def extract_main_text(html: str) -> str:
    """Main descriptive text from an about page. Prefers <main>/<article> content when present.
    Verbatim (no summarizing) — only whitespace collapsed and block segments joined by blank lines.
    Pure — no fetch. Returns '' if nothing descriptive is found."""
    ex = _TextExtractor()
    try:
        ex.feed(html)
    except Exception:  # noqa: BLE001 — malformed HTML must never crash the crawl
        pass
    main_segs = [t for in_main, t in ex.segments if in_main]
    chosen = main_segs if main_segs else [t for _, t in ex.segments]
    # Drop trivially short fragments (menu labels, breadcrumbs) that slipped past tag filtering.
    chosen = [t for t in chosen if len(t) >= 2]
    return "\n\n".join(chosen).strip()


def robots_allows(robots_txt: str, user_agent: str, url: str) -> bool:
    """True if `robots_txt` permits `user_agent` to fetch `url`. Empty/unparseable robots -> allow
    (standard behavior). Pure wrapper over stdlib RobotFileParser — no fetch."""
    rp = urllib.robotparser.RobotFileParser()
    rp.parse((robots_txt or "").splitlines())
    try:
        return rp.can_fetch(user_agent, url)
    except Exception:  # noqa: BLE001
        return True


# --------------------------------------------------------------------------- IO / fetch layer

class Fetcher:
    """Polite HTTP GET with identified UA, per-host delay, timeout, one 5xx/timeout retry, and 429
    hard-backoff. Returns (status, final_url, body) — status is the HTTP code, or -1 on a network
    error/timeout after the retry. Not pure; the crawl loop owns all the network side effects."""

    def __init__(self, user_agent: str = USER_AGENT) -> None:
        self.user_agent = user_agent
        self._last_host_time: dict[str, float] = {}
        self._last_any_time = 0.0

    def _pace(self, host: str) -> None:
        now = time.monotonic()
        wait = max(
            self._last_host_time.get(host, 0.0) + MIN_HOST_DELAY_S - now,
            self._last_any_time + GLOBAL_PACE_S - now,
        )
        if wait > 0:
            time.sleep(wait)
        stamp = time.monotonic()
        self._last_host_time[host] = stamp
        self._last_any_time = stamp

    def _open(self, url: str) -> tuple[int, str, str]:
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 (http/https only, validated)
                raw = resp.read(2_000_000)  # cap body read at ~2MB
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.status, resp.geturl(), raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read(500_000).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            return e.code, url, body

    def get(self, url: str) -> tuple[int, str, str]:
        host = urlparse(url).netloc.lower()
        self._pace(host)
        try:
            status, final_url, body = self._open(url)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(RETRY_BACKOFF_S)
            self._pace(host)
            try:
                status, final_url, body = self._open(url)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                return -1, url, ""
        if status == 429:
            time.sleep(HARD_BACKOFF_S)
        elif 500 <= status < 600:
            time.sleep(RETRY_BACKOFF_S)
            self._pace(host)
            try:
                status, final_url, body = self._open(url)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                return -1, url, ""
        return status, final_url, body


class RobotsCache:
    """Per-host robots.txt fetch + cache. A host's robots is fetched once. On 4xx/absent -> allow
    all; on network error/5xx -> allow (documented: unreachable robots is treated permissive, but
    the WAF circuit-breaker still protects the origin from a fetch storm)."""

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher
        self._cache: dict[str, str] = {}

    def _robots_txt(self, origin: str) -> str:
        if origin not in self._cache:
            status, _final, body = self._fetcher.get(f"{origin}/robots.txt")
            self._cache[origin] = body if status == 200 else ""
        return self._cache[origin]

    def allows(self, url: str) -> bool:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        return robots_allows(self._robots_txt(origin), self._fetcher.user_agent, url)


# --------------------------------------------------------------------------- crawl one entity

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _fail(status: str, source_url: str) -> dict:
    assert status in FAILURE_REASONS, status
    return {"text": None, "source_url": source_url, "fetched_at": _now_iso(),
            "method": None, "status": status, "truncated": False}


def crawl_entity(target: dict, fetcher: Fetcher, robots: RobotsCache,
                 host_403: dict[str, int]) -> dict:
    """Fetch + discover + extract for one target. Returns a descriptions.json value dict.

    host_403 tracks consecutive 403s per host across the whole run (the WAF circuit-breaker); a
    host that has tripped CONSEC_403_TRIP short-circuits without any further fetch."""
    start_url = target.get("start_url")
    if not start_url:
        return _fail("no_url", start_url or "")
    host = urlparse(start_url).netloc.lower()

    if host_403.get(host, 0) >= CONSEC_403_TRIP:
        r = _fail("fetch_failed", start_url)
        r["note"] = f"host_waf_blocked (skipped after {CONSEC_403_TRIP} consecutive 403s from {host})"
        return r

    # robots first — a Disallow on the start URL blocks the entity outright.
    if not robots.allows(start_url):
        return _fail("robots_disallowed", start_url)

    fetches = 0
    status, final_url, homepage = fetcher.get(start_url)
    fetches += 1
    if status == 403:
        host_403[host] = host_403.get(host, 0) + 1
    elif status == 200:
        host_403[host] = 0

    # Build the candidate list. Homepage link-scan FIRST — an about link the page actually
    # advertises is higher-confidence than a blind probe — then deterministic path-probes as the
    # fallback (and the ONLY option when the homepage 403s, as nyc.gov does). method label travels
    # with each candidate so a success reports how it was found.
    path_probes = discover_about_candidates(start_url)
    link_scans = find_about_links(homepage, final_url) if status == 200 and homepage else []
    seen_cand: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for u, m in [(u, "link-scan") for u in link_scans] + [(u, "path-probe") for u in path_probes]:
        if u.rstrip("/") not in seen_cand:
            seen_cand.add(u.rstrip("/"))
            candidates.append((u, m))

    if status != 200 and not path_probes:
        return _fail("fetch_failed", start_url)

    fetched_an_about = False
    for cand_url, method in candidates:
        if fetches >= MAX_FETCHES_PER_ENTITY:
            break
        if host_403.get(host, 0) >= CONSEC_403_TRIP:
            break
        if not robots.allows(cand_url):
            continue
        cstatus, cfinal, cbody = fetcher.get(cand_url)
        fetches += 1
        if cstatus == 403:
            host_403[host] = host_403.get(host, 0) + 1
            continue
        if cstatus != 200 or not cbody:
            continue
        host_403[host] = 0
        fetched_an_about = True
        text = extract_main_text(cbody)
        if text:
            capped, was_trunc = truncate(text)
            return {"text": capped, "source_url": cfinal, "fetched_at": _now_iso(),
                    "method": method, "status": "ok", "truncated": was_trunc}

    # Nothing extractable. Distinguish "found an about page but it was empty" from "never found one"
    # and from "the site itself never responded".
    if fetched_an_about:
        return _fail("extraction_empty", start_url)
    if status == 200:
        return _fail("no_about_found", start_url)
    return _fail("fetch_failed", start_url)


# --------------------------------------------------------------------------- orchestration

def _load_out() -> dict:
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    return {"_generated_from": "crawl_about.py", "descriptions": {}}


def _write_out(doc: dict) -> None:
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _coverage(descriptions: dict) -> dict:
    counts = {"ok": 0}
    for r in descriptions.values():
        s = r.get("status", "?")
        counts[s] = counts.get(s, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Politely crawl entity about pages -> descriptions.json")
    ap.add_argument("--operator-authorized", action="store_true",
                    help="REQUIRED to fetch. Without it, prints the crawl plan and exits (access gate).")
    ap.add_argument("--force", action="store_true",
                    help="Re-crawl entities already present in descriptions.json (default: skip them).")
    ap.add_argument("--limit", type=int, default=0,
                    help="Crawl at most N not-yet-done targets (0 = all). Useful for a smoke run.")
    args = ap.parse_args(argv)

    if not TARGETS.exists():
        raise SystemExit(f"error: no crawl set at {TARGETS}. Run scripts/crawl_targets.py first.")
    doc = json.loads(TARGETS.read_text(encoding="utf-8"))
    targets = doc.get("targets", [])
    no_url_entities = doc.get("no_url", [])

    out = _load_out()
    descriptions = out.setdefault("descriptions", {})

    # Record no_url entities up front (they need no network) so coverage is complete.
    for e in no_url_entities:
        if args.force or e["id"] not in descriptions:
            descriptions[e["id"]] = _fail("no_url", "")

    pending = [t for t in targets if args.force or t["id"] not in descriptions]
    if args.limit and args.limit > 0:
        pending = pending[:args.limit]

    if not args.operator_authorized:
        print("ACCESS GATE: --operator-authorized not set; not fetching.")
        print(f"  crawl set: {len(targets)} targets, {len(no_url_entities)} no_url "
              f"({len(pending)} would be fetched this run).")
        print("  Re-run with --operator-authorized to perform the crawl.")
        return 0

    out["crawl_started_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    out["user_agent"] = USER_AGENT
    fetcher = Fetcher()
    robots = RobotsCache(fetcher)
    host_403: dict[str, int] = {}

    print(f"crawling {len(pending)} target(s) with UA: {USER_AGENT}")
    for i, target in enumerate(pending, 1):
        result = crawl_entity(target, fetcher, robots, host_403)
        descriptions[target["id"]] = result
        _write_out(out)  # rewrite after each entity: interrupt-safe / resumable
        note = f" [{result.get('note')}]" if result.get("note") else ""
        print(f"  [{i}/{len(pending)}] {target['id']}: {result['status']}{note}")

    out["crawl_finished_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    _write_out(out)
    print(f"\ncoverage ({len(descriptions)} entities): {_coverage(descriptions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
