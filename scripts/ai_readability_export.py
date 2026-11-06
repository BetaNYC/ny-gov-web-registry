"""ai_readability_export.py — one row per website, in the federal Site Scanning layout.

WHY THIS EXISTS
  A probe run (scripts/probe_bot_access.py) writes one row per *test* per host, each with its own
  evidence (results.jsonl), and render_check.py adds v1-headless.jsonl. That shape keeps the
  proof, but nobody can open it next to the federal data. GSA's Site Scanning program publishes
  one row per *website* with fixed column names, documented in its data dictionary
  (https://github.com/GSA/site-scanning-documentation/blob/main/data/Site_Scanning_Data_Dictionary.csv,
  a copy is vendored at data/ai-readability/gsa/). This script writes the same layout:

    1. every GSA column, in GSA's order, with GSA's field id;
    2. filled only where our measurement means what GSA's field means (each case explained in
       the data dictionary's "description - details"); otherwise left blank, never guessed;
    3. then our own columns, prefixed `ny_` (who the site belongs to) and `ai_<test id>_`
       (the AI-readability catalog, docs/ai-readability/tests.md), in the same naming style.

  The raw results.jsonl stays the record of truth; this file is a view of it.

OUTPUTS (inside the run folder, git-ignored with the rest of runs/)
  sites.csv   — GSA conventions: TRUE/FALSE, lists comma-separated, blank = not measured
  sites.json  — the same rows as a JSON array: true/false, lists as arrays, null = not measured

  --write-dictionary writes data/ai-readability/data-dictionary.csv, in GSA's dictionary format
  (same seven columns), generated from the FIELDS table below so the two cannot drift.

Run:
  python scripts/ai_readability_export.py --run-dir data/ai-readability/runs/<run-id> \
      --targets-file data/ai-readability/pool-2026-11-06.json
  python scripts/ai_readability_export.py --write-dictionary
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ai_readability as ar  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
GSA_DICT = ROOT / "data" / "ai-readability" / "gsa" / "Site_Scanning_Data_Dictionary.csv"
PSL_FILE = ROOT / "data" / "ai-readability" / "sources" / "2026-11-06" / "public_suffix_list.dat"
OUR_DICT = ROOT / "data" / "ai-readability" / "data-dictionary.csv"
REGISTRY = ROOT / "data" / "registry.json"
DICT_COLUMNS = ("name", "field id", "description", "description - details", "relevant_scan",
                "data type", "queryable")

RESEARCH = "research_betanyc"
IDENTITIES = ("research_betanyc", "googlebot", "gptbot", "oai_searchbot", "chatgpt_user",
              "mechanism_probe_chrome")
LIVE_CODES = {200, 201, 202, 203, 204, 205, 206}      # GSA `live` definition

# Our transport-failure kinds (probe_bot_access._transport_kind) -> GSA scan status codes
# (https://github.com/GSA/site-scanning-engine/blob/main/entities/scan-status.ts).
TRANSPORT_TO_GSA = {"dns": "dns_resolution_error", "tls": "invalid_ssl_cert",
                    "reset": "connection_reset", "timeout": "timeout",
                    "refused": "connection_refused", "other": "unknown_error"}


# ------------------------------------------------------------------------------ public suffix
def load_psl(path=PSL_FILE) -> tuple:
    rules, wild, exc = set(), set(), set()
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        rule = line.split()[0].lower()
        if rule.startswith("!"):
            exc.add(rule[1:])
        elif rule.startswith("*."):
            wild.add(rule[2:])
        else:
            rules.add(rule)
    return rules, wild, exc


def base_domain(host: str, psl: tuple) -> str | None:
    """Registered domain per the Public Suffix List algorithm (publicsuffix.org/list/)."""
    if not host:
        return None
    rules, wild, exc = psl
    labels = host.lower().rstrip(".").split(".")
    suffix_len = 1                                   # default rule "*"
    for i in range(len(labels)):
        cand = ".".join(labels[i:])
        if cand in exc:
            suffix_len = len(labels) - i - 1
            break
        if cand in rules or (i + 1 < len(labels) and ".".join(labels[i + 1:]) in wild):
            suffix_len = len(labels) - i
            break
    if len(labels) <= suffix_len:
        return None
    return ".".join(labels[-(suffix_len + 1):])


def tld(host: str | None) -> str | None:
    return host.rsplit(".", 1)[-1].lower() if host else None


def bare_mime(ctype: str | None) -> str | None:
    return ctype.split(";")[0].strip().lower() if ctype else None


# ------------------------------------------------------------------------------ host context
class Host:
    """Everything known about one host: its pool row, test rows, V1 rows, review rows."""

    def __init__(self, target, rows, v1, review, psl, tenants=()):
        self.t, self.rows, self.v1, self.review, self.psl = target, rows, v1, review, psl
        self.tenants = sorted(tenants)
        self.by = {}
        for r in rows:
            self.by.setdefault((r["test"], r.get("identity")), r)

    def row(self, test, identity=RESEARCH):
        r = self.by.get((test, identity))
        if r is None and identity == RESEARCH:      # derived tests carry identity "*"
            r = self.by.get((test, "*"))
        return r

    def verdict(self, test, identity=RESEARCH):
        r = self.row(test, identity)
        return r["verdict"] if r else None

    def ev(self, test, key, identity=RESEARCH):
        r = self.row(test, identity)
        return (r.get("evidence") or {}).get(key) if r else None

    @property
    def host(self):
        return urllib.parse.urlparse(self.t["url"]).hostname

    def final_url(self):
        return self.ev("W4", "final_url")

    def quarantine(self):
        for r in self.review:
            if r.get("action") == "quarantined":
                return r.get("what")
        return None

    def primary_status(self):
        if self.ev("W4", "status") is not None:
            return "completed"
        q = self.quarantine()
        if not q:
            return None if not self.rows else "unknown_error"
        if q.startswith("transport "):
            return TRANSPORT_TO_GSA.get(q.split()[1].rstrip(":"), "unknown_error")
        return "skipped"                              # rate-limited (HTTP 429): we stopped


def _status_of(h, test):
    r = h.row(test)
    if r is None:
        return None
    if (r.get("evidence") or {}).get("status") is not None:
        return "completed"
    return h.primary_status() if h.primary_status() != "completed" else "unknown_error"


def _agency(h):
    """GSA's `agency` owns the domain. A host shared by several registry bodies has no one owner
    we can name from the registry, so it stays blank and `ny_entity_ids` lists the bodies."""
    return None if len(h.tenants) > 1 else h.t.get("entity_name")


def _https_enforced(h):
    st = h.ev("I5", "http_status")
    if h.ev("I5", "http_redirects_to_https"):
        return True
    if st is not None and 200 <= st < 300:          # served the page over plain http
        return False
    return None                                      # refused, errored or redirected elsewhere


def _is_live(code):
    return None if code is None else code in LIVE_CODES


def _redirect(h):
    fu = h.final_url()
    if fu is None:
        return None
    a, b = urllib.parse.urlparse(h.t["url"]), urllib.parse.urlparse(fu)
    return (a.hostname, a.path or "/") != (b.hostname, b.path or "/")


def _sitemap_at_root(h):
    """GSA's sitemap fields describe /sitemap.xml exactly. Ours may use a robots-declared one."""
    url = h.ev("M2", "url")
    return bool(url) and urllib.parse.urlparse(url).path == "/sitemap.xml"


