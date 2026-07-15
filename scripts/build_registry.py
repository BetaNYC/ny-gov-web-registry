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

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "registry.seed.json"
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "registry.json"
PROPOSALS = ROOT / "data" / "build_proposals.json"
SCHEMA = ROOT / "schema" / "property.schema.json"
CURATION = ROOT / "data" / "curation.json"
GREENBOOK_ENRICHMENT = ROOT / "data" / "greenbook_enrichment.json"
WIKIDATA_ENRICHMENT = ROOT / "data" / "wikidata_enrichment.json"
BOUNDARIES_MAPPING = ROOT / "data" / "boundaries_mapping.json"
BOUNDARIES_VOCAB = ROOT / "data" / "nyc_boundaries_layers.json"

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


def _add_other_name(entity: dict, name: str, note: str | None) -> bool:
    """Add an other_names[] entry if that name isn't already present (case-insensitive). Returns
    True if added. Used by both curation and (indirectly) nothing else — kept small and pure."""
    existing = {_norm(o.get("name", "")) for o in entity.get("other_names", [])}
    if _norm(name) in existing:
        return False
    entity.setdefault("other_names", []).append({"name": name, "note": note})
    return True


def apply_seed_curation(seed_entities: list[dict], curation: dict) -> list[dict]:
    """Apply operator-confirmed merges onto COPIES of the seed BEFORE the source merge.

    A confirmed_merge injects a verified {scheme, identifier} onto the named seed entity so the
    build's identifier-first matching auto-matches the upstream record and ENRICHES it instead of
    minting a duplicate (this is how the operator-confirmed EDC merge collapses to one entity).
    Keeping this in curation (not the seed file) preserves the 'seed carries only the legacy id'
    invariant. Pure: returns new entity dicts, never mutates the input.
    """
    entities = [dict(e) for e in seed_entities]
    by_id = {e["id"]: e for e in entities}
    for merge in curation.get("confirmed_merges", []):
        target = by_id.get(merge["seed_id"])
        if target is None:
            continue
        # Deep-copy the mutable members we touch so the input list is untouched.
        target["identifiers"] = [dict(i) for i in target.get("identifiers", [])]
        target["other_names"] = [dict(o) for o in target.get("other_names", [])]
        target["provenance"] = dict(target.get("provenance", {"sources": []}))
        target["provenance"]["sources"] = list(target["provenance"].get("sources", []))

        pair = (merge["match"]["scheme"], merge["match"]["identifier"])
        if pair not in {(i["scheme"], i["identifier"]) for i in target["identifiers"]}:
            target["identifiers"].append({"scheme": pair[0], "identifier": pair[1]})
        for on in merge.get("add_other_names", []):
            _add_other_name(target, on["name"], on.get("note"))
        src = merge.get("add_provenance_source")
        if src and src not in target["provenance"]["sources"]:
            target["provenance"]["sources"].append(src)
    return entities


def apply_entity_curation(entities: list[dict], curation: dict) -> list[dict]:
    """Apply other_names_additions to built entities by id (AFTER the merge). Documents
    name-variant false-positive guards (e.g. ESD) in-data. Mutates the built entities in place."""
    by_id = {e["id"]: e for e in entities}
    for add in curation.get("other_names_additions", []):
        target = by_id.get(add["entity_id"])
        if target is None:
            continue
        for on in add.get("names", []):
            _add_other_name(target, on["name"], on.get("note"))
    return entities


def apply_greenbook_enrichment(entities: list[dict], enrichment: dict) -> tuple[list[dict], list[dict]]:
    """Attach Greenbook contact scaffolding to matched entities (idempotent, deduped, additive).

    Returns (entities, skipped). Each enrichment is keyed by entity_id (produced by
    sync_greenbook.py via the tier-gated reconciliation). Adds contact_details (dedup by
    type+value), web_candidates (dedup by www-normalized domain), and the 'greenbook' provenance
    source. Never overwrites an existing primary domain. Re-running is safe.
    """
    by_id = {e["id"]: e for e in entities}
    skipped: list[dict] = []
    for enr in enrichment.get("enrichments", []):
        target = by_id.get(enr["entity_id"])
        if target is None:
            skipped.append({"entity_id": enr["entity_id"], "reason": "entity_id not in registry"})
            continue
        cds = target.setdefault("contact_details", [])
        have_cd = {(c.get("type"), c.get("value")) for c in cds}
        for cd in enr.get("contact_details", []):
            if (cd.get("type"), cd.get("value")) not in have_cd:
                cds.append(cd)
                have_cd.add((cd.get("type"), cd.get("value")))
        wps = target.setdefault("web_properties", [])
        have_dom = {re.sub(r"^www\d*\.", "", w.get("domain", "").lower()) for w in wps}
        for wc in enr.get("web_candidates", []):
            norm_dom = re.sub(r"^www\d*\.", "", wc.get("domain", "").lower())
            if norm_dom and norm_dom not in have_dom:
                wps.append(wc)
                have_dom.add(norm_dom)
        src = enr.get("add_provenance_source")
        if src:
            prov = target.setdefault("provenance", {"sources": []})
            prov.setdefault("sources", [])
            if src not in prov["sources"]:
                prov["sources"].append(src)
    return entities, skipped


