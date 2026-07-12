# Data sources & provenance

Every record's `provenance.sources` names which upstream(s) asserted it. Values in
`crosswalk` are populated **only** when verified against the named upstream — never inferred.

## MODA — city entities (primary city source)
- **Repo:** https://github.com/MODA-NYC/nyc-governance-organizations · **Open Data:** `t3jq-9nkf`
- **License:** MIT.
- **Coverage:** ~434 NYC governance organizations (broader than mayoral agencies — boards, commissions, advisory/regulatory bodies). Immutable record id. Has a `url` field (**current site only**, no legacy-domain history). Notably records what *established* each org (Charter provision / Mayoral EO / statute) — a hook for the EO project.
- **Caveat:** the multi-domain / legacy history is ours to build; MODA only carries the current URL.
- **Sync:** `scripts/sync_moda.py` — ⚠️ its field map is **unverified** against MODA's data dictionary; confirm column names before the first real run.

## ABO — public authorities (primary authority source)
- **Dataset:** https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x
- **License:** listed **"unspecified"** on data.ny.gov — resolve before redistributing raw ABO data in a public build.
- **Coverage:** 608 active authorities, four classes (State / Local / IDA / LDC). 9 columns (verified 2026-07-11): `public_authority_type`, `public_authority_name`, `address_line_1`, `address_line_2`, `city`, `state`, `zip`, `website`, `georeference`.
- **`website` column:** added **2026-01-15**; type URL; **self-reported via PARIS, blank where not provided** → partial coverage.
- **No id column:** the dataset exposes no stable authority id, so `crosswalk.abo_id` cannot be filled from it. Backup per-class listings: `abo.ny.gov/paw/paw_weblisting{ST,LOCAL,IDA,LDC}.html`.
- **Sync:** `scripts/sync_abo.py` (concrete mapping against the verified columns).

## ny.gov — state executive agencies
- **Directory:** https://www.ny.gov/agencies — HTML, paginated, **no bulk export, no stated license.**
- Covers executive agencies that are **not** authorities (so absent from ABO). Requires scrape + curation.
- **Sync:** `scripts/sync_nygov.py` — **stub, not implemented** (gated + brittle). Curate manually for now.

## Wikidata / IRS — crosswalk enrichment
- `wikidata_qid` (often carries P856 official-website) and `irs_ein` (for `.org` PBCs like H+H). Verified values only.

## nyc-boundaries — geography by reference
- https://github.com/BetaNYC/nyc-boundaries — the registry's `jurisdiction.boundary_ref` points into this by `{layer, id}`; geometry is never copied here. Jurisdictions that exceed it (MTA region, bi-state PANYNJ, statewide) carry a `boundary_note` naming a candidate external source (Census TIGER / NYS GIS Clearinghouse) instead.

## Access gate
Live pulls from any of these wait for explicit operator authorization. The sync scripts read
from a local `data/cache/` and never fetch on their own.