def _sm(h, key):
    return h.ev("M2", key) if _sitemap_at_root(h) else None


def _sm_count(h):
    return h.ev("M2", "url_count") if h.ev("M2", "kind") != "sitemapindex" else h.ev("M2", "child_count")


def _robots_detected(h):
    st, fu = h.ev("W1", "status"), h.ev("W1", "final_url")
    if st is None:
        return None
    ok_place = fu is None or (urllib.parse.urlparse(fu).hostname == h.host
                              and urllib.parse.urlparse(fu).path == "/robots.txt")
    return st == 200 and ok_place


def _w3_decisions(h):
    toks = h.ev("W3", "tokens") or {}
    out = []
    for tok, paths in sorted(toks.items()):
        p = paths.get("/")
        d = p.get("decision") if isinstance(p, dict) else p   # short form: {"/": "disallow"}
        if d:
            out.append("{}={}".format(tok, d))
    return out or None


def _w4_admitted(h, ident):
    v = h.verdict("W4", ident)
    return None if v in (None, "na") else v == "pass"


def _w5_divergent(h):
    r = h.row("W5")
    if not r:
        return None
    return sorted((r.get("evidence") or {}).get("divergent") or [])


def _v1(h, variant, key):
    r = h.v1.get(variant)
    return r.get(key) if r else None


