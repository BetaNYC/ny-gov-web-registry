# Data sources & provenance

Every record's `provenance.sources` names which upstream(s) asserted it. Values in
`identifiers[]` are populated **only** when verified against the scheme's named authority — never
inferred. Each scheme's authority, format, and verification rule is catalogued in [`schemes.md`](schemes.md).

## MODA — city entities (primary city source)
- **Repo:** https://github.com/MODA-NYC/nyc-governance-organizations · **Open Data:** `t3jq-9nkf`
- **License:** MIT.
- **Coverage:** NYC governance organizations (broader than mayoral agencies — boards, commissions, advisory/regulatory bodies). The 2026-07-15 export (`t3jq-9nkf` rows.csv) has **306 rows, all `operational_status = Active`**. Immutable record id. Has a `url` field (**current site only**, no legacy-domain history).
- **Caveat:** the multi-domain / legacy history is ours to build; MODA only carries the current URL.
- **`record_id` format:** the export carries the **`NYC_GOID_XXXXXX`** form (e.g. `NYC_GOID_000476`), not a bare 6-digit numeric. Stored **verbatim as a string** under scheme `nyc_goid` (see [`schemes.md`](schemes.md)); do not strip the prefix.
- **Sync:** `scripts/sync_moda.py` — field map verified **2026-07-15** against the actual 17-column export. Emits v2 records:
  - `record_id` → `identifiers[] {scheme: "nyc_goid"}` (verbatim)
  - `organization_type` → `classification`
  - `acronym` → `short_name` **and** `other_names[]` (noted `acronym`)
  - `alternate_or_former_names` / `alternate_or_former_acronyms` (`;`-delimited) → `other_names[]` (noted `alternate or former name` / `alternate or former acronym`)
  - `url` → `web_properties[] {role: "primary"}` (host only)
  - `operational_status` → **Active-only gate**; emitted `status` is `active`.
  - **Deferred (no phase-0 schema home; do not invent fields):** `principal_officer_*` (personnel identity — no person/officer structure; `contact_details[]` is for contact points, not identity → **phase 2**), `listed_in_nyc_gov_agency_directory` (the flag phase 2 validates against the nyc.gov scrape, user story 13), `reports_to` (hierarchy-by-name; needs a second pass to resolve to minted ids), `name_alphabetized` / `in_org_chart` (display-only).
  - **No mandate seed:** this export has no establishing-authority / Charter / EO column, so `mandates[]` cannot be seeded from it (contrary to earlier assumption). Left to City Record archive curation.
  - **Matching:** the build seam matches identifier-scheme-first then **exact normalized name**. Operator-confirmed near-duplicate merges (e.g. seed `nycedc` = export "Economic Development Corporation", `NYC_GOID_000177`) are recorded in `data/curation.json` and injected onto the seed at build time, so the identifier match enriches instead of minting a duplicate (phase 2). Messy *external-source* names (Greenbook) are reconciled via **nycresolver** — see below.
  - Re-verify the field map if MODA revises the schema.

