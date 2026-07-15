# Schema reference

Authoritative contract: [`schema/property.schema.json`](../schema/property.schema.json) (JSON Schema, draft 2020-12). One record = one **entity**.

The schema is **steward-neutral and standards-aligned** — vocabulary is drawn from the [Popolo](https://www.popoloproject.com/) specification, the [W3C Organization ontology](https://www.w3.org/TR/vocab-org/), and [schema.org/GovernmentOrganization](https://schema.org/GovernmentOrganization), the same lineage as the 2015 CROW notice schema. Any municipality can adopt it; the NYC/NYS-specific choices live in configuration and docs (see the scheme catalog and "Configuration layer" below), not in field names.

## Fields

| Field | Type | Notes |
|---|---|---|
| `id` | string (slug) | Opaque, stable, steward-neutral key (`^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`). Immutable, never reused. The retired `BNYC-ORG-NNNNNN` form is preserved as an `identifiers[]` entry under scheme `betanyc_org_legacy`. Id **syntax is configurable** (see below). |
| `name` / `short_name` | string / string\|null | Official name (Popolo `name`) / common acronym. |
| `other_names[]` | array | Alternate/former/variant names (`{name, note?}`; Popolo `other_names`, EAC-CPF `<nameEntries>`). Never inferred by fuzzy matching. |
| `government_level` | enum | `nyc` \| `nys` \| `bi-state` \| `authority` \| `pbc`. Coarse; **configuration-layer enum** (a fork adjusts it). `classification` carries finer detail. |
| `classification` | string\|null | Finer legal/organizational class (Popolo `classification`; formerly `entity_type`) — `mayoral-agency`, `public-benefit-corporation`, `local-development-corporation`, … |
| `parent_id` / `child_ids` | ids | Org hierarchy (MTA → LIRR / Metro-North / NYCT / TBTA). |
| `identifiers[]` | array | **The interoperability core** (Popolo `identifiers`). Each external key is a `{scheme, identifier}` pair. New upstreams add **scheme values**, never fields. Scheme catalog: [`docs/schemes.md`](schemes.md). **Populated only when verified — never invented.** |
| `web_properties[]` | array | `{domain, role: primary\|legacy\|microsite, valid_from, valid_to, notes}`. The harvester's crawl set **and** the Internet Archive join surface (domain + validity window = Wayback/CDX query keys). `domain` is a host, not a URL/path. |
| `url_conventions[]` | array | Per-era path patterns under a domain (`{era, pattern, notes}`). |
| `areas[]` | array | Geography **by reference** (`{scheme, layer, id, role}`; no geometry). `role`: `operates_layer` (an entity runs a whole district system — NYPD→`pp`, DSNY→`dsny`) or `jurisdiction` (an entity's own footprint — a community board→its `cd`). Schemes: `nyc-boundaries`, `us_census_geoid` (see `docs/schemes.md`). |
| `area_note` | string\|null | Free-text extent note, esp. where an entity **exceeds** available boundary layers (MTA region, bi-state PANYNJ, statewide) and no clean `areas[]` reference exists. Names a candidate external geometry source; stores no geometry. |
| `mandates[]` | array | Legal authority creating/empowering the entity (EAC-CPF `<mandate>`): `{authority_type, citation, url, date, role}`. `authority_type` ∈ {charter, local_law, executive_order, state_statute, federal_statute, charter_revision, other}; `role` ∈ {establishing, amending, abolishing}. Generalizes the former `established_by_eo[]`. Citation is text + URL; no statute text embedded. |
| `relations[]` | array | Succession links (EAC-CPF `<cpfRelations>`): `{type: predecessor_of\|successor_of, target_id, date}`. Supersedes the former `succeeds_ids` / `succeeded_by_ids`. |
| `status` | enum | `active` \| `dissolved` \| `merged` \| `renamed` \| `planned`. Removals are status changes, not deletes. |
| `founding_date` / `dissolution_date` | string\|null | Lifecycle dates (Popolo; EAC-CPF `<existDates>`). Formerly `valid_from` / `valid_to`. |
| `links[]` | array | Related non-owned URLs (`{url, note?}`; Popolo `links`). Owned domains go in `web_properties[]`. |
| `contact_details[]` | array | Contact points (`{type, value, note?}`; Popolo `contact_details`). Greenbook scaffolding lands here in phase 2, staleness flagged in `note`. |
| `provenance` | object | `sources[]` (`moda`\|`abo`\|`nygov`\|`wikidata`\|`manual`; configuration-layer enum) + `last_verified`. |

`id`, `name`, `government_level`, `web_properties`, and `status` are **required**; every other field is optional and additive.

## Configuration layer (what a fork changes, without touching the schema)

A municipality adopting this schema changes only **configuration and docs**, never the JSON Schema:

- **id syntax** — the reference convention is a lower-case slug; a fork may choose its own opaque scheme.
- **scheme catalog** — which `identifiers[]` schemes exist (`docs/schemes.md`); adding one is a doc edit.
- **geographic schemes** — which `areas[]` schemes/layers exist (here: `nyc-boundaries`, `us_census_geoid`).
- **canonical source** — NYC uses NYC Open Data `t3jq-9nkf` (`nyc_goid`); a fork points at its own.
- **`government_level` / `provenance.sources` enums** — coarse local vocabulary.

## Archival interoperability

Registry records map losslessly to EAC-CPF concepts (a documented crosswalk, not a native format): [`docs/eac-cpf-crosswalk.md`](eac-cpf-crosswalk.md). Authority-file schemes (`snac_ark`, `viaf`, `lcnaf`) join the catalog where verified. ICA Records in Contexts (RiC-O) is the forward-looking linked-data target; a JSON-LD `@context` should not preclude it.

## Migration note (v1 → v2)

v2 is a pre-adoption breaking change. `scripts/migrate_seed.py` performs the lossless v1→v2 migration; `tests/test_migration.py` asserts every v1 value survives (old `betanyc_id` → `identifiers[]` legacy scheme, `crosswalk` → `identifiers[]`, `boundary_ref` → `areas[]`, `established_by_eo[]` → `mandates[]`, succession lists → `relations[]`).
