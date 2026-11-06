"""ai_readability_websites.py — every known New York government website, one row per host.

WHY THIS EXISTS
  The AI-readability study needs one published list of the websites it can test, built from the
  registry itself so the two never drift, plus the statewide lists that cover what the registry
  does not yet hold. It is written in the layout of GSA's Federal Website Index
  (Target_URL_List_Data_Dictionary.csv, copied in data/ai-readability/gsa/): `initial_url`,
  `base_domain`, `top_level_domain`, `branch`, `agency`, `bureau`, then one `source_list_*`
  TRUE/FALSE column per source, then our `ny_` columns.

SOURCES (all read from the repo; this script never fetches)
  registry        data/registry.json web_properties (role primary; legacy domains left out),
                  plus the start URLs in data/crawl_targets.json (derived from the registry; they
                  can name a subdomain, e.g. new.mta.info, that web_properties does not)
  state_agency    data/ai-readability/sources/2026-11-06/state-agency-listing-websites.csv
                  ("a state agency listing", shared privately; domain, URL and agency only)
  abo             ABO Directory of Public Authorities, data.ny.gov 4vym-q77x (`website`)
  cisa            CISA .gov list, NY rows only (`Security contact email` is never read)
  its_2023        NYS ITS Locality Hierarchy with Websites, data.ny.gov 55k6-h6qq (stale, 2023)

  One row per host (a leading `www.` is kept in `initial_url` but ignored for matching). When
  a source lists a page rather than a host (a town hosted on its county's site), the page goes
  in `ny_page_urls` and the host's row records every owner in `ny_names`.

  A row here says "a public list names this host", nothing more. It is not a registry entity
  and is never minted into data/registry.json by this script.

OUTPUTS
  data/ai-readability/websites.csv / websites.json   (published)
  data/ai-readability/target-list-dictionary.csv     (--write-dictionary; GSA target-list format)

Run:
  python scripts/ai_readability_websites.py
  python scripts/ai_readability_websites.py --write-dictionary
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ai_readability_export as ex  # noqa: E402  (public-suffix helpers, csv conventions)

ROOT = pathlib.Path(__file__).resolve().parent.parent
AIR = ROOT / "data" / "ai-readability"
SRC = AIR / "sources" / "2026-11-06"
PATHS = {
    "registry": ROOT / "data" / "registry.json",
    "crawl_targets": ROOT / "data" / "crawl_targets.json",
    "state_agency": SRC / "state-agency-listing-websites.csv",
    "abo": SRC / "abo-directory-4vym-q77x.json",
    "cisa": SRC / "cisa-dotgov-current-full.csv",
    "its_2023": SRC / "its-locality-websites-55k6-h6qq.json",
}
OUT_CSV, OUT_JSON = AIR / "websites.csv", AIR / "websites.json"
OUT_DICT = AIR / "target-list-dictionary.csv"

# Order = precedence for `agency` when several sources name an owner.
SOURCES = (
    ("registry", "Registry", "data/registry.json (this repo)"),
    ("state_agency", "State agency listing",
     "a state agency listing (shared with BetaNYC privately; link private)"),
    ("abo", "ABO", "NYS Authorities Budget Office directory, data.ny.gov 4vym-q77x"),
    ("cisa", "CISA .gov", "CISA .gov domain list, github.com/cisagov/dotgov-data"),
    ("its_2023", "ITS 2023", "NYS ITS Locality Hierarchy with Websites, data.ny.gov 55k6-h6qq, "
     "last updated 2023-03-06"),
)


def _host_path(u: str):
    u = (u or "").strip()
    if not u:
        return None, None
    p = urllib.parse.urlparse(u if "//" in u else "https://" + u)
    if not p.hostname or "." not in p.hostname:
        return None, None
    path = p.path.rstrip("/")
    return p.hostname.lower().rstrip("."), (path or None)


def _key(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


# ------------------------------------------------------------------------------ readers
def read_registry(path, crawl_targets=None):
    reg = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in reg["entities"]}
    if crawl_targets and pathlib.Path(crawl_targets).exists():
        ct = json.loads(pathlib.Path(crawl_targets).read_text(encoding="utf-8"))
        for t in ct.get("targets", []):
            h, p = _host_path(t.get("start_url") or "")
            e = by_id.get(t.get("id"), {})
            if h:
                yield {"host": h, "path": p, "name": e.get("name") or t.get("name"),
                       "entity_id": t.get("id"), "level": e.get("government_level")}
    for e in reg["entities"]:
        for w in e.get("web_properties") or []:
            if w.get("role") == "legacy":
                continue
            h, p = _host_path(w.get("domain") or w.get("url") or "")
            if h:
                yield {"host": h, "path": p, "name": e.get("name"), "entity_id": e["id"],
                       "level": e.get("government_level")}


def read_state_agency(path):
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            h, p = _host_path(r.get("url") or r.get("domain"))
            if h:
                yield {"host": h, "path": p, "name": r.get("agency"), "level": "state agency"}


def read_abo(path):
    for a in json.loads(pathlib.Path(path).read_text(encoding="utf-8")):
        w = a.get("website")
        if isinstance(w, dict):
            w = w.get("url")
        h, p = _host_path(w or "")
        if h:
            yield {"host": h, "path": p, "name": a.get("public_authority_name"),
                   "level": "authority ({})".format(a.get("public_authority_type") or "?")}


def read_cisa(path):
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("State") not in ("NY", "New York"):
                continue
            h, _ = _host_path(r["Domain name"])
            if h:
                yield {"host": h, "path": None, "name": r.get("Organization name"),
                       "level": "dotgov: " + (r.get("Domain type") or "?")}


def _its_name(x):
    t = x.get("type")
    if t == "City":
        return "City of " + (x.get("city_name") or x.get("municipality") or "?")
    if t == "Town":
        return "Town of " + (x.get("town_name") or x.get("municipality") or "?")
    if t == "Village":
        return "Village of " + (x.get("village_name") or x.get("municipality") or "?")
    return (x.get("county") or "?") + " County"


def read_its(path):
    for x in json.loads(pathlib.Path(path).read_text(encoding="utf-8")):
        w = x.get("website")
        if isinstance(w, dict):
            w = w.get("url")
        h, p = _host_path(w or "")
        if h:
            yield {"host": h, "path": p, "name": _its_name(x),
                   "level": (x.get("type") or "?").lower(), "county": x.get("county")}


READERS = {"registry": read_registry, "state_agency": read_state_agency, "abo": read_abo,
           "cisa": read_cisa, "its_2023": read_its}


# ------------------------------------------------------------------------------ build
COLUMNS = (["initial_url", "base_domain", "top_level_domain", "branch", "agency", "bureau"]
           + ["source_list_" + s for s, _, _ in SOURCES]
           + ["ny_initial_domain", "ny_entity_ids", "ny_names", "ny_levels", "ny_counties",
              "ny_page_urls", "ny_source_count"])


def build(paths=PATHS, psl=None) -> list:
    psl = psl or ex.load_psl()
    hosts = {}
    for src, _, _ in SOURCES:
        reader = READERS[src]
        recs = reader(paths[src], paths.get("crawl_targets")) if src == "registry" else reader(paths[src])
        for rec in recs:
            k = _key(rec["host"])
            h = hosts.setdefault(k, {"host": rec["host"], "by": {}, "entity_ids": set(), "names": [],
                                     "levels": set(), "counties": set(), "pages": set()})
            h["by"].setdefault(src, []).append(rec)
            if rec.get("entity_id"):
                h["entity_ids"].add(rec["entity_id"])
            if rec.get("name") and rec["name"] not in h["names"]:
                h["names"].append(rec["name"])
            if rec.get("level"):
                h["levels"].add(rec["level"])
            if rec.get("county"):
                h["counties"].add(rec["county"])
            if rec.get("path"):
                h["pages"].add("https://{}{}".format(rec["host"], rec["path"]))
    rows = []
    for k in sorted(hosts):
        h = hosts[k]
        row = {"initial_url": "https://{}/".format(h["host"]),
               "base_domain": ex.base_domain(h["host"], psl),
               "top_level_domain": ex.tld(h["host"]), "branch": None, "bureau": None,
               "agency": _agency(h)}
        for src, _, _ in SOURCES:
            row["source_list_" + src] = src in h["by"]
        row.update(ny_initial_domain=h["host"], ny_entity_ids=sorted(h["entity_ids"]) or None,
                   ny_names=h["names"] or None, ny_levels=sorted(h["levels"]) or None,
                   ny_counties=sorted(h["counties"]) or None, ny_page_urls=sorted(h["pages"]) or None,
                   ny_source_count=len(h["by"]))
        rows.append(row)
    return rows


def _agency(h):
    """The one owner a source names for the host itself, by source precedence. Blank when the
    registry lists several bodies on the host, or when the only mentions are page-level (a
    town page on a county host)."""
    if len(h["entity_ids"]) > 1:
        return None
    for src, _, _ in SOURCES:
        for rec in h["by"].get(src, []):
            if not rec.get("path") and rec.get("name"):
                return rec["name"]
    return None


def write(rows, csv_path=OUT_CSV, json_path=OUT_JSON) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([ex.csv_value(r.get(c)) for c in COLUMNS])
    pathlib.Path(json_path).write_text(
        json.dumps([{c: r.get(c) for c in COLUMNS} for r in rows], indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")


# ------------------------------------------------------------------------------ dictionary
def dictionary_rows() -> list:
    gsa = {}
    with open(AIR / "gsa" / "Target_URL_List_Data_Dictionary.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            gsa[r["field id"]] = r
    rows = [
        {"name": "Initial URL", "field id": "initial_url", "data type": "string",
         "description": "URL to be scanned (GSA). BetaNYC: always `https://` + host + `/`; pages "
                        "listed by a source are in `ny_page_urls`"},
        {"name": "Initial Base Domain", "field id": "base_domain", "data type": "string",
         "description": "Base Domain of initial URL (GSA). BetaNYC: by the Public Suffix List snapshot "
                        "in sources/2026-11-06/, so `.ny.us` hosts get one more label (`state.ny.us`)"},
        {"name": "Initial Top Level Domain", "field id": "top_level_domain", "data type": "string",
         "description": "Top Level Domain of initial URL (GSA)"},
        {"name": "Branch", "field id": "branch", "data type": "string",
         "description": "Branch of Government of the Agency Owner of initial Base Domain (GSA). "
                        "BetaNYC: not yet filled; no source states it reliably"},
        {"name": "Agency", "field id": "agency", "data type": "string",
         "description": "Agency Owner of initial Base Domain (GSA). BetaNYC: the owner of the host, "
                        "from the first source that names one, in this order: registry, state agency "
                        "listing, ABO, CISA, ITS 2023. Blank for shared hosts (several registry bodies) "
                        "and for hosts named only page-by-page; see `ny_names`"},
        {"name": "Bureau", "field id": "bureau", "data type": "string",
         "description": "Bureau Owner of initial Base Domain (GSA). BetaNYC: not yet filled"},
    ]
    for src, label, credit in SOURCES:
        rows.append({"name": "Source List - " + label, "field id": "source_list_" + src,
                     "data type": "boolean",
                     "description": "Whether the host was sourced from " + credit})
    rows += [
        {"name": "NY - Initial Domain", "field id": "ny_initial_domain", "data type": "string",
         "description": "The host as first listed, `www.` kept"},
        {"name": "NY - Entity IDs", "field id": "ny_entity_ids", "data type": "list",
         "description": "Registry ids (data/registry.json) whose primary website is this host"},
        {"name": "NY - Names", "field id": "ny_names", "data type": "list",
         "description": "Every owner name the sources give for this host or a page on it"},
        {"name": "NY - Levels", "field id": "ny_levels", "data type": "list",
         "description": "What kind of body each source says owns it: registry government_level, "
                        "`state agency`, ABO type, CISA domain type, or ITS type"},
        {"name": "NY - Counties", "field id": "ny_counties", "data type": "list",
         "description": "County named by ITS 2023, when any"},
        {"name": "NY - Page URLs", "field id": "ny_page_urls", "data type": "list",
         "description": "Pages (not whole hosts) a source lists, e.g. a town page on its county's site"},
        {"name": "NY - Source Count", "field id": "ny_source_count", "data type": "integer",
         "description": "How many of the five sources list this host"},
    ]
    assert {r["field id"] for r in rows[:6]} <= set(gsa) | {"base_domain", "top_level_domain"}
    return rows


def write_dictionary(path=OUT_DICT) -> pathlib.Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=("name", "field id", "description", "data type"))
        w.writeheader()
        w.writerows(dictionary_rows())
    return pathlib.Path(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write-dictionary", action="store_true")
    a = ap.parse_args(argv)
    if a.write_dictionary:
        print("wrote", write_dictionary())
        return 0
    rows = build()
    write(rows)
    counts = {s: sum(r["source_list_" + s] for r in rows) for s, _, _ in SOURCES}
    print("wrote {} hosts to {} and {}; per source: {}".format(len(rows), OUT_CSV, OUT_JSON, counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
