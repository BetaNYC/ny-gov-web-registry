"""sync_moda.py — map MODA's NYC governance-organizations registry into registry records.

Source: MODA-NYC/nyc-governance-organizations (MIT)
        https://github.com/MODA-NYC/nyc-governance-organizations
        also NYC Open Data dataset t3jq-9nkf
        NYC governance organizations incl. boards/commissions/advisory bodies; has a `url`
        field (current site only — no legacy-domain history) and an immutable record id.

FIELD MAP VERIFIED 2026-07-15 against the actual gated export
(data/cache/moda_nyc-governance-organizations.csv, 306 rows + header, 17 columns
downloaded from NYC Open Data t3jq-9nkf rows.csv). The 17 columns are:
    record_id, operational_status, organization_type, name, acronym, name_alphabetized,
    url, alternate_or_former_names, alternate_or_former_acronyms, principal_officer_title,
    principal_officer_full_name, principal_officer_first_name, principal_officer_last_name,
    principal_officer_contact_url, reports_to, in_org_chart, listed_in_nyc_gov_agency_directory

NOTE ON record_id FORMAT: the export carries the NYC_GOID_XXXXXX form (e.g.
"NYC_GOID_000476"), NOT the bare 6-digit numeric the phase-0 docstring anticipated for
Phase II. It is stored VERBATIM AS A STRING in an identifiers[] entry under scheme
"nyc_goid" (docs/schemes.md), so either form round-trips losslessly. Do not normalize or
strip the prefix. Re-verify the field map if MODA revises the schema.

MAPPINGS (schema v2, issue #1 phase 1):
  record_id                       -> identifiers[{scheme:"nyc_goid"}]   (verbatim string)
  organization_type               -> classification
  name                            -> name
  acronym                         -> short_name  AND  other_names[]{note:"acronym"}
  alternate_or_former_names (;)   -> other_names[]{note:"alternate or former name"}
  alternate_or_former_acronyms(;) -> other_names[]{note:"alternate or former acronym"}
  url                             -> web_properties[]{role:"primary"}    (host only)
  operational_status              -> ACTIVE-ONLY GATE: only "Active" rows emit a record;
                                     the emitted status is "active".

DELIBERATELY NOT CARRIED IN PHASE 1 (no schema home; phase-0 schema is not extended here):
  - principal_officer_* : personnel identity, and the schema has no person/officer structure.
    contact_details[] is for contact POINTS (email/voice/address), not officer identity, so
    stuffing a name there would misuse the field. Deferred to phase 2 (add an officer/person
    structure, or map principal_officer_contact_url -> contact_details then).
  - listed_in_nyc_gov_agency_directory : no home (top-level and provenance are
    additionalProperties:false). This is exactly the flag phase 2 validates against the
    nyc.gov directory scrape (issue #1 user story 13). Deferred to phase 2.
  - reports_to : a hierarchy reference by NAME; resolving it to parent_id requires the merge's
    minted ids (a second pass). Deferred (hierarchy resolution is not phase-1 scope).
  - name_alphabetized, in_org_chart : cosmetic / display-only; no schema home.

There are no establishing-authority / charter / EO columns in this export, so mandates[]
cannot be seeded from it (contrary to the phase-0 note that assumed such fields). mandates[]
population is left to the City Record archive curation phase.

OUTPUT SHAPE: schema v2. This emits valid v2 records (id is minted by build_registry.py
during merge, not here). nycresolver tier-gated fuzzy matching is NOT implemented in this
phase — the build seam matches identifier-scheme-first then EXACT normalized name only, so
near-duplicate names (e.g. MODA "Department of Education" vs seed "New York City Department
of Education") are minted as separate records and are candidates for a later nycresolver pass.

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

# Verified 2026-07-15 against the actual export (see module docstring).
FIELD = {
    "record_id": "record_id",                 # immutable MODA primary key -> nyc_goid identifier
    "status": "operational_status",           # active-only gate
    "org_type": "organization_type",          # -> classification
    "name": "name",                           # entity name
    "acronym": "acronym",                     # current short name/acronym
    "url": "url",                             # current official website
    "alt_names": "alternate_or_former_names",         # ; -delimited
    "alt_acronyms": "alternate_or_former_acronyms",   # ; -delimited
}

# Multi-value cells in this export are semicolon-delimited (e.g. "Department of Sanitation;NYC Sanitation").
MULTIVALUE_SEP = ";"


def _split_multi(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(MULTIVALUE_SEP) if part.strip()]


def _is_active(row: dict) -> bool:
    return (row.get(FIELD["status"]) or "").strip().lower() == "active"


def to_record(row: dict) -> dict:
    """Map one CSV row to a v2 registry record (without an `id` — build_registry mints that).

    Assumes the row is Active; callers gate on _is_active before emitting. Kept assumption-free
    on other fields so the offline fixture tests can exercise the shape directly.
    """
    # web_properties: current url -> host-only primary property.
    url = (row.get(FIELD["url"]) or "").strip()
    web_properties = []
    if url:
        host = url.replace("https://", "").replace("http://", "").split("/")[0].strip().lower()
        if host:
            web_properties.append({"domain": host, "role": "primary",
                                   "valid_from": None, "valid_to": None,
                                   "notes": "From MODA `url` field (current site only)."})

    # identifiers: record_id -> nyc_goid, stored verbatim as a string (NYC_GOID_XXXXXX form).
    record_id = (row.get(FIELD["record_id"]) or "").strip()
    identifiers = []
    if record_id:
        identifiers.append({"scheme": "nyc_goid", "identifier": record_id})

    # names: current acronym -> short_name (schema's convenience field, matches the seed's
    # convention) and ALSO into other_names[] as a variant; former names/acronyms -> other_names[].
    acronym = (row.get(FIELD["acronym"]) or "").strip()
    other_names = []
    if acronym:
        other_names.append({"name": acronym, "note": "acronym (MODA)"})
    for former in _split_multi(row.get(FIELD["alt_names"])):
        other_names.append({"name": former, "note": "alternate or former name (MODA)"})
    for former_acr in _split_multi(row.get(FIELD["alt_acronyms"])):
        other_names.append({"name": former_acr, "note": "alternate or former acronym (MODA)"})

    return {
        # `id` is minted by build_registry.py during merge, not here.
        "name": (row.get(FIELD["name"]) or "").strip(),
        "short_name": acronym or None,
        "other_names": other_names,
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
        rows = list(csv.DictReader(fh))

    active = [r for r in rows if _is_active(r)]
    skipped = len(rows) - len(active)
    records = [to_record(r) for r in active]

    OUT.write_text(json.dumps({"records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"read {len(rows)} rows; {len(active)} Active -> {len(records)} records "
          f"({skipped} non-Active skipped) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
