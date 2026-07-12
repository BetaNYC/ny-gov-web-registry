"""sync_moda.py — map MODA's NYC governance-organizations registry into registry records.

Source: MODA-NYC/nyc-governance-organizations (MIT)
        https://github.com/MODA-NYC/nyc-governance-organizations
        also NYC Open Data dataset t3jq-9nkf
        ~434 city entities incl. boards/commissions/advisory bodies; has a `url` field
        (current site only — no legacy-domain history) and an immutable record id.

⚠️ FIELD MAP UNVERIFIED. The exact upstream column names below are NOT yet confirmed
against MODA's published data dictionary. Confirm each against the repo's data
dictionary before the first real run, and do not guess — leave a field null if the
upstream column can't be identified. (Build-against-docs rule.)

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

# TODO(verify against MODA data dictionary): confirm these column names before running.
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
    return {
        "name": (row.get(FIELD["name"]) or "").strip(),
        "government_level": "nyc",
        "entity_type": (row.get(FIELD["org_type"]) or "").strip() or None,
        "crosswalk": {"moda_govid": (row.get(FIELD["record_id"]) or "").strip() or None,
                      "abo_id": None, "wikidata_qid": None, "irs_ein": None, "opendata_dataset": None},
        "web_properties": web_properties,
        "jurisdiction": {"boundary_ref": None, "coverage": "citywide", "boundary_note": None},
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