def _v1_name_decides(h):
    a, b = _v1(h, "headless_default", "verdict"), _v1(h, "headless_as_chrome", "verdict")
    if a is None or b is None or "na" in (a, b):
        return None
    return a == "blocked" and b == "pass"


def _scores(h):
    v = ar.host_row_verdicts(h.rows) if h.rows else {}
    return ar.abhi_score(v), ar.betanyc_view(v)


def _i2(h, prop):
    return (h.ev("I2", "values") or {}).get(prop) or None


def _main_present(h):
    lm = h.ev("R3", "landmarks")
    return None if lm is None else "main" in lm


# ------------------------------------------------------------------------------ the fields
# GSA columns we fill: field id -> (relevant_scan, BetaNYC details, extractor).
# Every GSA column not listed here is written blank, and the dictionary says so.
GSA_FILLED = {
    "name": ("pool", "Same rule as GSA: the starting host with a leading `www.` removed.",
             lambda h: h.host[4:] if h.host.startswith("www.") else h.host),
    "initial_url": ("pool", "The `url` from the target list. Unlike GSA it may carry a path (a few "
                    "sites are registered at a page, not a bare host).", lambda h: h.t["url"]),
    "initial_domain": ("pool", "Same as GSA.", lambda h: h.host),
    "initial_base_domain": ("pool", "Computed with the Public Suffix List snapshot in "
                            "`data/ai-readability/sources/2026-11-06/`. Under `.ny.us` the base is "
                            "one label deeper (e.g. `state.ny.us`), because `ny.us` is a public suffix.",
                            lambda h: base_domain(h.host, h.psl)),
    "initial_top_level_domain": ("pool", "Same as GSA, without the leading period.",
                                 lambda h: tld(h.host)),
    "redirect": ("W4", "From the research-identity homepage request (W4, `research_betanyc`): TRUE "
                 "if the final host or path differs from the starting one.", _redirect),
    "url": ("W4", "Final URL of the research-identity homepage request (W4).", lambda h: h.final_url()),
    "domain": ("W4", "Host of `url`.",
               lambda h: urllib.parse.urlparse(h.final_url()).hostname if h.final_url() else None),
    "base_domain": ("W4", "As `initial_base_domain`, for `domain`.",
                    lambda h: base_domain(urllib.parse.urlparse(h.final_url()).hostname, h.psl)
                    if h.final_url() else None),
    "top_level_domain": ("W4", "As `initial_top_level_domain`, for `domain`.",
                         lambda h: tld(urllib.parse.urlparse(h.final_url()).hostname)
                         if h.final_url() else None),
    "live": ("W4", "Same rule as GSA (status 200–206), applied to the research-identity homepage "
             "request. GSA loads pages in a headless browser; this is a plain HTTP request, so a "
             "site that blocks scripts but admits browsers can read FALSE here. See `ai_v1_*`.",
             lambda h: _is_live(h.ev("W4", "status"))),
    "status_code": ("W4", "Status of the research-identity homepage request (W4).",
                    lambda h: h.ev("W4", "status")),
    "agency": ("pool", "Not a federal agency: the New York government body that owns the site, "
               "from the target list's `entity_name`. Blank when `data/registry.json` lists the host "
               "as primary for more than one body (e.g. `www.nyc.gov`); see `ny_entity_ids`.", _agency),
    "404_test": ("F4", "From F4 (honest 404): TRUE if a made-up path returns 404 or 410.",
                 lambda h: {"pass": True, "fail": False}.get(h.verdict("F4"))),
    "source_list": ("pool", "Which list the address came from: `nyc-registry` (this repo's "
                    "registry), `cisa` (CISA .gov list), `its-2023` (NYS ITS localities list "
                    "55k6-h6qq) or `abo` (NYS ABO directory 4vym-q77x). One value per site, "
                    "not every list it appears on.",
                    lambda h: [h.t.get("lead") or h.t.get("pool")] if (h.t.get("lead") or h.t.get("pool")) else None),
    "scan_date": ("W4", "Time of the research-identity homepage request (UTC, ISO 8601). Unlike "
                  "GSA, no later process rewrites it.",
                  lambda h: (h.row("W4") or {}).get("ts") or ((h.rows or [{}])[0].get("ts"))),
    "primary_scan_status": ("W4", "`completed` if the homepage answered at all (any status code). "
                            "Otherwise GSA's own codes, mapped from our transport errors (dns → "
                            "`dns_resolution_error`, tls → `invalid_ssl_cert`, reset → "
                            "`connection_reset`, timeout → `timeout`, refused → `connection_refused`, "
                            "other → `unknown_error`). `skipped` means the site asked us to slow "
                            "down (HTTP 429) and we stopped visiting it.", lambda h: h.primary_status()),
    "not_found_scan_status": ("F4", "As `primary_scan_status`, for the F4 request.",
                              lambda h: _status_of(h, "F4")),
    "robots_txt_scan_status": ("W1", "As `primary_scan_status`, for the W1 request.",
                               lambda h: _status_of(h, "W1")),
    "sitemap_xml_scan_status": ("M2", "As `primary_scan_status`, for the M2 request. Blank when M2 "
                                "read a robots-declared sitemap instead of `/sitemap.xml`.",
                                lambda h: _status_of(h, "M2") if _sitemap_at_root(h) else None),
    "cms": ("P1", "From P1. Our detection list is shorter than GSA's 63 signatures and also names "
            "hosts (e.g. `WP Engine`); several matches are comma-separated.",
            lambda h: ", ".join(h.ev("P1", "platforms") or []) or None),
    "https_enforced": ("I5", "From I5: TRUE if `http://` redirects to `https://`, FALSE if `http://` "
                       "serves the page (2xx), blank if it refused, failed or redirected elsewhere. "
                       "GSA takes this from a separate federal security dataset; ours is one direct "
                       "request.", _https_enforced),
    "title": ("R3", "Same as GSA (`<title>` text), from the plain HTTP homepage body.",
              lambda h: h.ev("R3", "title") or None),
    "description": ("I1", "Same as GSA (meta description), from the plain HTTP homepage body.",
                    lambda h: h.ev("I1", "description") or None),
    "og_title": ("I2", "Same as GSA.", lambda h: _i2(h, "og:title")),
    "og_description": ("I2", "Same as GSA.", lambda h: _i2(h, "og:description")),
    "language": ("R3", "Same as GSA (`<html lang>`).", lambda h: h.ev("R3", "lang") or None),
    "main_element_present": ("R3", "TRUE if the page has a `<main>` element or `role=main`. GSA "
                             "counts the `<main>` tag only.", _main_present),
    "robots_txt_detected": ("W1", "Same rule as GSA: status 200 at `/robots.txt` on the same host. "
                            "W1's own verdict is stricter (it also needs `text/plain` and a body "
                            "that parses); see `ai_w1_verdict`.", _robots_detected),
    "robots_txt_url": ("W1", "Same as GSA.",
                       lambda h: h.ev("W1", "final_url") if h.ev("W1", "status") is not None else None),
    "robots_txt_status_code": ("W1", "Same as GSA.", lambda h: h.ev("W1", "status")),
    "robots_txt_media_type": ("W1", "Media type only, parameters such as `charset` dropped.",
                              lambda h: bare_mime(h.ev("W1", "content_type"))),
    "robots_txt_sitemap_locations": ("M1", "Same as GSA (`Sitemap:` lines).",
                                     lambda h: h.ev("M1", "declared") or None),
    "sitemap_xml_detected": ("M2", "Same rule as GSA, filled only when M2 read `/sitemap.xml`. When "
                             "it read a robots-declared sitemap, these fields stay blank and the "
                             "`ai_m2_*` fields carry the result.",
                             lambda h: (h.verdict("M2") == "pass") if _sitemap_at_root(h) else None),
    "sitemap_xml_url": ("M2", "See `sitemap_xml_detected`.", lambda h: _sm(h, "url")),
    "sitemap_xml_status_code": ("M2", "See `sitemap_xml_detected`.", lambda h: _sm(h, "status")),
    "sitemap_xml_media_type": ("M2", "See `sitemap_xml_detected`. Parameters dropped.",
                               lambda h: bare_mime(_sm(h, "content_type"))),
    "sitemap_xml_count": ("M2", "See `sitemap_xml_detected`. For a sitemap index, the number of "
                          "child sitemaps (children are not fetched).",
                          lambda h: _sm_count(h) if _sitemap_at_root(h) else None),
    "sitemap_xml_lastmod": ("M3", "Newest `<lastmod>` in the file (M3). See `sitemap_xml_detected`.",
                            lambda h: h.ev("M3", "newest") if _sitemap_at_root(h) else None),
}

