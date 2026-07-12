"""build_registry.py — merge the seed + synced source records into data/registry.json.

Pipeline:
    data/registry.seed.json                      (hand-authored anchor set; source of truth for structure)
    data/cache/records_moda.json    (optional)   (from sync_moda.py)
    data/cache/records_abo.json     (optional)   (from sync_abo.py)
    data/cache/records_nygov.json   (optional)   (from sync_nygov.py — not yet produced)
        |
        v
    data/registry.json  +  schema validation

Merge discipline (design doc §4):
  - Seed records are authoritative and keep their betanyc_id.
  - Source records are matched to existing records by crosswalk id first, then by
    normalized name. A NEW source record is assigned the next free BNYC-ORG-NNNNNN.
  - Removals/renames are NOT silent deletes — they surface as a proposals report
    (build_proposals.json) for human confirmation; this script never drops a record.

Runs OFFLINE against whatever is in data/cache/. With no cache files present it simply
validates + republishes the seed as registry.json (useful right now).
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
SCHEMA = ROOT / "schema" / "property.schema.json"

_ID_RE = re.compile(r"^BNYC-ORG-(\d{6})$")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _next_id(existing: list[dict]) -> int:
    used = [int(m.group(1)) for e in existing if (m := _ID_RE.match(e.get("betanyc_id", "")))]
    return (max(used) + 1) if used else 1


def _load(path: pathlib.Path, key: str) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get(key, [])


def main() -> int:
    entities = _load(SEED, "entities")
    if not entities:
        print(f"error: no seed entities at {SEED}", file=sys.stderr)
        return 1

    by_moda = {e["crosswalk"]["moda_govid"]: e for e in entities
               if e.get("crosswalk", {}).get("moda_govid")}
    by_name = {_norm(e["name"]): e for e in entities}
    proposals: list[dict] = []
    counter = _next_id(entities)

    for src_key in ("records_moda.json", "records_abo.json", "records_nygov.json"):
        for rec in _load(CACHE / src_key, "records"):
            moda = rec.get("crosswalk", {}).get("moda_govid")
            match = by_moda.get(moda) if moda else by_name.get(_norm(rec.get("name", "")))
            if match is None:
                counter += 1
                rec = {**rec, "betanyc_id": f"BNYC-ORG-{counter:06d}"}
                entities.append(rec)
                by_name[_norm(rec.get("name", ""))] = rec
                proposals.append({"action": "add", "betanyc_id": rec["betanyc_id"], "name": rec.get("name")})
            else:
                # Enrich existing record's empty crosswalk ids from the source (never overwrite).
                for k, v in rec.get("crosswalk", {}).items():
                    if v and not match["crosswalk"].get(k):
                        match["crosswalk"][k] = v
                        proposals.append({"action": "enrich", "betanyc_id": match["betanyc_id"],
                                          "field": f"crosswalk.{k}"})

    OUT.write_text(json.dumps({"_generated_from": "build_registry.py", "entities": entities},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    (ROOT / "data" / "build_proposals.json").write_text(
        json.dumps({"proposals": proposals}, indent=2), encoding="utf-8")
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
