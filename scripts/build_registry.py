"""build_registry.py — merge the seed + synced source records into data/registry.json.

Pipeline:
    data/registry.seed.json                      (hand-authored anchor set; source of truth for structure)
    data/cache/records_moda.json    (optional)   (from sync_moda.py)
    data/cache/records_abo.json     (optional)   (from sync_abo.py)
    data/cache/records_nygov.json   (optional)   (from sync_nygov.py — not yet produced)
        |
        v
    data/registry.json  +  schema validation

Merge discipline (issue #1):
  - Seed records are authoritative and keep their `id` (ids are stable, never rewritten).
  - A source record is matched to an existing record by IDENTIFIER SCHEME FIRST — any shared
    {scheme, identifier} pair — then by normalized name. A new source record is minted a
    fresh neutral id exactly once.
  - Removals/renames are NOT silent deletes — they surface in a proposals report
    (build_proposals.json) for human confirmation; this build never drops a record.

The merge logic lives in the pure function `build_entities(...)` so it can be tested at the
built-registry seam with in-memory fixtures (no file IO, no network). Runs OFFLINE against
whatever is in data/cache/; with no cache files it validates + republishes the seed.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "registry.seed.json"
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "registry.json"
PROPOSALS = ROOT / "data" / "build_proposals.json"
SCHEMA = ROOT / "schema" / "property.schema.json"

SOURCE_FILES = ("records_moda.json", "records_abo.json", "records_nygov.json")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "entity"


def _pairs(entity: dict) -> list[tuple[str, str]]:
    """The {scheme, identifier} pairs on a record, as hashable tuples."""
    return [(i["scheme"], i["identifier"]) for i in entity.get("identifiers", [])
            if i.get("scheme") and i.get("identifier")]


def _mint_id(name: str, used: set[str]) -> str:
    base, candidate, n = _slugify(name), _slugify(name), 1
    while candidate in used:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def build_entities(seed_entities: list[dict], source_batches: list[list[dict]]) -> tuple[list[dict], list[dict]]:
    """Merge source record batches into a copy of the seed. Pure; no IO.

    Returns (entities, proposals). Matching is identifier-scheme-first, then normalized name.
    Existing records keep their id; unmatched source records are minted a new neutral id.
    Never deletes; enrichment and additions are recorded as proposals.
    """
    entities = [dict(e) for e in seed_entities]
    by_pair: dict[tuple[str, str], dict] = {}
    for e in entities:
        for pair in _pairs(e):
            by_pair.setdefault(pair, e)
    by_name = {_norm(e["name"]): e for e in entities}
    used_ids = {e["id"] for e in entities}
    proposals: list[dict] = []

    for batch in source_batches:
        for rec in batch:
            match = None
            for pair in _pairs(rec):
                if pair in by_pair:
                    match = by_pair[pair]
                    break
            if match is None:
                match = by_name.get(_norm(rec.get("name", "")))

            if match is None:
                new_id = _mint_id(rec.get("name", ""), used_ids)
                used_ids.add(new_id)
                new_rec = {k: v for k, v in rec.items() if k != "id"}
                new_rec["id"] = new_id
                entities.append(new_rec)
                by_name[_norm(new_rec.get("name", ""))] = new_rec
                for pair in _pairs(new_rec):
                    by_pair.setdefault(pair, new_rec)
                proposals.append({"action": "add", "id": new_id, "name": new_rec.get("name")})
            else:
                # Enrich: add source identifier pairs the matched record lacks (never overwrite).
                existing = set(_pairs(match))
                for pair in _pairs(rec):
                    if pair not in existing:
                        match.setdefault("identifiers", []).append(
                            {"scheme": pair[0], "identifier": pair[1]})
                        existing.add(pair)
                        by_pair.setdefault(pair, match)
                        proposals.append({"action": "enrich", "id": match["id"],
                                          "identifier": {"scheme": pair[0], "identifier": pair[1]}})
    return entities, proposals


def _load(path: pathlib.Path, key: str) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get(key, [])


def main() -> int:
    seed = _load(SEED, "entities")
    if not seed:
        print(f"error: no seed entities at {SEED}", file=sys.stderr)
        return 1

    batches = [_load(CACHE / name, "records") for name in SOURCE_FILES]
    entities, proposals = build_entities(seed, batches)

    OUT.write_text(json.dumps({"_generated_from": "build_registry.py", "entities": entities},
                              indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    PROPOSALS.write_text(json.dumps({"proposals": proposals}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(entities)} entities -> {OUT}  ({len(proposals)} proposal(s))")

    # Best-effort schema validation (skip cleanly if jsonschema isn't installed).
    try:
        import jsonschema  # noqa: WPS433
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        for e in entities:
            jsonschema.validate(e, schema)
        print("schema: all entities valid")
    except ModuleNotFoundError:
        print("schema: jsonschema not installed — skipped validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
