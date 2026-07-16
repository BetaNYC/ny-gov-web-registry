"""sync_boundaries.py — extract the nyc-boundaries layer VOCABULARY into a committed artifact.

Source: BetaNYC/nyc-boundaries — `frontend/src/assets/boundaries/index.ts`, the Boundaries Map's
        authoritative layer catalog (the `BoundaryId` union + the `layers` object). The operator
        placed the file at `data/cache/nyc-boundaries_layers_index.ts`, fetched 2026-07-15. The
        cache dir is git-ignored; this script reads it and emits the committed
        `data/nyc_boundaries_layers.json` so the BUILD validates `areas[]{scheme:"nyc-boundaries"}`
        layer ids against a vocabulary that travels in git (parallel to the greenbook/wikidata
        seams: a sync step reads uncommitted cache, the build reads the committed derivative).

WHY VOCABULARY-BY-EXTRACTION (issue #1 phase-4 scope):
  The registry stores geography BY REFERENCE — it never copies geometry. nyc-boundaries owns the
  boundaries; the registry only asserts "entity X operates layer Y". For that assertion to be
  trustworthy, Y must be a real published layer id. The Boundaries Map's `BoundaryId` union IS the
  published-id vocabulary (bid, cc, cd, dsny, fb, hc, nta, nycongress, pp, sa, sd, ss, zipcode, hd,
  ibz, uhf, puma, cdta, ps, nda, ed, mc). Extracting it deterministically — rather than transcribing
  by hand — keeps the vocabulary honest against the upstream and refreshable on a re-fetch.

PARSE SHAPE (verified against the 2026-07-15 cache, not guessed):
  - `export type BoundaryId = | 'bid' | 'cc' | ... ;`  -> the ordered id union (authority for ids).
  - `export const layers: ILayers = { <id>: { name: '...', name_plural: '...',
       description: '...'?, description_url: '...'?, apiUrl, icon, formatUrl?, formatContent }, ... }`
    String literals are single-quoted; internal apostrophes are typographic U+2019 (never ASCII '),
    so `'([^']*)'` captures a whole literal safely. `hd` has no `description`.

  A consistency guard asserts the union ids == the `layers` object keys (raises on divergence) so a
  future upstream edit that adds a layer to one but not the other fails loud here, not silently in
  the build.

OUTPUT (committed): data/nyc_boundaries_layers.json
  { "_generated_from": "sync_boundaries.py", "source": ..., "boundaries_as_of": "2026-07-15",
    "layers": [ {id, name, name_plural, description|null, description_url|null}, ... ] }

Runs OFFLINE against the local cache; never fetches. Pure parse functions (`parse_boundary_ids`,
`parse_layers`) are unit-tested with in-memory fixtures mirroring the cache shape.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "nyc-boundaries_layers_index.ts"
OUT = ROOT / "data" / "nyc_boundaries_layers.json"

SOURCE = "BetaNYC/nyc-boundaries — frontend/src/assets/boundaries/index.ts"
BOUNDARIES_AS_OF = "2026-07-15"

# Layer-block start at 2-space indent inside the `layers` object, e.g. `  cd: {`.
_BLOCK_RE = re.compile(r"(?m)^  (\w+):\s*\{")
_UNION_ID_RE = re.compile(r"\|\s*'([a-z0-9_]+)'")


def parse_boundary_ids(ts_text: str) -> list[str]:
    """The `BoundaryId` union ids, in source order — the authoritative published-id vocabulary."""
    start = ts_text.find("export type BoundaryId")
    if start == -1:
        raise ValueError("BoundaryId union not found in source")
    end = ts_text.find(";", start)
    if end == -1:
        raise ValueError("BoundaryId union has no terminating ';'")
    return _UNION_ID_RE.findall(ts_text[start:end])


def _field(block: str, key: str) -> str | None:
    """First `key: '...'` or `key: "..."` string literal in a layer block, or None. `key` is
    matched with a trailing colon so `name` does not match `name_plural` and `description` not
    `description_url`. Both quote styles occur in the source (most layers single-quote; `uhf`
    double-quotes) — the delimiter is captured and the value runs to the matching delimiter on the
    same line, so a single-quoted value may contain `"` and vice versa (e.g. pp's `"precinct"`)."""
    m = re.search(rf"(?<![\w]){re.escape(key)}:\s*(['\"])((?:(?!\1)[^\n])*)\1", block)
    return m.group(2) if m else None


def parse_layers(ts_text: str) -> list[dict]:
    """Parse the `layers` object into ordered vocabulary rows. Raises if the object's keys diverge
    from the `BoundaryId` union (the loud consistency guard)."""
    obj_start = ts_text.find("export const layers")
    if obj_start == -1:
        raise ValueError("`export const layers` not found in source")
    body = ts_text[obj_start:]

    starts = [(m.group(1), m.start()) for m in _BLOCK_RE.finditer(body)]
    rows: list[dict] = []
    for i, (layer_id, pos) in enumerate(starts):
        block_end = starts[i + 1][1] if i + 1 < len(starts) else len(body)
        block = body[pos:block_end]
        rows.append({
            "id": layer_id,
            "name": _field(block, "name"),
            "name_plural": _field(block, "name_plural"),
            "description": _field(block, "description"),
            "description_url": _field(block, "description_url"),
        })

    union = parse_boundary_ids(ts_text)
    if set(union) != {r["id"] for r in rows}:
        raise ValueError(
            "BoundaryId union does not match the `layers` object keys: "
            f"union-only={sorted(set(union) - {r['id'] for r in rows})}, "
            f"object-only={sorted({r['id'] for r in rows} - set(union))}"
        )
    return rows


def main() -> int:
    if not CACHE.exists():
        raise SystemExit(
            f"error: cache not found at {CACHE}\n"
            "Place the nyc-boundaries index.ts export there (operator-fetched); this script does "
            "not fetch. See docs/freshness.md § nyc-boundaries."
        )
    ts_text = CACHE.read_text(encoding="utf-8")
    layers = parse_layers(ts_text)
    OUT.write_text(
        json.dumps(
            {
                "_generated_from": "sync_boundaries.py",
                "source": SOURCE,
                "boundaries_as_of": BOUNDARIES_AS_OF,
                "layers": layers,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(layers)} layer ids -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