# Our columns: (field id, name, description, details, relevant_scan, data type, extractor)
T, N, B, L, D = "Text", "Number", "Yes or no", "A list of values", "Date and time"


def _ours():
    f = [
        ("ny_entity_id", "NY - Entity ID", "The registry id of the body that owns the site",
         "Matches `id` in `data/registry.json`. Blank for sites not yet in the registry (the "
         "statewide test pool).", "pool", T, lambda h: h.t.get("entity_id")),
        ("ny_entity_ids", "NY - Entity IDs on Host", "Every registry body whose primary website is this host",
         "From `data/registry.json` `web_properties` (role `primary`). More than one means a shared "
         "host such as `www.nyc.gov`, and `agency` is left blank.", "pool", L, lambda h: h.tenants or None),
        ("ny_pool", "NY - Pool", "Which group of sites this one was tested with",
         "`nyc-registry`, `city`, `county` or `state-authority`.", "pool", T, lambda h: h.t.get("pool")),
        ("ai_run_id", "AI - Run ID", "The run this row came from",
         "Folder name under `data/ai-readability/runs/`: date, then vantage (where we ran from).",
         "(run)", T, lambda h: (h.rows or [{}])[0].get("run_id")),
        ("ai_catalog_version", "AI - Catalog Version", "Version of the test catalog used",
         "See `docs/ai-readability/tests.md`. Compare runs only within one version.", "(run)", T,
         lambda h: (h.rows or [{}])[0].get("catalog_version")),
        ("ai_vantage", "AI - Vantage", "Where the requests came from",
         "`office` or `residential`. Sites can treat these connections differently.", "(run)", T,
         lambda h: (h.rows or [{}])[0].get("vantage")),
        ("ai_w1_verdict", "AI - W1 robots.txt", "Whether robots.txt is reachable and is a real robots file",
         "pass / partial / fail / blocked / na. Stricter than `robots_txt_detected`.", "W1", T,
         lambda h: h.verdict("W1")),
        ("ai_w2_verdict", "AI - W2 Names AI Crawlers", "Whether robots.txt gives any AI crawler its own rules",
         "pass if at least one AI token has its own group.", "W2", T, lambda h: h.verdict("W2")),
        ("ai_w2_tokens_named", "AI - W2 AI Tokens Named", "Which AI crawler names robots.txt mentions",
         "Matched case-insensitively against the catalog's token list.", "W2", L,
         lambda h: h.ev("W2", "tokens_named") or None),
        ("ai_w3_decisions", "AI - W3 Stated Decision", "What robots.txt says each AI crawler may do at `/`",
         "`token=allow` or `token=disallow`, by RFC 9309 longest-match, falling back to `*`.", "W3", L,
         _w3_decisions),
        ("ai_w6_present", "AI - W6 AI Preference Signals", "Whether robots.txt declares AI-use preferences",
         "Cloudflare `Content-Signal`, IETF aipref `Content-Usage`, or `__usage_*` groups.", "W6", B,
         lambda h: h.ev("W6", "present")),
        ("ai_w4_verdict", "AI - W4 Reachable by AI", "Whether the homepage admits the AI identities",
         "pass if every AI identity got the page, partial if some did, blocked if none did.", "W4", T,
         lambda h: h.verdict("W4", "all_ai")),
    ]
    for ident in IDENTITIES:
        f.append(("ai_w4_{}_status".format(ident), "AI - W4 {} Status".format(ident),
                  "Homepage status code when we said we were `{}`".format(ident),
                  "One request with that identity's User-Agent. `mechanism_probe_chrome` is a "
                  "disclosed control (a Chrome UA), used only to show a block is about identity.",
                  "W4", N, lambda h, i=ident: h.ev("W4", "status", i)))
        f.append(("ai_w4_{}_admitted".format(ident), "AI - W4 {} Admitted".format(ident),
                  "Whether `{}` got the real homepage".format(ident),
                  "TRUE only for a 200 that is not a challenge or block page.", "W4", B,
                  lambda h, i=ident: _w4_admitted(h, i)))
    f += [
        ("ai_w4_server", "AI - W4 Server Header", "The `Server` header on the homepage",
         "Often names the CDN (e.g. `AkamaiGHost`, `cloudflare`).", "W4", T, lambda h: h.ev("W4", "server")),
        ("ai_w4_waf", "AI - W4 Firewall Detected", "Web firewall or bot-defense products recognized",
         "From headers and cookies. Blank means none recognized.", "W4", L, lambda h: h.ev("W4", "waf") or None),
        ("ai_w5_divergent", "AI - W5 Stated vs Enforced", "Identities where robots.txt and the edge disagree",
         "robots.txt allows the crawler but the homepage refuses it, or the reverse. Cloudflare and "
         "Akamai may refuse a vendor UA sent from a non-vendor network (spoof detection).", "W5", L,
         _w5_divergent),
        ("ai_m1_verdict", "AI - M1 Sitemap Declared", "Whether robots.txt names a sitemap", "", "M1", T,
         lambda h: h.verdict("M1")),
        ("ai_m2_verdict", "AI - M2 Sitemap Works", "Whether the sitemap loads and is valid XML",
         "Reads the first robots-declared sitemap, else `/sitemap.xml`.", "M2", T, lambda h: h.verdict("M2")),
        ("ai_m2_url", "AI - M2 Sitemap URL", "The sitemap address M2 read", "", "M2", T, lambda h: h.ev("M2", "url")),
        ("ai_m2_count", "AI - M2 Sitemap Items", "Entries listed directly in that sitemap",
         "Child sitemaps for an index (not fetched).", "M2", N, _sm_count),
        ("ai_m3_newest_lastmod", "AI - M3 Newest lastmod", "Newest `<lastmod>` in that sitemap", "", "M3", D,
         lambda h: h.ev("M3", "newest")),
        ("ai_m4_verdict", "AI - M4 llms.txt", "Whether `/llms.txt` exists and is well formed",
         "Catalog v0.1 wants an H1 and a `>` summary; the llmstxt.org spec requires only the H1.",
         "M4", T, lambda h: h.verdict("M4")),
        ("ai_m4_status_code", "AI - M4 llms.txt Status", "Status code for `/llms.txt`", "", "M4", N,
         lambda h: h.ev("M4", "status")),
        ("ai_r1_verdict", "AI - R1 Text Without JavaScript", "Whether real text arrives without running scripts",
         "", "R1", T, lambda h: h.verdict("R1")),
        ("ai_r1_words", "AI - R1 Words", "Visible words in the plain HTTP page", "In `<main>` if present, else `<body>`.",
         "R1", N, lambda h: h.ev("R1", "words")),
        ("ai_r3_h1_count", "AI - R3 H1 Count", "Number of `<h1>` headings", "", "R3", N, lambda h: h.ev("R3", "h1_count")),
        ("ai_i1_verdict", "AI - I1 Meta Description", "Whether a useful meta description exists",
         "pass at 50+ characters and not a CMS default.", "I1", T, lambda h: h.verdict("I1")),
        ("ai_i2_verdict", "AI - I2 Open Graph", "Whether og:title, og:description and og:site_name exist",
         "All three = pass, one or two = partial.", "I2", T, lambda h: h.verdict("I2")),
        ("ai_i3_verdict", "AI - I3 Structured Data", "Whether schema.org data says this is a government",
         "", "I3", T, lambda h: h.verdict("I3")),
        ("ai_i3_types", "AI - I3 Schema Types", "schema.org types found", "", "I3", L, lambda h: h.ev("I3", "types") or None),
        ("ai_i4_names_agree", "AI - I4 Names Agree", "Whether the page names itself the way the registry does",
         "", "I4", B, lambda h: h.ev("I4", "names_agree")),
        ("ai_i5_cisa_dotgov_list", "AI - I5 On CISA .gov List", "Whether the host is on CISA's .gov list",
         "", "I5", B, lambda h: h.ev("I5", "cisa_dotgov_list")),
        ("ai_d1_verdict", "AI - D1 Open Data Linked", "Whether the homepage links to an open data portal",
         "Portals listed in `data/ai-readability/portals.json`.", "D1", T, lambda h: h.verdict("D1")),
        ("ai_d1_count", "AI - D1 Portal Links", "Number of open data portal links on the homepage", "", "D1", N,
         lambda h: h.ev("D1", "count")),
        ("ai_f1_verdict", "AI - F1 Current Year", "Whether the newest printed year is this year or last",
         "", "F1", T, lambda h: h.verdict("F1")),
        ("ai_f1_newest_year", "AI - F1 Newest Year", "Newest copyright or 'updated' year printed", "", "F1", N,
         lambda h: h.ev("F1", "newest")),
        ("ai_p2_vendors", "AI - P2 Civic Vendors", "Civic platforms the homepage links to",
         "Legistar, Granicus, eCode360, ArcGIS Hub, and others.", "P2", L, lambda h: h.ev("P2", "vendors") or None),
        ("ai_p3_translation_widget", "AI - P3 Translation Widget", "Whether a translation widget is on the page",
         "", "P3", B, lambda h: h.ev("P3", "translation_widget")),
        ("ai_p3_hreflang_alternates", "AI - P3 hreflang Count", "Number of `hreflang` alternate links", "", "P3", N,
         lambda h: h.ev("P3", "hreflang_alternates")),
        ("ai_p4_third_party_script_count", "AI - P4 Third-Party Scripts",
         "Distinct outside hosts that serve scripts to the homepage",
         "Scripts only, so smaller than GSA's `third_party_service_count`. The host names are kept private.",
         "P4", N, lambda h: h.ev("P4", "third_party_script_hosts")),
    ]
    for variant, label in (("headless_default", "Own Name"), ("headless_as_chrome", "As Chrome")):
        f.append(("ai_v1_{}_status".format(variant), "AI - V1 Headless {} Status".format(label),
                  "Homepage status in headless Chrome ({})".format(variant),
                  "`headless_default` sends the browser's own UA (contains `HeadlessChrome`); "
                  "`headless_as_chrome` is the same browser with only that word changed.", "V1", N,
                  lambda h, v=variant: _v1(h, v, "status")))
        f.append(("ai_v1_{}_verdict".format(variant), "AI - V1 Headless {} Verdict".format(label),
                  "Whether headless Chrome ({}) got the real page".format(variant),
                  "pass / blocked / na.", "V1", T, lambda h, v=variant: _v1(h, v, "verdict")))
    f += [
        ("ai_v1_name_decides", "AI - V1 Name Decides", "Whether only the declared browser name changed the answer",
         "TRUE if the browser was blocked under its own name and let in as `Chrome`.", "V1", B, _v1_name_decides),
        ("ai_v1_words", "AI - V1 Rendered Words", "Visible words after the page's scripts ran",
         "From `headless_as_chrome`. Compare with `ai_r1_words`.", "V1", N,
         lambda h: _v1(h, "headless_as_chrome", "words")),
        ("ai_abhi12_score", "AI - Abhi-12 Score", "Score on Abhi Nemani's 12-row scorecard",
         "Pass 1, partial 0.5, fail, blocked and na 0, out of 12.", "(score)", N, lambda h: _scores(h)[0]),
        ("ai_betanyc_score", "AI - BetaNYC Score", "The same score over only the rows we could measure",
         "Blocked rows are left out, not counted as fail. Read with `ai_betanyc_measured_rows`.", "(score)", N,
         lambda h: _scores(h)[1]["score"]),
        ("ai_betanyc_measured_rows", "AI - BetaNYC Measured Rows", "How many of the 12 rows could be measured",
         "", "(score)", N, lambda h: _scores(h)[1]["measured_rows"]),
        ("ai_review_count", "AI - Review Notes", "Notes this site left on the run's review list",
         "Pushback, errors and oddities to look at by hand.", "(run)", N, lambda h: len(h.review)),
    ]
    return f