def apply_wikidata_enrichment(entities: list[dict], enrichment: dict) -> tuple[list[dict], list[dict]]:
    """Attach domain-anchored Wikidata QIDs to matched entities (idempotent, deduped, additive).

    Returns (entities, skipped). Each enrichment is keyed by entity_id (produced by
    sync_wikidata.py's distinctive-domain auto tier — NEVER a name-only match). Adds
    identifiers[]{scheme:"wikidata"} (dedup by the {scheme, identifier} pair) and the 'wikidata'
    provenance source. Never overwrites an existing identifier. Re-running is safe.
    """
    by_id = {e["id"]: e for e in entities}
    skipped: list[dict] = []
    for enr in enrichment.get("enrichments", []):
        target = by_id.get(enr["entity_id"])
        if target is None:
            skipped.append({"entity_id": enr["entity_id"], "reason": "entity_id not in registry"})
            continue
        ids = target.setdefault("identifiers", [])
        have = {(i.get("scheme"), i.get("identifier")) for i in ids}
        for ident in enr.get("identifiers", []):
            pair = (ident.get("scheme"), ident.get("identifier"))
            if pair not in have:
                ids.append({"scheme": pair[0], "identifier": pair[1]})
                have.add(pair)
        src = enr.get("add_provenance_source")
        if src:
            prov = target.setdefault("provenance", {"sources": []})
            prov.setdefault("sources", [])
            if src not in prov["sources"]:
                prov["sources"].append(src)
    return entities, skipped


def _add_area(entity: dict, area: dict) -> bool:
    """Add an areas[] reference if an equivalent one isn't already present. Equivalence is the
    (scheme, layer, id, role) tuple, so a rebuild never duplicates a reference. Returns True if
    added. Pure helper over one entity."""
    key = (area["scheme"], area["layer"], area.get("id"), area["role"])
    have = {(a.get("scheme"), a.get("layer"), a.get("id"), a.get("role"))
            for a in entity.get("areas", [])}
    if key in have:
        return False
    entity.setdefault("areas", []).append(area)
    return True


def apply_boundaries_mapping(entities: list[dict], mapping: dict,
                            layer_vocab: list[dict]) -> tuple[list[dict], list[dict]]:
    """Attach geography-by-reference areas[] from the operator-curated boundaries mapping.

    Returns (entities, skipped). Two attaching sections (both idempotent, deduped, additive):
      - `operates_layer`: whole-layer references {scheme:"nyc-boundaries", layer, id:null,
        role:"operates_layer"} for entities that administer a district system (NYPD->pp, DSNY->dsny,
        FDNY->fb, DOE->sd, City Council->cc, Community Boards->cd, Board of Elections->ed).
      - `jurisdictions`: county-footprint references {scheme:"us_census_geoid", layer, id:<FIPS>,
        role:"jurisdiction"} for the 5 borough-president offices (nyc-boundaries has no borough
        layer, so the national scheme carries the footprint).
    Every attach adds the 'manual' provenance source. Non-attaching sections
    (defines_not_operates / no_registry_entity / deferred) are documentation only.

    Fails LOUD (raises ValueError) on any {scheme:"nyc-boundaries"} layer id not in `layer_vocab`
    (the committed vocabulary from sync_boundaries.py) — a typo'd or removed layer id is a build
    error, never a silent bad reference. us_census_geoid layers are Census summary levels, not
    nyc-boundaries ids, so they are not checked against this vocabulary.
    """
    valid_layers = {row["id"] for row in layer_vocab}
    by_id = {e["id"]: e for e in entities}
    skipped: list[dict] = []

    def _attach(section_key: str, role: str) -> None:
        for ref in mapping.get(section_key, []):
            if ref.get("scheme") == "nyc-boundaries" and ref["layer"] not in valid_layers:
                raise ValueError(
                    f"boundaries mapping references unknown nyc-boundaries layer "
                    f"'{ref['layer']}' (entity {ref.get('entity_id')}); valid layers: "
                    f"{sorted(valid_layers)}. Re-run sync_boundaries.py or fix the mapping."
                )
            target = by_id.get(ref["entity_id"])
            if target is None:
                skipped.append({"entity_id": ref["entity_id"], "layer": ref.get("layer"),
                                "reason": "entity_id not in registry"})
                continue
            area = {"scheme": ref["scheme"], "layer": ref["layer"],
                    "id": ref.get("id"), "role": role}
            _add_area(target, area)
            prov = target.setdefault("provenance", {"sources": []})
            prov.setdefault("sources", [])
            if "manual" not in prov["sources"]:
                prov["sources"].append("manual")

    _attach("operates_layer", "operates_layer")
    _attach("jurisdictions", "jurisdiction")
    return entities, skipped


