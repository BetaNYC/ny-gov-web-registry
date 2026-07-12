# Schema reference

Authoritative contract: [`schema/property.schema.json`](../schema/property.schema.json) (JSON Schema, draft 2020-12). One record = one **entity**.

## Fields

| Field | Type | Notes |
|---|---|---|
| `betanyc_id` | string `BNYC-ORG-NNNNNN` | Our stable, immutable key. No upstream spans all levels, so we mint our own. |
| `name` / `short_name` | string | Official name / common acronym. |
| `government_level` | enum | `nyc` \| `nys` \| `bi-state` \| `authority` \| `pbc`. Coarse; `entity_type` carries finer detail. |
| `entity_type` | string\|null | e.g. `mayoral-agency`, `public-benefit-corporation`, `local-development-corporation`. |
| `parent_id` / `child_ids` | ids | Org hierarchy (MTA → LIRR / Metro-North / NYCT / TBTA). |
| `crosswalk` | object | `moda_govid`, `abo_id`, `wikidata_qid`, `irs_ein`, `opendata_dataset`. **Null until verified — never invented.** |
| `web_properties[]` | array | `{domain, role: primary\|legacy\|microsite, valid_from, valid_to, notes}`. The harvester's crawl set. `domain` is a host, not a URL/path. |
| `url_conventions[]` | array | Per-era path patterns under a domain (`{era, pattern, notes}`) — where documents historically lived. |
| `jurisdiction` | object | `boundary_ref: {layer, id}` into nyc-boundaries (or null); `coverage` enum; `boundary_note` for jurisdictions that exceed nyc-boundaries. **No geometry stored.** |
| `established_by_eo[]` | array | EO linkage via LOCKED names `eo_id` + `source_pdf_url`. Reciprocal of the EO record's `establishes_entity` = this entity's `betanyc_id`. |
| `status` | enum | `active` \| `dissolved` \| `merged` \| `renamed` \| `planned`. Removals are status changes, not deletes. |
| `valid_from` / `valid_to` | string\|null | Lifecycle dates. |
| `succeeds_ids` / `succeeded_by_ids` | ids | Predecessor/successor chains (OTI succeeds DoITT). |
| `provenance` | object | `sources[]` (`moda`\|`abo`\|`nygov`\|`wikidata`\|`manual`) + `last_verified`. |

## Locked cross-link names
`eo_id`, `establishes_entity`, `source_pdf_url` are locked project-wide (shared with the EO archive
and the Open Government Timeline) so the three datasets join cleanly. Do not rename them here.

## Open question (O1) — `government_level` vs. the design's richer vocab
`authority` and `pbc` overlap by design (many authorities *are* public-benefit corporations). The coarse
enum keeps queries simple; `entity_type` carries the precise legal instrument. Revisit if the coarse split
proves lossy for real queries.
