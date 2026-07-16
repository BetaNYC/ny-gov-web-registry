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

ACCESS GATE: this script does NOT fetch. It reads the built registry + the gated MODA cache and
writes a local artifact. The full URLs it records are public agency URLs (not a third-party data
dump), so `data/crawl_targets.json` is COMMITTED (parallel to data/nyc_boundaries_layers.json —
a git-committed derivative of a git-ignored cache).

Run: python scripts/crawl_targets.py  ->  writes data/crawl_targets.json

The derivation function `derive_targets(entities, url_by_goid)` is pure (no IO) so it is tested at
the seam with in-memory fixtures.
"""
from __future__ import annotations

import csv
import json
import pathlib

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


def derive_targets(entities: list[dict], url_by_goid: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Pure derivation. Returns (targets, no_url).

    `url_by_goid` maps a MODA record_id (e.g. 'NYC_GOID_000152') to its verbatim `url` cell (only
    non-empty URLs need be present). `targets` entries are {id, name, start_url, derivation}; the
    `no_url` list is {id, name} for entities with no recoverable URL. Order follows `entities`.
    """
    targets: list[dict] = []
    no_url: list[dict] = []
    for e in entities:
        start_url: str | None = None
        derivation: str | None = None

        # 1. Full URL from the MODA export (the only place the deep path survives).
        for goid in _goids(e):
            csv_url = (url_by_goid.get(goid) or "").strip()
            if csv_url:
                start_url, derivation = csv_url, "moda_csv_url"
                break

        # 2/3. Fall back to a web_property host (seed entities with no goid land here).
        if start_url is None:
            domain, role = _primary_domain(e)
            if domain:
                start_url, derivation = _url_from_domain(domain), role

        if start_url is None:
            no_url.append({"id": e["id"], "name": e.get("name")})
        else:
            targets.append({"id": e["id"], "name": e.get("name"),
                            "start_url": start_url, "derivation": derivation})
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

    targets, no_url = derive_targets(entities, url_by_goid)

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
