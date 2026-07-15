# Data sources & provenance

Every record's `provenance.sources` names which upstream(s) asserted it. Values in
`identifiers[]` are populated **only** when verified against the scheme's named authority — never
inferred. Each scheme's authority, format, and verification rule is catalogued in [`schemes.md`](schemes.md).

## MODA — city entities (primary city source)
- **Repo:** https://github.com/MODA-NYC/nyc-governance-organizations · **Open Data:** `t3jq-9nkf`
- **License:** MIT.
- **Coverage:** ~434 NYC governance organizations (broader than mayoral agencies — boards, commissions, advisory/regulatory bodies). Immutable record id. Has a `url` field (**current site only**, no legacy-domain history). Notably records what *established* each org (Charter provision / Mayoral EO / statute) — a hook for the EO project.
- **Caveat:** the multi-domain / legacy history is ours to build; MODA only carries the current URL.
- **Sync:** `scripts/sync_moda.py` — field map verified 2026-07-11 against MODA's Phase II published schema. Emits v2 records: `record_id` → `identifiers[] {scheme: "nyc_goid"}`, `organization_type` → `classification`. Re-verify if MODA revises the schema.

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
- `wikidata` (the QID often carries P856 official-website) and `us_irs_ein` (for `.org` PBCs like H+H). Verified values only. See `schemes.md`.

## nyc-boundaries — geography by reference
- https://github.com/BetaNYC/nyc-boundaries — the registry's `areas[]` point into this by `{scheme: "nyc-boundaries", layer, id}`; geometry is never copied here. Jurisdictions that exceed it (MTA region, bi-state PANYNJ, statewide) carry an `area_note` naming a candidate external source (Census TIGER / NYS GIS Clearinghouse) instead. `us_census_geoid` is the documented national geographic scheme.

## Access gate
Live pulls from any of these wait for explicit operator authorization. The sync scripts read
from a local `data/cache/` and never fetch on their own.