def _load(path: pathlib.Path, key: str) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get(key, [])


def _load_obj(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_unenriched() -> tuple[list[dict], list[dict]]:
    """Build entities from seed + sources + curation, WITHOUT Greenbook enrichment.

    Returns (entities, proposals). This is the reconciliation match target for sync_greenbook.py:
    building it here (not reading the possibly-already-enriched registry.json) keeps the Greenbook
    web-lead comparison deterministic and independent of registry.json's enrichment state.
    """
    seed = _load(SEED, "entities")
    if not seed:
        raise SystemExit(f"error: no seed entities at {SEED}")
    curation = _load_obj(CURATION)
    seed = apply_seed_curation(seed, curation)  # confirmed merges injected before the source merge
    batches = [_load(CACHE / name, "records") for name in SOURCE_FILES]
    entities, proposals = build_entities(seed, batches)
    entities = apply_entity_curation(entities, curation)  # name-variant guards, by id
    return entities, proposals


def main() -> int:
    entities, proposals = build_unenriched()

    # Greenbook contact scaffolding (optional; produced by sync_greenbook.py). Additive + deduped.
    greenbook = _load_obj(GREENBOOK_ENRICHMENT)
    entities, gb_skipped = apply_greenbook_enrichment(entities, greenbook)

    # Wikidata QIDs (optional; produced by sync_wikidata.py, domain-anchored auto tier). Additive.
    wikidata = _load_obj(WIKIDATA_ENRICHMENT)
    entities, wd_skipped = apply_wikidata_enrichment(entities, wikidata)

    # Boundaries areas[] (operator-curated mapping; layer ids validated against the committed
    # vocabulary). Additive + idempotent; raises on an unknown nyc-boundaries layer id.
    boundaries = _load_obj(BOUNDARIES_MAPPING)
    bd_skipped: list[dict] = []
    if boundaries.get("operates_layer") or boundaries.get("jurisdictions"):
        vocab = _load_obj(BOUNDARIES_VOCAB).get("layers", [])
        if not vocab:
            raise SystemExit(
                f"error: boundaries mapping present but no layer vocabulary at {BOUNDARIES_VOCAB}. "
                "Run scripts/sync_boundaries.py to extract it from the cached index.ts."
            )
        entities, bd_skipped = apply_boundaries_mapping(entities, boundaries, vocab)

    OUT.write_text(json.dumps({"_generated_from": "build_registry.py", "entities": entities},
                              indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    PROPOSALS.write_text(json.dumps({"proposals": proposals}, indent=2) + "\n", encoding="utf-8")
    gb_note = ""
    if greenbook.get("enrichments"):
        gb_note = (f"; greenbook enrichment applied to {len(greenbook['enrichments']) - len(gb_skipped)}"
                   f" entities" + (f" ({len(gb_skipped)} skipped)" if gb_skipped else ""))
    wd_note = ""
    if wikidata.get("enrichments"):
        wd_note = (f"; wikidata QIDs attached to {len(wikidata['enrichments']) - len(wd_skipped)}"
                   f" entities" + (f" ({len(wd_skipped)} skipped)" if wd_skipped else ""))
    bd_note = ""
    n_areas = len(boundaries.get("operates_layer", [])) + len(boundaries.get("jurisdictions", []))
    if n_areas:
        bd_note = (f"; boundaries areas attached to {n_areas - len(bd_skipped)} entity refs"
                   + (f" ({len(bd_skipped)} skipped)" if bd_skipped else ""))
    print(f"wrote {len(entities)} entities -> {OUT}  ({len(proposals)} proposal(s))"
          f"{gb_note}{wd_note}{bd_note}")

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
