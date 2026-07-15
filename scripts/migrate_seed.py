"""migrate_seed.py — one-shot, lossless migration of the v1 seed to the v2 schema.

v2 generalizes the schema for reuse beyond one steward (issue #1, phase 0):
  - betanyc_id -> opaque neutral `id`; the old value is preserved as an
    identifiers[] entry under the legacy scheme `betanyc_org_legacy` (nothing lost).
  - crosswalk{...} -> identifiers[] {scheme, identifier} (scheme-based, extensible).
  - entity_type -> classification; valid_from/valid_to -> founding_date/dissolution_date.
  - jurisdiction.boundary_ref -> areas[]; jurisdiction.coverage + boundary_note -> area_note.
  - established_by_eo[] -> mandates[] (authority_type=executive_order, role=establishing).
  - succeeds_ids / succeeded_by_ids -> relations[] (successor_of / predecessor_of).

Deterministic and idempotent: it always reads the canonical v1 snapshot
(tests/fixtures/registry.seed.v1.json) and rewrites data/registry.seed.json. No network.

Run: python scripts/migrate_seed.py
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
V1_SNAPSHOT = ROOT / "tests" / "fixtures" / "registry.seed.v1.json"
OUT = ROOT / "data" / "registry.seed.json"

# Legacy scheme carrying the retired BNYC-ORG-NNNNNN key so the migration loses nothing.
LEGACY_ID_SCHEME = "betanyc_org_legacy"

# v1 crosswalk field -> v2 identifier scheme (docs/schemes.md). Applied only to non-null values.
CROSSWALK_SCHEME = {
    "moda_govid": "nyc_goid",
    "abo_id": "nys_abo",
    "wikidata_qid": "wikidata",
    "irs_ein": "us_irs_ein",
    "opendata_dataset": "socrata_dataset",
}

# Curated neutral slugs for the known anchor set (nicer than a raw name-slug).
# Any id not listed here falls back to a unique slug of short_name/name.
ID_OVERRIDES = {
    "BNYC-ORG-000001": "mta",
    "BNYC-ORG-000002": "lirr",
    "BNYC-ORG-000003": "mnr",
    "BNYC-ORG-000004": "nyct",
    "BNYC-ORG-000005": "tbta",
    "BNYC-ORG-000006": "panynj",
    "BNYC-ORG-000007": "nycedc",
    "BNYC-ORG-000008": "nyc-health-hospitals",
    "BNYC-ORG-000009": "cuny",
    "BNYC-ORG-000010": "nycha",
    "BNYC-ORG-000011": "nyserda",
    "BNYC-ORG-000012": "nys-thruway",
    "BNYC-ORG-000013": "dasny",
    "BNYC-ORG-000014": "esd",
    "BNYC-ORG-000015": "oti",
    "BNYC-ORG-000016": "nyc-doe",
    "BNYC-ORG-000017": "nyc-mayor",
}


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "entity"


def build_id_map(v1_entities: list[dict]) -> dict[str, str]:
    """Old betanyc_id -> new neutral id, collision-free and deterministic."""
    id_map: dict[str, str] = {}
    used: set[str] = set()
    for e in v1_entities:
        old = e["betanyc_id"]
        base = ID_OVERRIDES.get(old) or slugify(e.get("short_name") or e.get("name"))
        candidate, n = base, 1
        while candidate in used:
            n += 1
            candidate = f"{base}-{n}"
        used.add(candidate)
        id_map[old] = candidate
    return id_map


def migrate_entity(v1: dict, id_map: dict[str, str]) -> dict:
    identifiers = [{"scheme": LEGACY_ID_SCHEME, "identifier": v1["betanyc_id"]}]
    for field, scheme in CROSSWALK_SCHEME.items():
        val = (v1.get("crosswalk") or {}).get(field)
        if val:
            identifiers.append({"scheme": scheme, "identifier": str(val)})

    jur = v1.get("jurisdiction") or {}
    areas: list[dict] = []
    bref = jur.get("boundary_ref")
    if bref:
        areas.append({"scheme": "nyc-boundaries", "layer": bref["layer"],
                      "id": bref.get("id"), "role": "jurisdiction"})
    note_parts = []
    if jur.get("coverage"):
        note_parts.append(f"coverage: {jur['coverage']}")
    if jur.get("boundary_note"):
        note_parts.append(jur["boundary_note"])
    area_note = ". ".join(note_parts) if note_parts else None

    mandates = [{"authority_type": "executive_order", "citation": eo["eo_id"],
                 "url": eo.get("source_pdf_url"), "date": None, "role": "establishing"}
                for eo in v1.get("established_by_eo", [])]

    relations = [{"type": "successor_of", "target_id": id_map.get(s, s), "date": None}
                 for s in v1.get("succeeds_ids", [])]
    relations += [{"type": "predecessor_of", "target_id": id_map.get(s, s), "date": None}
                  for s in v1.get("succeeded_by_ids", [])]

    parent = v1.get("parent_id")
    return {
        "id": id_map[v1["betanyc_id"]],
        "name": v1["name"],
        "short_name": v1.get("short_name"),
        "other_names": [],
        "government_level": v1["government_level"],
        "classification": v1.get("entity_type"),
        "parent_id": id_map.get(parent) if parent else None,
        "child_ids": [id_map[c] for c in v1.get("child_ids", [])],
        "identifiers": identifiers,
        "web_properties": v1.get("web_properties", []),
        "url_conventions": v1.get("url_conventions", []),
        "areas": areas,
        "area_note": area_note,
        "mandates": mandates,
        "relations": relations,
        "status": v1["status"],
        "founding_date": v1.get("valid_from"),
        "dissolution_date": v1.get("valid_to"),
        "provenance": v1["provenance"],
    }


def migrate_seed(v1_doc: dict) -> dict:
    entities = v1_doc["entities"]
    id_map = build_id_map(entities)
    return {
        "_about": "Provisional hand-authored anchor-set SEED (schema v2, steward-neutral) — "
                  "NOT the built dataset. Exercises the schema and build pipeline and anchors the "
                  "v1 marquee-authority scope. Only confidently-stateable values are populated: "
                  "names, well-known primary domains, parent/child structure. External keys are "
                  "carried as identifiers[] {scheme, identifier}; the only populated scheme here is "
                  "betanyc_org_legacy (the retired BNYC-ORG id, preserved so migration loses nothing). "
                  "All verified-only schemes (nyc_goid, wikidata, us_irs_ein, ...) are absent because "
                  "none were verified in this build (guardrail: never invent an id, EIN, or boundary id).",
        "_generated": "2026-07-15",
        "_schema": "../schema/property.schema.json",
        "_migrated_by": "scripts/migrate_seed.py (v1 -> v2, lossless)",
        "_scope": "v1 anchor set: MTA family + marquee NYC/NYS authorities & PBCs + a few obvious city entities.",
        "entities": [migrate_entity(e, id_map) for e in entities],
    }


def main() -> int:
    v1_doc = json.loads(V1_SNAPSHOT.read_text(encoding="utf-8"))
    out_doc = migrate_seed(v1_doc)
    OUT.write_text(json.dumps(out_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"migrated {len(out_doc['entities'])} entities v1 -> v2 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
