"""statewide_common.py — helpers shared by the statewide sync scripts (sync_abo, sync_nygov).

Pure helpers plus two small IO conveniences. Nothing here fetches.

  - URL → host / path split, `www.` normalization, a minimal host sanity check.
  - `base_entities()`: the registry as it stands BEFORE the statewide batches (seed + curation +
    the MODA batch), i.e. what an ABO row or a state agency must be checked against so it
    enriches an existing entity instead of duplicating it. Built from the same functions as
    build_registry.py (the sync_greenbook precedent), so the sync and the build cannot disagree.
  - The state agency listing reader (`data/cache/state_agency_listing_websites.csv`).
  - `write_review_section()`: data/statewide_web_review.json carries one section per sync
    script; each script rewrites only its own section, so re-running either is idempotent.
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build_registry as br  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
CURATION = ROOT / "data" / "curation.json"
REVIEW = ROOT / "data" / "statewide_web_review.json"
LISTING = CACHE / "state_agency_listing_websites.csv"

# The dated snapshot the cache copies came from (data/ai-readability/sources/<date>/).
SNAPSHOT_DATE = "2026-11-06"

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def split_url(raw: str | None) -> tuple[str | None, str]:
    """Return (lower-case host, path) for a URL-ish string. ('' path means the site root.)

    A bare host ('nysba.ny.gov') is accepted. An index file at the root ('/index.html') counts as
    a path: it is a page, and the growth plan records page-only presences in url_conventions[]."""
    raw = (raw or "").strip()
    if not raw:
        return None, ""
    p = urllib.parse.urlparse(raw if "//" in raw else "https://" + raw)
    host = (p.hostname or "").lower() or None
    path = p.path or ""
    if path == "/":
        path = ""
    return host, path


def norm_host(host: str) -> str:
    """Drop a leading www./www1. so 'www.bpca.ny.gov' and 'bpca.ny.gov' compare equal."""
    return re.sub(r"^www\d*\.", "", (host or "").lower())


def host_is_valid(host: str | None, tlds: set[str] | None = None) -> bool:
    """A syntactic check only (labels + a TLD; no 'wwww.' typo prefix). Never resolves DNS.

    With `tlds` (the top-level rules of the Public Suffix List, see `psl_tlds`), the last label
    must be a real TLD, which catches a truncated host like 'www.cityofyonkers'."""
    if not host or not _HOST_RE.match(host) or host.startswith("wwww."):
        return False
    return tlds is None or host.rsplit(".", 1)[-1] in tlds


def psl_tlds() -> set[str]:
    """Single-label rules of the committed Public Suffix List snapshot (publicsuffix.org)."""
    import ai_readability_export as ex  # local import: only the sync scripts need it
    rules, _, _ = ex.load_psl()
    return {r for r in rules if "." not in r}


def owned_hosts(entity: dict) -> set[str]:
    return {norm_host(w.get("domain", "")) for w in entity.get("web_properties", [])}


def load_curation(path: pathlib.Path = CURATION) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def base_entities() -> list[dict]:
    """Seed + curation + MODA batch only: the registry before any statewide record is merged."""
    seed = br._load(br.SEED, "entities")
    curation = br._load_obj(br.CURATION)
    seed = br.apply_seed_curation(seed, curation)
    moda_path = br.CACHE / "records_moda.json"
    if not moda_path.exists():
        # Without the MODA batch the ~300 MODA-derived city entities are invisible, and every
        # NYC-local authority (School Construction Authority, HDC, ...) would be minted twice.
        raise SystemExit(f"error: {moda_path} is missing; the statewide syncs match against the "
                         "seed + MODA registry and refuse to run without it.")
    moda = br._load(moda_path, "records")
    entities, _ = br.build_entities(seed, [moda])
    return br.apply_entity_curation(entities, curation)


def read_listing(path: pathlib.Path = LISTING) -> list[dict]:
    """Rows of the state agency listing: {domain, url, agency}. Hosts lower-cased."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = [{"domain": (r.get("domain") or "").strip().lower(),
                 "url": (r.get("url") or "").strip(),
                 "agency": (r.get("agency") or "").strip()} for r in csv.DictReader(fh)]
    return [r for r in rows if r["domain"]]


def listing_hosts_by_target(listing: list[dict], curation: dict) -> dict[tuple[str, str], list[str]]:
    """Group listing hosts by their curated target, e.g. ('abo', '<ABO name>') or ('agency', key).

    Target resolution: a per-host override (`state_listing_host_overrides`) wins, else the code's
    entry in `state_listing_codes`. A code with no entry raises: every code must be curated, so a
    new code in a refreshed listing is a build error, not a silent drop."""
    codes = curation.get("state_listing_codes", {})
    overrides = curation.get("state_listing_host_overrides", {})
    out: dict[tuple[str, str], list[str]] = {}
    for row in listing:
        target = overrides.get(row["domain"], {}).get("target") or (codes.get(row["agency"]) or {}).get("target")
        if target is None:
            raise ValueError(f"state listing code {row['agency']!r} (host {row['domain']}) has no "
                             "entry in data/curation.json state_listing_codes")
        kind, _, value = target.partition(":")
        out.setdefault((kind, value), [])
        if row["domain"] not in out[(kind, value)]:
            out[(kind, value)].append(row["domain"])
    return out


def write_review_section(section: str, items: list[dict], about: str, path: pathlib.Path = REVIEW) -> None:
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc.setdefault("_about", (
        "Statewide items held for human review, never applied to data/registry.json. One section "
        "per sync script (each rewrites only its own). Every item names its reason. Growth plan: "
        "docs/registry-growth-plan.md."))
    doc[section] = {"_about": about, "items": items}
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