OURS = _ours()


def gsa_dictionary(path=GSA_DICT) -> list:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def columns(gsa=None) -> list:
    gsa = gsa or gsa_dictionary()
    return [r["field id"] for r in gsa] + [f[0] for f in OURS]


# ------------------------------------------------------------------------------ build rows
def registry_tenants(path=REGISTRY) -> dict:
    """host -> set of entity ids that list it as a primary web property."""
    path = pathlib.Path(path)
    if not path.exists():
        return {}
    reg = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for e in reg.get("entities", []) if isinstance(reg, dict) else reg:
        for w in e.get("web_properties") or []:
            if w.get("role", "primary") != "primary":
                continue
            d = w.get("url") or w.get("domain") or ""
            host = urllib.parse.urlparse(d if "//" in d else "https://" + d).hostname
            if host:
                out.setdefault(host, set()).add(e["id"])
    return out


def build_rows(targets, results, v1_rows, review_rows, psl, gsa=None, tenants=None) -> list:
    gsa = gsa or gsa_dictionary()
    tenants = tenants or {}
    by_host, rev_by_host, v1_by_url = {}, {}, {}
    for r in results:
        by_host.setdefault(r["host"], []).append(r)
    for r in review_rows:
        rev_by_host.setdefault(r.get("host"), []).append(r)
    for r in v1_rows:
        v1_by_url.setdefault(r["url"], {})[r["variant"]] = r
    out = []
    for t in targets:
        host = urllib.parse.urlparse(t["url"]).hostname
        h = Host(t, by_host.get(host, []), v1_by_url.get(t["url"], {}), rev_by_host.get(host, []), psl,
                 tenants.get(host, ()))
        row = {}
        for g in gsa:
            fid = g["field id"]
            row[fid] = GSA_FILLED[fid][2](h) if fid in GSA_FILLED else None
        for fid, *_rest, fn in OURS:
            row[fid] = fn(h)
        out.append(row)
    return out