## Greenbook — city contact scaffolding (structure donor, STALE)
- **Source:** NYC Greenbook (Official Directory of the City of New York) · NYC Open Data `mdcw-n682`.
- **Shape:** one row **per officer** (2,555 rows in the 2026-07-15 export), each carrying the agency's name/acronym/website plus an office address + phones. `sync_greenbook.py` aggregates to **123 distinct agencies**.
- **Staleness:** last refreshed **2023-12**. Used as a **structure donor only** — agency-level website/address/phone scaffolding — **never** as current-officer data. Officer identities (names, titles) are **deliberately dropped**; every attached value is stamped `Greenbook 2023-12 (stale…)` in its `contact_details` note.
- **Matching:** aggregated agency names are reconciled against the registry with **nycresolver** (MODA's own matcher, github.com/MODA-NYC/nyc-entity-resolver, Apache-2.0), built **offline** from registry entities (no Socrata fetch). Tier-gated per PR #5:
  - **exact + same `government_level`** → contact scaffolding attached to the matched entity (`provenance.sources` gains `greenbook`);
  - **fuzzy / cross-`government_level` / no-match** → written to `data/greenbook_reconciliation.json` for human review, **never applied**. Cross-level is the guard that keeps a NYS authority (ESD) out of the city-EDC candidate set — it also holds legitimate marquee authorities/PBCs (CUNY, NYCHA, H+H, NYCEDC) for human confirmation rather than auto-attaching.
  - **Unmatched Greenbook agencies are never minted as entities** (a Greenbook division is not an entity).
- **Website leads:** an agency website whose registrable host (www/www1 normalized) **differs** from every domain the entity already owns is added as an **unverified `legacy` web-property lead** — never overwriting the current primary.
- **Sync:** `scripts/sync_greenbook.py` → `data/greenbook_enrichment.json` (applied by `build_registry.py`) + `data/greenbook_reconciliation.json` (review report).

## nyc.gov agency directory — same-upstream VALIDATION (not a new source)
- **Endpoint:** the nyc.gov agency-directory JSON (306 records; export fetched **2026-07-15**).
- **Discovery (2026-07-15):** this is the **same upstream** as the canonical dataset — identical `record_id` values (`NYC_GOID_XXXXXX`), i.e. the directory page is powered by the governance registry (`t3jq-9nkf`). It is therefore a **validation input, not new entities**.
- **Check:** `scripts/validate_nycgov_directory.py` asserts directory `record_id`s ⊆ registry `nyc_goid`s and reports drift both ways; it also surfaces the `listed_in_nyc_gov_agency` flag `sync_moda` deferred (user story 13). 2026-07-15 result: **306 ⊆ 306, zero drift**; 177 records flagged `listed_in_nyc_gov_agency = true`.

## ABO — public authorities (primary authority source)
- **Dataset:** https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x
- **License:** listed **"unspecified"** on data.ny.gov — resolve before redistributing raw ABO data in a public build.
- **Coverage:** 608 active authorities, four classes (State / Local / IDA / LDC). 9 columns (verified 2026-07-11): `public_authority_type`, `public_authority_name`, `address_line_1`, `address_line_2`, `city`, `state`, `zip`, `website`, `georeference`.
- **`website` column:** added **2026-01-15**; type URL; **self-reported via PARIS, blank where not provided** → partial coverage.
- **No id column:** the dataset exposes no stable authority id, so the `nys_abo` identifier scheme cannot be filled from it (see `schemes.md`). Backup per-class listings: `abo.ny.gov/paw/paw_weblisting{ST,LOCAL,IDA,LDC}.html`.
- **Sync:** `scripts/sync_abo.py` (concrete mapping against the verified columns).

## ny.gov — state executive agencies
- **Directory:** https://www.ny.gov/agencies — HTML, paginated, **no bulk export, no stated license.**
- Covers executive agencies that are **not** authorities (so absent from ABO). Requires scrape + curation.
- **Sync:** `scripts/sync_nygov.py` — **stub, not implemented** (gated + brittle). Curate manually for now.

## Wikidata / IRS — identifier enrichment
- `wikidata` — QIDs from the WDQS SPARQL endpoint (CC0). **Sync:** `scripts/sync_wikidata.py` (phase 3, 2026-07-15), **domain-anchored**: auto-attaches a QID only when its `P856` official-website host is owned by exactly one entity (a *distinctive* domain); apex-shared hosts like `nyc.gov` never match, and exact name/alias matches become review proposals, never auto-attachments. Reads the operator-placed cache `data/cache/wikidata_nyc_gov_orgs.json` (does not fetch); query + fetch date in `freshness.md § Wikidata`.
- `us_irs_ein` (for `.org` PBCs like H+H). Verified values only. See `schemes.md`.

## nyc-boundaries — geography by reference
- https://github.com/BetaNYC/nyc-boundaries — the registry's `areas[]` point into this by `{scheme: "nyc-boundaries", layer, id}`; geometry is never copied here. Jurisdictions that exceed it (MTA region, bi-state PANYNJ, statewide) carry an `area_note` naming a candidate external source (Census TIGER / NYS GIS Clearinghouse) instead. `us_census_geoid` is the documented national geographic scheme.

## Access gate
Live pulls from any of these wait for explicit operator authorization. The sync scripts read
from a local `data/cache/` and never fetch on their own.
