"""sync_moda.py — map MODA's NYC governance-organizations registry into registry records.

Source: MODA-NYC/nyc-governance-organizations (MIT)
        https://github.com/MODA-NYC/nyc-governance-organizations
        also NYC Open Data dataset t3jq-9nkf
        ~434 city entities incl. boards/commissions/advisory bodies; has a `url` field
        (current site only — no legacy-domain history) and an immutable record id.

FIELD MAP VERIFIED 2026-07-11 against MODA's Phase II published schema
(schemas/nycgo_published_dataset.tableschema.json, 25 public fields): the columns
record_id, name, url, organization_type exist as named below. NOTE: record_id is a
6-digit numeric in Phase II (NYC_GOID_XXXXXX in Phase I) — it is stored verbatim as a
string in an identifiers[] entry under scheme "nyc_goid" (docs/schemes.md). Still do
not guess if MODA revises the schema; re-verify.

OUTPUT SHAPE: schema v2 (issue #1, phase 0). record_id -> identifiers[{scheme:"nyc_goid"}];
organization_type -> classification. This emits valid v2 records; full phase-1 population
(mandates from establishing-authority fields, areas, nycresolver matching) is out of scope.

ACCESS GATE: this script does NOT fetch. Place a MODA export at
data/cache/moda_nyc-governance-organizations.csv first.
Run: python scripts/sync_moda.py  ->  writes data/cache/records_moda.json
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

CACHE = pathlib.Path(__file__).resolve().parent.parent / "data" / "cache"
SOURCE = CACHE / "moda_nyc-governance-organizations.csv"
OUT = CACHE / "records_moda.json"

# Verified 2026-07-11 against MODA Phase II published schema (see module docstring).
FIELD = {
    "record_id": "record_id",       # immutable MODA primary key -> crosswalk.moda_govid
    "name": "name",                 # entity name
    "url": "url",                   # current official website
    "org_type": "organization_type",  # -> entity_type
}


def to_record(row: dict) -> dict:
    url = (row.get(FIELD["url"]) or "").strip()
    web_properties = []
    if url:
        host = url.replace("https://", "").replace("http://", "").split("/")[0].strip().lower()
        if host:
            web_properties.append({"domain": host, "role": "primary",
                                   "valid_from": None, "valid_to": None,
                                   "notes": "From MODA `url` field (current site only)."})
    record_id = (row.get(FIELD["record_id"]) or "").strip()
    identifiers = []
    if record_id:
        identifiers.append({"scheme": "nyc_goid", "identifier": record_id})
    return {
        # `id` is minted by build_registry.py during merge, not here.
        "name": (row.get(FIELD["name"]) or "").strip(),
        "government_level": "nyc",
        "classification": (row.get(FIELD["org_type"]) or "").strip() or None,
        "identifiers": identifiers,
        "web_properties": web_properties,
        "areas": [],
        "area_note": None,
        "status": "active",
        "provenance": {"sources": ["moda"], "last_verified": None},
    }


def main() -> int:
    if not SOURCE.exists():
        print(f"[gated] no source export at {SOURCE}\n"
              f"        download nyc-governance-organizations (manual, gated) and retry.", file=sys.stderr)
        return 2
    with SOURCE.open(newline="", encoding="utf-8-sig") as fh:
        records = [to_record(r) for r in csv.DictReader(fh)]
    OUT.write_text(json.dumps({"records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(records)} MODA records -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