def csv_value(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    return str(v)


def write_outputs(rows, run_dir: pathlib.Path, cols) -> None:
    with open(run_dir / "sites.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([csv_value(r.get(c)) for c in cols])
    (run_dir / "sites.json").write_text(
        json.dumps([{c: r.get(c) for c in cols} for r in rows], indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")


# ------------------------------------------------------------------------------ dictionary
def dictionary_rows(gsa=None) -> list:
    gsa = gsa or gsa_dictionary()
    rows = []
    for g in gsa:
        fid = g["field id"]
        if fid in GSA_FILLED:
            scan, details, _ = GSA_FILLED[fid]
            details = "BetaNYC: " + details
        else:
            scan, details = "(not measured)", ("BetaNYC: not measured. Always blank in BetaNYC "
                                              "exports; kept so the file has GSA's full column set.")
        rows.append({"name": g["name"], "field id": fid, "description": g["description"],
                     "description - details": details, "relevant_scan": scan,
                     "data type": g["data type"], "queryable": "No"})
    for fid, name, desc, details, scan, dtype, _ in OURS:
        rows.append({"name": name, "field id": fid, "description": desc,
                     "description - details": details, "relevant_scan": scan,
                     "data type": dtype, "queryable": "No"})
    return rows


def write_dictionary(path=OUR_DICT, gsa=None) -> pathlib.Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DICT_COLUMNS)
        w.writeheader()
        w.writerows(dictionary_rows(gsa))
    return pathlib.Path(path)


# ------------------------------------------------------------------------------ cli
def _jsonl(path: pathlib.Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", help="run folder holding results.jsonl (and v1-headless.jsonl)")
    ap.add_argument("--targets-file", help="the target list the run used")
    ap.add_argument("--write-dictionary", action="store_true",
                    help="write data/ai-readability/data-dictionary.csv and exit")
    a = ap.parse_args(argv)
    if a.write_dictionary:
        print("wrote", write_dictionary())
        return 0
    if not (a.run_dir and a.targets_file):
        ap.error("--run-dir and --targets-file are required (or use --write-dictionary)")
    run_dir = pathlib.Path(a.run_dir)
    targets = json.loads(pathlib.Path(a.targets_file).read_text(encoding="utf-8"))
    gsa = gsa_dictionary()
    rows = build_rows(targets, _jsonl(run_dir / "results.jsonl"), _jsonl(run_dir / "v1-headless.jsonl"),
                      _jsonl(run_dir / "review.jsonl"), load_psl(), gsa, registry_tenants())
    write_outputs(rows, run_dir, columns(gsa))
    print("wrote {} rows x {} columns to {}/sites.csv and sites.json".format(
        len(rows), len(columns(gsa)), run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
