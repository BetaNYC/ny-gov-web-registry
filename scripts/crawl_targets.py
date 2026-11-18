"""crawl_targets.py — derive the about-crawler's start-URL set into a committed artifact.

WHY THIS EXISTS (issue #1 phase-5 scope, decision 1):
  The registry stores `web_properties[]` as BARE HOSTS (a phase-1 limitation of sync_moda, which
  strips MODA's `url` to host-only). That is insufficient for the many NYC entities that live at a
  DEEP nyc.gov path — e.g. Sanitation is `https://www.nyc.gov/site/dsny/index.page`, not the apex
  `nyc.gov`. Crawling the bare host would land on the citywide homepage, not the agency's own site.

  So this step RECOVERS each entity's full start URL and emits a reviewable, reproducible crawl-set
  artifact (`data/crawl_targets.json`) BEFORE any fetch happens. The crawl set is thus auditable in
  a diff, independent of the networked crawler.

DERIVATION (per entity, in priority order — the first that yields a URL wins):
  1. `moda_csv_url` — the entity carries a `nyc_goid` identifier AND that goid's row in the MODA
     export (`data/cache/moda_nyc-governance-organizations.csv`, `url` column) has a non-empty URL.
     This is the ONLY source of the full path (nyc.gov/site/<slug>/...); web_properties lost it.
  2. `web_property_primary` — no usable MODA URL, but the entity has a `primary` web_property; the
     start URL is `https://<domain>/` (seed entities like the MTA live here — they have no goid).
  3. `web_property_other` — no primary, but some web_property exists; use the first, same shaping.
  4. `no_url` — no URL anywhere. Recorded explicitly (never guessed) so coverage gaps are visible.

MARKED HOSTS: a host in data/ai-readability/website-corrections.csv (checked by hand and found no
longer the body's website: spam, for sale, a lookalike, a redirect to an unrelated site) never
becomes a start URL. A candidate on a marked host does not yield a URL, so derivation moves on to
the next rule; the entity's entry lists the hosts it passed over in `skipped_marked_hosts`. If
nothing unmarked is left, the entity is `no_url`, with the same list. Hosts compare without a
leading `www.` (statewide_common.norm_host).

ACCESS GATE: this script does NOT fetch. It reads the built registry + the gated MODA cache + the
committed website-corrections.csv and writes a local artifact. The full URLs it records are public agency URLs (not a third-party data
dump), so `data/crawl_targets.json` is COMMITTED (parallel to data/nyc_boundaries_layers.json —
a git-committed derivative of a git-ignored cache).

Run: python scripts/crawl_targets.py  ->  writes data/crawl_targets.json

The derivation function `derive_targets(entities, url_by_goid, corrections)` is pure (no IO) so it
is tested at the seam with in-memory fixtures.
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import statewide_common as sc  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "registry.json"
MODA_CSV = ROOT / "data" / "cache" / "moda_nyc-governance-organizations.csv"
OUT = ROOT / "data" / "crawl_targets.json"

# The crawl-set artifact carries the fetch date of its inputs for provenance/freshness.
INPUTS_AS_OF = "2026-07-15"


def _goids(entity: dict) -> list[str]:
    return [i["identifier"] for i in entity.get("identifiers", [])
            if i.get("scheme") == "nyc_goid" and i.get("identifier")]


def _primary_domain(entity: dict) -> tuple[str | None, str | None]:
    """Return (domain, role) of the best web_property to seed from: a `primary` role first,
    else the first property present. (None, None) if the entity owns no web property."""
    wps = entity.get("web_properties", [])
    for wp in wps:
        if wp.get("role") == "primary" and wp.get("domain"):
            return wp["domain"], "web_property_primary"
    for wp in wps:
        if wp.get("domain"):
            return wp["domain"], "web_property_other"
    return None, None


def _url_from_domain(domain: str) -> str:
    """Shape a bare host into an https start URL. Domains are stored host-only (no scheme/path)."""
    return f"https://{domain.strip().rstrip('/')}/"


def derive_targets(entities: list[dict], url_by_goid: dict[str, str],
                   corrections: dict[str, dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Pure derivation. Returns (targets, no_url).

    `url_by_goid` maps a MODA record_id (e.g. 'NYC_GOID_000152') to its verbatim `url` cell (only
    non-empty URLs need be present). `corrections` ({norm_host: row}, statewide_common.
    load_corrections; None = no marks) holds back marked hosts: a candidate on one is passed over
    and the next rule applies. `targets` entries are {id, name, start_url, derivation}; the
    `no_url` list is {id, name} for entities with no recoverable URL. Either entry also carries
    `skipped_marked_hosts` when a marked host was passed over. Order follows `entities`.
    """
    corrections = corrections or {}
    targets: list[dict] = []
    no_url: list[dict] = []
    for e in entities:
        start_url: str | None = None
        derivation: str | None = None
        skipped: list[str] = []

        def marked(host: str | None) -> bool:
            if host and sc.norm_host(host) in corrections:
                if host not in skipped:
                    skipped.append(host)
                return True
            return False

        # 1. Full URL from the MODA export (the only place the deep path survives).
        for goid in _goids(e):
            csv_url = (url_by_goid.get(goid) or "").strip()
            if csv_url and not marked(sc.split_url(csv_url)[0]):
                start_url, derivation = csv_url, "moda_csv_url"
                break

        # 2/3. Fall back to a web_property host (seed entities with no goid land here).
        if start_url is None:
            wps = [w for w in e.get("web_properties", []) if not marked(w.get("domain"))]
            domain, role = _primary_domain({"web_properties": wps})
            if domain:
                start_url, derivation = _url_from_domain(domain), role

        entry = {"id": e["id"], "name": e.get("name")}
        if start_url is not None:
            entry.update(start_url=start_url, derivation=derivation)
        if skipped:
            entry["skipped_marked_hosts"] = skipped
        (no_url if start_url is None else targets).append(entry)
    return targets, no_url


def load_url_by_goid(csv_path: pathlib.Path) -> dict[str, str]:
    """Map each MODA record_id -> its `url` cell (non-empty only). Empty if the cache is absent —
    derivation then falls back to web_properties, which is still useful for the seed entities."""
    if not csv_path.exists():
        return {}
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    out: dict[str, str] = {}
    for r in rows:
        rid = (r.get("record_id") or "").strip()
        url = (r.get("url") or "").strip()
        if rid and url:
            out[rid] = url
    return out


def main() -> int:
    if not REGISTRY.exists():
        raise SystemExit(f"error: no built registry at {REGISTRY}. Run scripts/build_registry.py first.")
    entities = json.loads(REGISTRY.read_text(encoding="utf-8")).get("entities", [])
    url_by_goid = load_url_by_goid(MODA_CSV)
    if not url_by_goid:
        print(f"[warn] no MODA cache at {MODA_CSV}; deriving from web_properties only "
              "(deep nyc.gov/site paths will be unavailable).")

    targets, no_url = derive_targets(entities, url_by_goid, sc.load_corrections())

    by_derivation: dict[str, int] = {}
    for t in targets:
        by_derivation[t["derivation"]] = by_derivation.get(t["derivation"], 0) + 1

    OUT.write_text(json.dumps({
        "_generated_from": "crawl_targets.py",
        "inputs_as_of": INPUTS_AS_OF,
        "counts": {"targets": len(targets), "no_url": len(no_url), "by_derivation": by_derivation},
        "targets": targets,
        "no_url": no_url,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {len(targets)} crawl targets, {len(no_url)} no_url -> {OUT}")
    print(f"  by derivation: {by_derivation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
