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

CATALOG 0.2 (issue #45)
  - `--targets-file data/ai-readability/addresses.csv`: one row per host, the address the scanner
    read (probe_bot_access.plan_targets), with `ny_kind`, `ny_kinds`, `ny_platform`,
    `ny_owning_body`, `ny_level`.
  - W4 columns per identity come from run.json `identities`: the 2026-11-06 six first, under the
    same names, then every other identity the run used. The dictionary lists every identity a
    full-tier run sends today (crawlers.csv plus the controls).
  - The run's network record (run.json `network`) on every row as `ai_run_*`. Never the address.
  - `--only-run-hosts`: only the hosts the run visited. A flag, not a default: run.json does not
    record `--only-hosts`.

Run:
  python scripts/ai_readability_export.py --run-dir data/ai-readability/runs/<run-id> \
      --targets-file data/ai-readability/addresses.csv [--only-run-hosts] [--out-dir <dir>]
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
# The 2026-11-06 core run's six identities. Their columns always come first, under these names,
# so that run exports the same as before; any other identity a run used (run.json `identities`,
# from data/ai-readability/crawlers.csv since catalog 0.2) gets the same two columns after them.
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

    def __init__(self, target, rows, v1, review, psl, tenants=(), reach=None, retest=(), person=None,
                 person_rows=(), run=None):
        self.t, self.rows, self.v1, self.review, self.psl = target, rows, v1, review, psl
        self.run = run or {}                 # the run's run.json (identities, network record)
        self.reach = reach or {}             # this host's reach_check.py record, if it was checked
        self.retest = {(r["test"], r.get("identity")): r for r in retest}   # a later run's rows
        self.person = person or {}           # headed_check.py: one visible-Chrome visit, if made
        self.person_rows = list(person_rows)  # the core tests scored on what that Chrome received
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


# ---- catalog 0.2 tests (C1, D2–D7, R2, M5, F3)
def _d2_kind_count(h, kind):
    if h.row("D2") is None or h.verdict("D2") != "reported":
        return None
    return (h.ev("D2", "by_kind") or {}).get(kind, 0)


def _r2_part(h, part, key):
    p = h.ev("R2", part)
    return p.get(key) if isinstance(p, dict) else None


def _r2_alternates(h):
    alts = h.ev("R2", "advertised_alternates")
    return [a.get("href") for a in alts if isinstance(a, dict) and a.get("href")] or None \
        if isinstance(alts, list) else None


def _d5_count(h):
    n = h.ev("D5", "datasets")
    return n if n is not None else h.ev("D5", "entries")


def _d7_split(h, admitted):
    ids = h.ev("D7", "ai_identities", ar.W4_AGGREGATE_IDENTITY)
    if not isinstance(ids, dict):
        return None
    return sorted(k for k, v in ids.items() if (v == "pass") == admitted) or None


# ---- the run's network record (run.json `network`, issue #44)
def _net(h):
    return h.run.get("network") or {}


def _reading(h, which):
    return _net(h).get(which) or {}


def _family_ok(h, fam):
    """TRUE if the start reading forced over `fam` got an address back, FALSE if it failed,
    blank if the run has no per-family reading. A run before the per-family reading (#51) still
    proves the family its one reading came over."""
    start = _reading(h, "start")
    by = start.get("by_family")
    if isinstance(by, dict) and fam in by:
        return bool((by[fam] or {}).get("public_ip"))
    ip = start.get("public_ip")
    if ip:
        return (":" in ip) == (fam == "ipv6") or None
    return None


def _changed(h, key):
    c = _net(h).get("changed")
    return c.get(key) if isinstance(c, dict) else None


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
    "cms": ("C1", "From C1: GSA Site Scanning's own `cms` value when the address list carries "
            "one, kept as is; otherwise what the homepage says (P1's signals plus the WordPress "
            "REST link). Our detection list is shorter than GSA's 63 signatures and also names "
            "hosts (e.g. `WP Engine`); several matches are comma-separated. Runs before catalog "
            "0.2 have no C1 and fall back to P1.",
            lambda h: h.ev("C1", "cms") or ", ".join(h.ev("P1", "platforms") or []) or None),
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


def _reach_diagnoses() -> dict:
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in sys.path:  # `python -I` leaves the script's own folder off sys.path
        sys.path.insert(0, here)
    import reach_check
    return reach_check.DIAGNOSES


def run_identities(run: dict = None) -> list:
    """The identity keys a run's W4 columns cover: the 2026-11-06 six first (fixed names), then
    every other identity run.json lists, in its own order. Nothing is hardcoded beyond the six."""
    keys = list(IDENTITIES)
    for i in (run or {}).get("identities") or []:
        k = i.get("key") if isinstance(i, dict) else i
        if k and k not in keys:
            keys.append(k)
    return keys


def _ours(identities=IDENTITIES):
    f = [
        ("ny_entity_id", "NY - Entity ID", "The registry id of the body that owns the site",
         "Matches `id` in `data/registry.json`. Blank for sites not yet in the registry (the "
         "statewide test pool).", "pool", T, lambda h: h.t.get("entity_id")),
        ("ny_entity_ids", "NY - Entity IDs on Host", "Every registry body whose primary website is this host",
         "From `data/registry.json` `web_properties` (role `primary`). More than one means a shared "
         "host such as `www.nyc.gov`, and `agency` is left blank.", "pool", L, lambda h: h.tenants or None),
        ("ny_pool", "NY - Pool", "Which group of sites this one was tested with",
         "`nyc-registry`, `city`, `county` or `state-authority`.", "pool", T, lambda h: h.t.get("pool")),
        ("ny_kind", "NY - Address Kind", "The kind of address the run read for this host",
         "From `data/ai-readability/addresses.csv` `kind`: `main site`, `sub-site`, `section`, "
         "`data portal` or `API`. When several addresses share a host, the scanner reads one of them "
         "(a data portal or main site first) and this is its kind. Blank for other target lists.",
         "addresses", T, lambda h: h.t.get("kind")),
        ("ny_kinds", "NY - Address Kinds on Host", "Every address kind the list gives this host",
         "From `addresses.csv`. A host listed as both `main site` and `data portal` gets the portal "
         "tests (D5–D7).", "addresses", L, lambda h: h.t.get("kinds") or None),
        ("ny_platform", "NY - Platform", "The data portal's software, as the address list states it",
         "From `addresses.csv` `platform` (the portal registry's own words). D5 maps it to a platform "
         "only where it names Socrata, ArcGIS Hub or CKAN outright; see `ai_d5_platform`.",
         "addresses", T, lambda h: h.t.get("platform")),
        ("ny_owning_body", "NY - Owning Body", "The government body the address list says owns the address",
         "From `addresses.csv` `owning_body`, for the address the run read. Unlike `agency`, it is "
         "filled on shared hosts too. Blank for other target lists (see `agency`).", "addresses", T,
         lambda h: h.t.get("entity_name") if h.t.get("kind") else None),
        ("ny_level", "NY - Level or Entity Type", "Level of government or entity type",
         "From `addresses.csv` `level_or_entity_type`.", "addresses", T, lambda h: h.t.get("level")),
        ("ai_run_id", "AI - Run ID", "The run this row came from",
         "Folder name under `data/ai-readability/runs/`: date, then vantage (where we ran from).",
         "(run)", T, lambda h: (h.rows or [{}])[0].get("run_id")),
        ("ai_catalog_version", "AI - Catalog Version", "Version of the test catalog used",
         "See `docs/ai-readability/tests.md`. Compare runs only within one version.", "(run)", T,
         lambda h: (h.rows or [{}])[0].get("catalog_version")),
        ("ai_vantage", "AI - Vantage", "Where the requests came from",
         "`office` or `residential`. Sites can treat these connections differently.", "(run)", T,
         lambda h: (h.rows or [{}])[0].get("vantage")),
        ("ai_run_tier", "AI - Run Tier", "Which set of tests the run used",
         "`core` or `full` (run.json `tier`). Full-tier tests are blank in a core run.", "(run)", T,
         lambda h: h.run.get("tier")),
        ("ai_run_place", "AI - Run Place", "Where the run went out from",
         "`office`, `home` or `vpn` (run.json `network.place`). The same on every row of a run. "
         "Runs before catalog 0.2 have no network record and leave every `ai_run_*` network column "
         "blank.", "(network)", T, lambda h: _net(h).get("place")),
        ("ai_run_provider", "AI - Run Provider", "The internet provider at the start of the run",
         "RIPEstat `as-overview` `holder` for the start ASN (`scripts/network_record.py`).",
         "(network)", T, lambda h: _reading(h, "start").get("provider")),
        ("ai_run_asn_start", "AI - Run ASN at Start", "The network number (ASN) at the start of the run",
         "RIPEstat `network-info`, e.g. `AS12271`. Blank if the lookup failed.", "(network)", T,
         lambda h: _reading(h, "start").get("asn")),
        ("ai_run_asn_end", "AI - Run ASN at End", "The network number (ASN) at the end of the run",
         "As `ai_run_asn_start`, read again when the run closed.", "(network)", T,
         lambda h: _reading(h, "end").get("asn")),
        ("ai_run_ipv4", "AI - Run Had IPv4", "Whether the run's network had a working IPv4 route",
         "From the start reading forced over IPv4 (`by_family.ipv4`): TRUE if it got an address, FALSE "
         "if it failed. Runs without per-family readings fill only the family their one reading used. "
         "Addresses themselves are not exported.", "(network)", B, lambda h: _family_ok(h, "ipv4")),
        ("ai_run_ipv6", "AI - Run Had IPv6", "Whether the run's network had a working IPv6 route",
         "As `ai_run_ipv4`, for IPv6. A site may be reached over either family.", "(network)", B,
         lambda h: _family_ok(h, "ipv6")),
        ("ai_run_network_changed", "AI - Run Network Changed", "Whether the ASN changed during the run",
         "run.json `network.changed.asn`: TRUE if the start and end ASNs differ. Blank when either "
         "reading failed (an unreadable network is not evidence it stayed put).", "(network)", B,
         lambda h: _changed(h, "asn")),
        ("ai_run_ip_changed", "AI - Run Address Changed", "Whether the public address changed during the run",
         "run.json `network.changed.public_ip`. An address can change within one provider (IPv6 "
         "privacy addresses do); read with `ai_run_network_changed`.", "(network)", B,
         lambda h: _changed(h, "public_ip")),
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
    for ident in identities:
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
    rep = "Reported test: `reported` when measured, or `na` / `blocked` when it could not be."
    f += [
        # ---- catalog 0.2 (docs/ai-readability/tests.md § Catalog 0.2 additions), issue #43
        ("ai_c1_verdict", "AI - C1 Website Software", "Whether C1 ran", rep, "C1", T, lambda h: h.verdict("C1")),
        ("ai_c1_cms", "AI - C1 CMS", "The website software C1 settled on",
         "GSA's own `cms` when the address list carries one, else what the homepage shows. Same value "
         "as the GSA `cms` column, which falls back to P1 for older runs.", "C1", T,
         lambda h: h.ev("C1", "cms")),
        ("ai_c1_source", "AI - C1 Source", "Where `ai_c1_cms` came from",
         "`gsa` (the address list) or `homepage`. Blank when neither named any software.", "C1", T,
         lambda h: h.ev("C1", "source")),
        ("ai_c1_homepage_platforms", "AI - C1 Homepage Platforms", "Software the homepage itself shows",
         "P1's signals plus the WordPress REST link (rel `https://api.w.org/`). Kept even when GSA's "
         "value wins, so the two can be compared.", "C1", L,
         lambda h: h.ev("C1", "homepage_platforms") or None),
        ("ai_d2_verdict", "AI - D2 Data and API Links", "Whether D2 ran", rep, "D2", T, lambda h: h.verdict("D2")),
        ("ai_d2_count", "AI - D2 Links Found", "Data, API, catalog, feed and GIS links found",
         "In the homepage's anchors and `<link>` tags, its `Link:` header and llms.txt. No link is "
         "followed. A link can be of several kinds.", "D2", N, lambda h: h.ev("D2", "count")),
    ] + [
        ("ai_d2_{}_count".format(k), "AI - D2 {} Links".format(label), "Links D2 sorted as {}".format(desc),
         "By URL pattern or link text (`ai_readability.D2_PATTERNS`, `D2_TEXT`). 0 when D2 ran and "
         "found none.", "D2", N, (lambda k: lambda h: _d2_kind_count(h, k))(k))
        for k, label, desc in (("open_data", "Open Data", "open data (a D1 portal or the words "
                                "\"open data\")"),
                               ("api", "API", "an API"), ("developer", "Developer", "a developer page"),
                               ("catalog", "Catalog", "a machine-readable catalog"),
                               ("feed", "Feed", "an RSS, Atom or JSON feed"),
                               ("gis_service", "GIS Service", "a GIS service"))
    ] + [
        ("ai_d3_verdict", "AI - D3 WordPress REST", "Whether D3 ran",
         rep + " `na` when C1 does not show WordPress, or in a core run.", "D3", T, lambda h: h.verdict("D3")),
        ("ai_d3_rest_index", "AI - D3 REST Index", "Whether the WordPress REST index answered",
         "TRUE when the root answered 200 with JSON carrying `namespaces`.", "D3", B,
         lambda h: h.ev("D3", "rest_index")),
        ("ai_d3_status", "AI - D3 Status", "Status code of the WordPress REST root", "", "D3", N,
         lambda h: h.ev("D3", "status")),
        ("ai_d3_namespaces", "AI - D3 Namespaces", "Namespaces the REST index lists",
         "First 50, e.g. `wp/v2`. Shows which plugins expose an API.", "D3", L,
         lambda h: h.ev("D3", "namespaces") or None),
        ("ai_d3_routes", "AI - D3 Routes", "Number of routes in the REST index", "", "D3", N,
         lambda h: h.ev("D3", "routes")),
        ("ai_d4_verdict", "AI - D4 API Catalog", "Whether D4 ran", rep, "D4", T, lambda h: h.verdict("D4")),
        ("ai_d4_status", "AI - D4 Status", "Status code of `/.well-known/api-catalog`", "", "D4", N,
         lambda h: h.ev("D4", "status")),
        ("ai_d4_present", "AI - D4 Present", "Whether an RFC 9727 API catalog is there",
         "TRUE = 200 with a Linkset (a top-level `linkset` array).", "D4", B, lambda h: h.ev("D4", "present")),
        ("ai_d4_media_type_ok", "AI - D4 Media Type OK", "Whether it is served as `application/linkset+json`",
         "RFC 9727 §4.2 (a MUST).", "D4", B, lambda h: h.ev("D4", "media_type_ok")),
        ("ai_d4_profile_rfc9727", "AI - D4 Profile", "Whether the media type names the RFC 9727 profile",
         "A SHOULD in RFC 9727.", "D4", B, lambda h: h.ev("D4", "profile_rfc9727")),
        ("ai_d4_items", "AI - D4 Items", "Links of relation `item` in the catalog", "RFC 9727 §3.1.", "D4", N,
         lambda h: h.ev("D4", "items")),
        ("ai_d5_verdict", "AI - D5 Portal Catalog", "Whether a data portal serves its documented catalog",
         "pass / fail / blocked / na. Only addresses of kind `data portal`; `na` also when the platform "
         "was not recognized (no undocumented path is tried).", "D5", T, lambda h: h.verdict("D5")),
        ("ai_d5_platform", "AI - D5 Platform", "The portal platform D5 recognized",
         "`socrata`, `arcgis_hub` or `ckan`.", "D5", T, lambda h: h.ev("D5", "platform")),
        ("ai_d5_platform_signal", "AI - D5 Platform Signal", "What named the platform",
         "The address list, the portal registry's words, the host name, the meta generator or the "
         "homepage text.", "D5", T, lambda h: h.ev("D5", "platform_signal")),
        ("ai_d5_url", "AI - D5 Catalog URL", "The documented catalog address D5 asked for", "", "D5", T,
         lambda h: h.ev("D5", "url")),
        ("ai_d5_status", "AI - D5 Status", "Status code of the catalog", "", "D5", N, lambda h: h.ev("D5", "status")),
        ("ai_d5_datasets", "AI - D5 Catalog Entries", "Entries in the catalog response",
         "ArcGIS Hub: `dataset` items; CKAN: `result` items (the request asks for 1); Socrata: list "
         "length (the request asks for 1). A sample size, not the portal's total.", "D5", N, _d5_count),
        ("ai_d6_verdict", "AI - D6 Dataset Page Readable", "Whether one dataset page has text without JavaScript",
         "R1's rule (≥ 150 words, no JS-shell phrase) on the first dataset the catalog names.", "D6", T,
         lambda h: h.verdict("D6")),
        ("ai_d6_dataset_url", "AI - D6 Dataset URL", "The dataset page D6 read", "", "D6", T,
         lambda h: h.ev("D6", "dataset_url")),
        ("ai_d6_words", "AI - D6 Words", "Visible words on that dataset page", "", "D6", N,
         lambda h: h.ev("D6", "words")),
        ("ai_d7_verdict", "AI - D7 Catalog Reachable by AI", "Whether the portal catalog admits the AI identities",
         "W4's all-AI rule applied to the D5 catalog: pass if every AI identity got it, partial if "
         "some did, blocked if none did.", "D7", T, lambda h: h.verdict("D7", ar.W4_AGGREGATE_IDENTITY)),
        ("ai_d7_admitted", "AI - D7 Admitted", "AI identities that got the catalog",
         "Identity keys, as in the `ai_w4_<identity>_*` columns.", "D7", L, lambda h: _d7_split(h, True)),
        ("ai_d7_not_admitted", "AI - D7 Not Admitted", "AI identities that did not get the catalog",
         "Refused, failed or no answer.", "D7", L, lambda h: _d7_split(h, False)),
        ("ai_r2_verdict", "AI - R2 Markdown Twin", "Whether R2 ran", rep, "R2", T, lambda h: h.verdict("R2")),
        ("ai_r2_markdown_served", "AI - R2 Markdown Served", "Whether a markdown version was served",
         "TRUE if `GET /` with `Accept: text/markdown` or `GET /index.md` returned `text/markdown` "
         "that is not HTML.", "R2", B, lambda h: h.ev("R2", "markdown_served")),
        ("ai_r2_accept_status", "AI - R2 Accept Status", "Status of `GET /` with `Accept: text/markdown`",
         "", "R2", N, lambda h: _r2_part(h, "accept_markdown", "status")),
        ("ai_r2_accept_media_type", "AI - R2 Accept Media Type", "Media type of that answer",
         "Parameters dropped.", "R2", T,
         lambda h: bare_mime(_r2_part(h, "accept_markdown", "content_type"))),
        ("ai_r2_vary", "AI - R2 Vary Header", "`Vary` header on that answer",
         "`Accept` here means the server says it negotiates on it.", "R2", T,
         lambda h: _r2_part(h, "accept_markdown", "vary")),
        ("ai_r2_index_md_status", "AI - R2 index.md Status", "Status of `GET /index.md`", "", "R2", N,
         lambda h: _r2_part(h, "index_md", "status")),
        ("ai_r2_advertised_alternates", "AI - R2 Advertised Markdown", "Markdown versions the homepage advertises",
         "`Link:` header or `<link>` with rel `alternate` and type `text/markdown`.", "R2", L, _r2_alternates),
        ("ai_m5_verdict", "AI - M5 llms-full.txt", "Whether M5 ran",
         rep + " Never scored: llmstxt.org does not define this file.", "M5", T, lambda h: h.verdict("M5")),
        ("ai_m5_present", "AI - M5 Present", "Whether `/llms-full.txt` exists",
         "TRUE = 200 and not HTML.", "M5", B, lambda h: h.ev("M5", "present")),
        ("ai_m5_status_code", "AI - M5 Status", "Status code for `/llms-full.txt`", "", "M5", N,
         lambda h: h.ev("M5", "status")),
        ("ai_m5_bytes", "AI - M5 Bytes", "Size of `/llms-full.txt` as read", "", "M5", N,
         lambda h: h.ev("M5", "bytes")),
        ("ai_f3_verdict", "AI - F3 Broken Links", "Whether the homepage's links work",
         "pass if at most 1 of up to 10 same-host links returns 4xx/5xx. Decides Abhi's \"No stale or "
         "broken content\" row with F1 in a full run.", "F3", T, lambda h: h.verdict("F3")),
        ("ai_f3_checked", "AI - F3 Links Checked", "Same-host homepage links checked",
         "The first 10 in document order; `HEAD`, then `GET` on 405.", "F3", N, lambda h: h.ev("F3", "checked")),
        ("ai_f3_broken", "AI - F3 Links Broken", "Of those, how many returned 4xx/5xx",
         "A firewall refusing deep links looks like breakage; cross-check `ai_w4_verdict`.", "F3", N,
         lambda h: h.ev("F3", "broken")),
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
        ("ai_reach_checked", "AI - Reach Checked", "Whether reach_check.py looked at this site",
         "Only sites the run could not read (W4 na, blocked or fail) are checked; blank reach columns on "
         "other sites mean not checked.", "reach", B, lambda h: bool(h.reach) or None),
        ("ai_reach_diagnosis", "AI - Reach Diagnosis", "Why the site could not be read",
         "One of: " + "; ".join("`{}` {}".format(k, v) for k, v in _reach_diagnoses().items()),
         "reach", T, lambda h: h.reach.get("diagnosis")),
        ("ai_reach_dns_resolves", "AI - Reach DNS Resolves", "Whether the site's host name resolves", "",
         "reach", B, lambda h: h.reach.get("dns_resolves")),
        ("ai_reach_alt_host", "AI - Reach Alternate Host", "The www or bare variant that resolves instead",
         "Set only when the listed name does not resolve.", "reach", T, lambda h: h.reach.get("alt_host")),
        ("ai_reach_tls", "AI - Reach TLS", "Whether the HTTPS certificate verified",
         "`ok`, `bad_cert` or `error` (handshake failed). Blank if nothing answered on 443.", "reach", T,
         lambda h: h.reach.get("tls")),
        ("ai_reach_tls_detail", "AI - Reach TLS Detail", "Why the certificate or handshake failed",
         "For example `certificate has expired` or `Hostname mismatch`.", "reach", T,
         lambda h: h.reach.get("tls_detail")),
        ("ai_reach_https_status", "AI - Reach HTTPS Status", "Status of GET / over verified HTTPS",
         "Redirects not followed.", "reach", N, lambda h: h.reach.get("https_status")),
        ("ai_reach_http_status", "AI - Reach HTTP Status", "Status of GET / over plain http",
         "Redirects not followed. A 301 to https is normal.", "reach", N, lambda h: h.reach.get("http_status")),
        ("ai_reach_checked_at", "AI - Reach Checked At", "When reach_check.py ran for this site", "UTC.",
         "reach", D, lambda h: h.reach.get("ts")),
        ("ai_retest_run_id", "AI - Retest Run", "The later run that probed this site again",
         "Blank if the site was not retested.", "(run)", T,
         lambda h: next(iter(h.retest.values()))["run_id"] if h.retest else None),
        ("ai_retest_w4_verdict", "AI - Retest W4 Verdict", "W4 verdict in the retest run",
         "Compare with `ai_w4_verdict` to tell a passing glitch from a lasting failure.", "W4", T,
         lambda h: (h.retest.get(("W4", RESEARCH)) or {}).get("verdict")),
        ("ai_person_checked", "AI - Person Check", "Whether a visible Chrome visited this site",
         "headed_check.py, for sites that turned every crawler away. Blank if not checked.", "person", B,
         lambda h: True if h.person else None),
        ("ai_person_verdict", "AI - Person Verdict", "Did a visible Chrome, under its own name, get the homepage?",
         "pass = 200 and not a challenge page; blocked otherwise; na = no answer. A pass here with a "
         "blocked `ai_w4_verdict` means the site blocks bots, not people.", "person", T,
         lambda h: h.person.get("verdict")),
        ("ai_person_status", "AI - Person Status", "HTTP status Chrome received for the homepage", "",
         "person", N, lambda h: h.person.get("status")),
        ("ai_person_challenge", "AI - Person Challenge", "Chrome was shown a challenge or block page",
         "Automated Chrome is detectable (navigator.webdriver). A challenge here may still let a person by "
         "hand through.", "person", B, lambda h: h.person.get("challenge") if h.person else None),
        ("ai_person_vantage", "AI - Person Vantage", "The machine and connection used", "", "person", T,
         lambda h: h.person.get("vantage")),
        ("ai_person_abhi12_score", "AI - Person Abhi-12 Score", "The 12-row score on what Chrome received",
         "Same scoring as `ai_abhi12_score`. The agent-reachability row stays 0 here, because crawlers "
         "were turned away. Blank unless the person check passed.", "(score)", N,
         lambda h: ar.abhi_score(ar.host_row_verdicts(h.person_rows))
         if h.person.get("verdict") == "pass" and h.person_rows else None),
    ] + [
        ("ai_person_{}_verdict".format(t.lower()), "AI - Person {} Verdict".format(t),
         "{} verdict on what Chrome received".format(t), "Blank unless the person check passed.", t, T,
         (lambda t: lambda h: next((r["verdict"] for r in h.person_rows if r["test"] == t), None)
          if h.person.get("verdict") == "pass" else None)(t))
        for t in ("W1", "M2", "M4", "R1", "I1", "I2", "I3", "D1")
    ]
    return f




def gsa_dictionary(path=GSA_DICT) -> list:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def columns(gsa=None, identities=IDENTITIES) -> list:
    gsa = gsa or gsa_dictionary()
    return [r["field id"] for r in gsa] + [f[0] for f in _ours(identities)]


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


def _probe():
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in sys.path:  # `python -I` leaves the script's own folder off sys.path
        sys.path.insert(0, here)
    import probe_bot_access
    return probe_bot_access


def load_targets(path: str) -> list:
    """Same inputs as probe_bot_access.py --targets-file, including data/ai-readability/websites.json
    and data/ai-readability/addresses.csv.

    addresses.csv can list several addresses on one host (a main site and its sections), and the
    scanner reads each host once, from one of them (probe_bot_access.plan_targets). So a .csv
    gives one target per host: the address the scanner read, with every kind on the host
    (`kinds`) and the list's `owning_body_id` as `entity_id`."""
    probe = _probe()
    if not str(path).lower().endswith(".csv"):
        return probe.load_targets(path)
    targets = probe.load_addresses(path)
    with open(path, newline="", encoding="utf-8") as fh:
        raw = list(csv.DictReader(fh))
    ids = [(r.get("owning_body_id") or r.get("entity_id") or "").strip() or None for r in raw]
    # plan_targets picks a host's address by kind, then entity_id (None for every address, as in
    # the scanner); entity_name plays no part, so it can carry the row number back out.
    marked = [dict(t, entity_name="#{}".format(i)) for i, t in enumerate(targets)]
    out = []
    for job in probe.plan_targets(marked):
        i = int(job["entity_name"][1:])
        t = dict(targets[i], url=job["url"], entity_id=ids[i] or targets[i].get("entity_id"))
        for k in ("kinds", "platform", "gsa_cms"):
            if job.get(k):
                t[k] = job[k]
        out.append(t)
    return out


def run_hosts(results) -> set:
    """Hosts a run actually visited (host-level rows only)."""
    return {r["host"] for r in results if r.get("scope") != "page"}


def build_rows(targets, results, v1_rows, review_rows, psl, gsa=None, tenants=None, reach_rows=(),
               retest_results=(), person_rows=(), person_results=(), run=None) -> list:
    gsa = gsa or gsa_dictionary()
    run = run or {}
    ours = _ours(run_identities(run))
    tenants = tenants or {}
    by_host, rev_by_host, v1_by_url = {}, {}, {}
    reach_by_url = {r["url"]: r for r in reach_rows}
    retest_by_host = {}
    for r in retest_results:
        retest_by_host.setdefault(r["host"], []).append(r)
    person_by_url = {r["url"]: r for r in person_rows}
    person_by_host = {}
    for r in person_results:
        person_by_host.setdefault(r["host"], []).append(r)
    for r in results:
        if r.get("scope") == "page":       # --deep-pages rows are per page, never a host's
            continue
        by_host.setdefault(r["host"], []).append(r)
    for r in review_rows:
        if r.get("scope") == "page":
            continue
        rev_by_host.setdefault(r.get("host"), []).append(r)
    for r in v1_rows:
        v1_by_url.setdefault(r["url"], {})[r["variant"]] = r
    out = []
    for t in targets:
        host = urllib.parse.urlparse(t["url"]).hostname
        h = Host(t, by_host.get(host, []), v1_by_url.get(t["url"], {}), rev_by_host.get(host, []), psl,
                 tenants.get(host, ()), reach_by_url.get(t["url"]), retest_by_host.get(host, ()),
                 person_by_url.get(t["url"]), person_by_host.get(host, ()), run)
        row = {}
        for g in gsa:
            fid = g["field id"]
            row[fid] = GSA_FILLED[fid][2](h) if fid in GSA_FILLED else None
        for fid, *_rest, fn in ours:
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
def dictionary_identities() -> list:
    """Every identity a full-tier run sends today (data/ai-readability/crawlers.csv plus the
    controls), after the 2026-11-06 six. A run's export carries only the identities it used."""
    return run_identities({"identities": _probe().tier_identities("full")})


def dictionary_rows(gsa=None, identities=None) -> list:
    gsa = gsa or gsa_dictionary()
    identities = dictionary_identities() if identities is None else identities
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
    for fid, name, desc, details, scan, dtype, _ in _ours(identities):
        rows.append({"name": name, "field id": fid, "description": desc,
                     "description - details": details, "relevant_scan": scan,
                     "data type": dtype, "queryable": "No"})
    return rows


def write_dictionary(path=OUR_DICT, gsa=None, identities=None) -> pathlib.Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DICT_COLUMNS)
        w.writeheader()
        w.writerows(dictionary_rows(gsa, identities))
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
    ap.add_argument("--retest-run-dir", help="a later run over this run's failures; fills ai_retest_*")
    ap.add_argument("--person-run-dir", help="a headed_check.py run (headed.jsonl); fills ai_person_*")
    ap.add_argument("--only-run-hosts", action="store_true",
                    help="export only the hosts the run visited (e.g. a run made with --only-hosts); "
                         "by default every target gets a row, blank where the run did not go")
    ap.add_argument("--out-dir", help="where to write sites.csv and sites.json (default: --run-dir)")
    a = ap.parse_args(argv)
    if a.write_dictionary:
        print("wrote", write_dictionary())
        return 0
    if not (a.run_dir and a.targets_file):
        ap.error("--run-dir and --targets-file are required (or use --write-dictionary)")
    run_dir = pathlib.Path(a.run_dir)
    targets = load_targets(a.targets_file)
    gsa = gsa_dictionary()
    results = _jsonl(run_dir / "results.jsonl")
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8")) \
        if (run_dir / "run.json").exists() else {}
    if a.only_run_hosts:
        visited = run_hosts(results)
        targets = [t for t in targets if urllib.parse.urlparse(t["url"]).hostname in visited]
    rows = build_rows(targets, results, _jsonl(run_dir / "v1-headless.jsonl"),
                      _jsonl(run_dir / "review.jsonl"), load_psl(), gsa, registry_tenants(),
                      reach_rows=_jsonl(run_dir / "reach.jsonl"),
                      retest_results=_jsonl(pathlib.Path(a.retest_run_dir) / "results.jsonl")
                      if a.retest_run_dir else (),
                      person_rows=_jsonl(pathlib.Path(a.person_run_dir) / "headed.jsonl")
                      if a.person_run_dir else (),
                      person_results=_jsonl(pathlib.Path(a.person_run_dir) / "results.jsonl")
                      if a.person_run_dir else (), run=run)
    cols = columns(gsa, run_identities(run))
    out_dir = pathlib.Path(a.out_dir) if a.out_dir else run_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_outputs(rows, out_dir, cols)
    print("wrote {} rows x {} columns to {}/sites.csv and sites.json".format(len(rows), len(cols), out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
