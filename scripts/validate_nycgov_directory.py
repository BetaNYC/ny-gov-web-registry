"""validate_nycgov_directory.py — validate the nyc.gov agency directory against the registry.

DISCOVERY (2026-07-15): the nyc.gov agency-directory JSON endpoint is powered by the SAME
upstream as the canonical dataset — MODA / NYC Open Data `t3jq-9nkf` (identical `record_id`
values, `NYC_GOID_XXXXXX`). So this is a VALIDATION input, not a new entity source: the directory
should be a subset of the registry, and it carries the `listed_in_nyc_gov_agency` flag that
`sync_moda` deferred (issue #1 user story 13). We therefore assert CONSISTENCY rather than mint.

Checks (offline; reads the operator-placed cache + the built registry):
  1. record_ids in the directory ⊆ registry nyc_goids (same upstream => strict subset).
  2. reports any drift both ways (directory ids missing from registry; registry nyc_goids absent
     from the directory export).
  3. summarizes the listed_in_nyc_gov_agency flag distribution for downstream use.

ACCESS GATE: does NOT fetch. Place the export at data/cache/nycgov_agencydirectory.json.
Run: python scripts/validate_nycgov_directory.py   (exit 0 = subset holds; exit 3 = drift found)
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "data" / "cache" / "nycgov_agencydirectory.json"
REGISTRY = ROOT / "data" / "registry.json"
DIRECTORY_FETCHED = "2026-07-15"


def validate(directory_records: list[dict], entities: list[dict]) -> dict:
    """Pure: compare directory record_ids against registry nyc_goids. Returns a report dict."""
    dir_ids = {str(r.get("record_id", "")).strip() for r in directory_records if r.get("record_id")}
    reg_goids = {
        i["identifier"] for e in entities for i in e.get("identifiers", [])
        if i.get("scheme") == "nyc_goid"
    }
    listed_true = sum(1 for r in directory_records if r.get("listed_in_nyc_gov_agency"))
    missing = sorted(dir_ids - reg_goids)  # directory ids with no registry home => real drift
    return {
        "directory_fetched": DIRECTORY_FETCHED,
        "directory_records": len(directory_records),
        "directory_distinct_ids": len(dir_ids),
        "registry_nyc_goids": len(reg_goids),
        "subset_holds": not missing,
        "directory_ids_missing_from_registry": missing,
        "registry_goids_absent_from_directory": sorted(reg_goids - dir_ids),
        "listed_in_nyc_gov_agency_true": listed_true,
    }


def main() -> int:
    if not DIRECTORY.exists():
        print(f"[gated] no directory export at {DIRECTORY}", file=sys.stderr)
        return 2
    if not REGISTRY.exists():
        print(f"error: {REGISTRY} not found — run build_registry.py first.", file=sys.stderr)
        return 1
    directory = json.loads(DIRECTORY.read_text(encoding="utf-8"))
    entities = json.loads(REGISTRY.read_text(encoding="utf-8")).get("entities", [])
    rep = validate(directory, entities)
    print(f"directory records: {rep['directory_records']} | registry nyc_goids: {rep['registry_nyc_goids']}")
    print(f"subset holds (directory ⊆ registry): {rep['subset_holds']}")
    if rep["directory_ids_missing_from_registry"]:
        print(f"  DRIFT — directory ids missing from registry: {rep['directory_ids_missing_from_registry']}")
    if rep["registry_goids_absent_from_directory"]:
        print(f"  note — registry nyc_goids absent from this directory export: "
              f"{len(rep['registry_goids_absent_from_directory'])}")
    print(f"listed_in_nyc_gov_agency = true: {rep['listed_in_nyc_gov_agency_true']}")
    return 0 if rep["subset_holds"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
