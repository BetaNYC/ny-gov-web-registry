"""sync_abo.py — map the ABO Directory of Public Authorities into registry records.

Source: NYS Authorities Budget Office, "Directory of Public Authorities"
        data.ny.gov dataset 4vym-q77x  (608 authorities; State/Local/IDA/LDC)
        https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x

Columns are DOCUMENTED and were verified from the dataset's about page on 2026-07-11:
    public_authority_type, public_authority_name,
    address_line_1, address_line_2, city, state, zip,
    website (type URL; added 2026-01-15; self-reported, blank where not provided),
    georeference
There is NO explicit authority-id column, so crosswalk.abo_id cannot be populated
from this dataset (see docs/sources.md).

ACCESS GATE: this script does NOT fetch. Live data pulls are gated pending explicit
operator authorization. Place a CSV/JSON export at data/cache/abo_4vym-q77x.csv first.
Run: python scripts/sync_abo.py  ->  writes data/cache/records_abo.json
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

CACHE = pathlib.Path(__file__).resolve().parent.parent / "data" / "cache"
SOURCE = CACHE / "abo_4vym-q77x.csv"
OUT = CACHE / "records_abo.json"

# ABO public_authority_type -> our government_level (design doc §2).
# State/Local/IDA/LDC are all "authority" at the coarse level; entity_type keeps the class.
_LEVEL = {
    "State": ("authority", "state-authority"),
    "Local": ("authority", "local-authority"),
    "Industrial Development Agency": ("authority", "industrial-development-agency"),
    "Local Development Corporation": ("authority", "local-development-corporation"),
}


def to_record(row: dict) -> dict:
    level, entity_type = _LEVEL.get(row.get("public_authority_type", "").strip(), ("authority", None))
    website = (row.get("website") or "").strip()
    web_properties = []
    if website:
        # Store the registrable host, not the full URL (schema: domain, not URL/path).
        host = website.replace("https://", "").replace("http://", "").split("/")[0].strip().lower()
        if host:
            web_properties.append({"domain": host, "role": "primary",
                                   "valid_from": None, "valid_to": None,
                                   "notes": "From ABO Directory `website` field (self-reported)."})
    return {
        # betanyc_id is assigned by build_registry.py during merge, not here.
        "name": (row.get("public_authority_name") or "").strip(),
        "government_level": level,
        "entity_type": entity_type,
        "crosswalk": {"moda_govid": None, "abo_id": None, "wikidata_qid": None,
                      "irs_ein": None, "opendata_dataset": None},  # abo_id: no id column upstream
        "web_properties": web_properties,
        "jurisdiction": {"boundary_ref": None, "coverage": "statewide", "boundary_note": None},
        "status": "active",
        "provenance": {"sources": ["abo"], "last_verified": None},
        "_source_address": {k: (row.get(k) or "").strip()
                            for k in ("address_line_1", "address_line_2", "city", "state", "zip")},
    }


def main() -> int:
    if not SOURCE.exists():
        print(f"[gated] no source export at {SOURCE}\n"
              f"        download 4vym-q77x from data.ny.gov (manual, gated) and retry.", file=sys.stderr)
        return 2
    with SOURCE.open(newline="", encoding="utf-8-sig") as fh:
        records = [to_record(r) for r in csv.DictReader(fh)]
    OUT.write_text(json.dumps({"records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(records)} ABO records -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
