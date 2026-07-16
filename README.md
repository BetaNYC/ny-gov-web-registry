# ny-gov-web-registry

A curated, machine-readable registry of **New York City + New York State government and public-authority web properties** — every agency, authority, and public-benefit corporation, and the web domains each has owned over its life.

Built by [BetaNYC](https://beta.nyc). MIT-licensed.

> **Status: crosswalk + curation (phase 2, 2026-07-15).** The registry carries **317 entities** — the 17-entity anchor seed merged with **306 Active NYC governance organizations** from MODA / NYC Open Data `t3jq-9nkf`, less the operator-confirmed EDC merge (the separately-minted "Economic Development Corporation" folded into seed `nycedc`). Phase 2 also: reconciles the stale-but-rich **NYC Greenbook** (`mdcw-n682`, 2023-12) against the registry via **MODA's nycresolver** — attaching agency contact scaffolding (staleness-flagged) to 27 exact-matched city entities and routing fuzzy/cross-level/unmatched agencies to a review report, never minting new entities; and validates the **nyc.gov agency directory** (found to be the same `t3jq` upstream — 306 ⊆ 306, zero drift). **Phase 3 (2026-07-15)** attaches **Wikidata QIDs** by *domain-anchored* matching — a QID auto-attaches only when its `P856` official-website host is owned by exactly one entity (apex-shared hosts like `nyc.gov` never auto-match; exact-name matches become review proposals, never auto). A **place-vs-organization guard** (operator re-point directives in `curation.json`) overrides domain matches where `P856` belongs to a *place* rather than the office: the four borough-president offices were re-pointed off their borough *place* QIDs and **dropped** (Wikidata has no per-borough office item). Net: **3 QIDs auto-attached** (NYC Parks, H+H, Javits CCOC), **4 dropped**, **22 name proposals** to a review report. **Phase 4 (2026-07-15)** links geography by reference: an operator-curated mapping (`data/boundaries_mapping.json`) attaches `areas[]` for the entities that operate a district system (NYPD→`pp`+`ps`, DSNY→`dsny`, FDNY→`fb`, DOE→`sd`, City Council→`cc`, Community Boards→`cd`, Board of Elections→`ed`) and `us_census_geoid` county footprints for the 5 borough presidents; every layer id is validated against the nyc-boundaries vocabulary extracted into `data/nyc_boundaries_layers.json` (unknown layer = build error), and no entities are minted (per-board linkage deferred). All external data is still access-gated: syncs read operator-placed cache files and never fetch. **Phase 5 (2026-07-15)** adds the registry's **only networked component** — the about-page crawler. It derives a reviewable, committed crawl set (`data/crawl_targets.json`) by recovering each entity's full start URL (the deep `nyc.gov/site/<slug>/` paths that `web_properties[]` lost survive only in MODA's `url` column), then politely fetches each entity's about page — identified User-Agent, robots.txt honored, per-host delay, WAF circuit-breaker — and extracts the agency's **own words** into `data/descriptions.json` (never summarized or generated). The crawler refuses to fetch without `--operator-authorized`. See *Data sources & access* and [`docs/freshness.md`](docs/freshness.md).

## Why this exists

Not all government lives on `.gov`. The MTA is on `mta.info`, NYC EDC on `nycedc.com`, Health + Hospitals on `nychealthandhospitals.org`, CUNY on `cuny.edu`, while NYCHA sits on the city's own `nyc.gov`. Any project that wants to reason about "NYC/NYS government on the web" — archiving it, monitoring it, citing it — first needs an authoritative list of *which entities exist and what domains they use*. No such list spans city + state + authorities across every TLD. This is that list.

The immediate consumer is a forthcoming **NYC/NYS Wayback Machine harvester** (which reads this registry's domains as its crawl set). But the registry stands on its own as a reusable civic dataset.

## What's here

```
schema/property.schema.json          JSON Schema for one entity record (the contract; steward-neutral, standards-aligned)
data/registry.seed.json              provisional hand-authored anchor set (17 entities; carries only the legacy id)
data/curation.json                   operator-confirmed merges + name-variant guards applied at build time
data/registry.json                   the BUILT dataset — produced by build_registry.py
data/greenbook_enrichment.json       entity-keyed Greenbook contact scaffolding (applied by the build)
data/greenbook_reconciliation.json   Greenbook match review report (attached / fuzzy / cross-level / unmatched)
data/wikidata_enrichment.json        entity-keyed Wikidata QIDs, domain-anchored (applied by the build)
data/wikidata_reconciliation.json    Wikidata match review report (auto / proposals / conflicts / leads / caveats)
data/nyc_boundaries_layers.json      nyc-boundaries layer vocabulary extracted from the map (build validates areas[] against it)
data/boundaries_mapping.json         operator-curated layer->operator + borough-president->county areas[] assertions (applied by the build)
data/crawl_targets.json              the about-crawler's start-URL set, derived offline from the registry + MODA url column (committed, reviewable)
data/descriptions.json               each entity's about-page self-description (crawler output; the boundary artifact downstream consumers read)
scripts/                             source-sync + reconcile + build + migration + about-crawler pipeline (see below)
docs/                                schema reference, scheme catalog, EAC-CPF crosswalk, source provenance, freshness
tests/                               offline validation (schema, migration, build seam, reconciliation, curation, crawler discovery/extraction)
```

## The data model (one record = one entity)

A record describes an **entity**, not a domain — because one entity accretes and retires many domains over time. The schema is **steward-neutral and standards-aligned** (Popolo / W3C ORG / schema.org GovernmentOrganization) so any municipality can adopt it. Each record carries:

- `id` — an opaque, stable, steward-neutral slug (no org prefix). The retired `BNYC-ORG-NNNNNN` key is preserved as an `identifiers[]` entry under the legacy scheme `betanyc_org_legacy`.
- `government_level` / `classification` — coarse (`nyc | nys | bi-state | authority | pbc`) and fine organizational class.
- `web_properties[]` — the domains the entity has owned, each tagged `primary | legacy | microsite` with valid dates. **The harvester's crawl set and the Internet Archive join surface.**
- `identifiers[]` — external keys as `{scheme, identifier}` pairs (`nyc_goid`, `wikidata`, `us_irs_ein`, …). New upstreams add **scheme values, never fields**. **Populated only when verified — never invented.** Catalog: [`docs/schemes.md`](docs/schemes.md).
- `areas[]` — geography **by reference** (`{scheme, layer, id, role}`) into [`BetaNYC/nyc-boundaries`](https://github.com/BetaNYC/nyc-boundaries) or `us_census_geoid`, never embedded geometry. `area_note` covers jurisdictions that exceed the available layers.
- `mandates[]` — the legal authority (charter / local law / executive order / statute) creating or empowering the entity. `relations[]` — predecessor/successor lineage. `status` / lifecycle dates.

Full field reference: [`docs/schema.md`](docs/schema.md). Scheme catalog: [`docs/schemes.md`](docs/schemes.md). EAC-CPF crosswalk: [`docs/eac-cpf-crosswalk.md`](docs/eac-cpf-crosswalk.md).

## Data sources & access

| Source | Provides | Access | Notes |
|---|---|---|---|
| [MODA `nyc-governance-organizations`](https://github.com/MODA-NYC/nyc-governance-organizations) | City entities + current `url` | GitHub / NYC Open Data `t3jq-9nkf` | MIT. `url` is current-site only. |
| **NYC Greenbook** (`mdcw-n682`) | Agency website/address/phone **scaffolding** | NYC Open Data | **Stale (2023-12)** — structure donor only; reconciled via [nycresolver](https://github.com/MODA-NYC/nyc-entity-resolver), staleness-flagged; officers never imported. |
| **nyc.gov agency directory** | Directory-listing flag (**validation**) | nyc.gov JSON | **Same upstream as `t3jq-9nkf`** — a consistency check (record_ids ⊆ registry nyc_goids), not new entities. |
| ABO **Directory of Public Authorities** | 608 authorities (State/Local/IDA/LDC) + `Website` | data.ny.gov `4vym-q77x` | `Website` column added 2026-01-15; self-reported, **blank where not provided**. License "unspecified". No explicit id column. |
| `ny.gov/agencies` | State executive agencies | HTML directory | No bulk export → hand-curate. |
| **Wikidata** | `wikidata` QID identifiers | WDQS SPARQL (CC0) | **Domain-anchored:** auto-attach only on a *distinctive* `P856` host match; name matches are review proposals. Query + fetch date in [`docs/freshness.md`](docs/freshness.md). |
| IRS | `us_irs_ein` identifiers | — | Verified values only. |

Full provenance, licenses, and cadence: [`docs/sources.md`](docs/sources.md).

> **Access gate.** Per BetaNYC's operating rule, live external API/data pulls wait for explicit authorization. The sync scripts therefore read from a local `data/cache/` and **do not fetch on their own** — see each script's header.

## Building the dataset (once sources are pulled)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 1. place source exports in data/cache/ (manual, gated) — e.g. t3jq-9nkf rows.csv
#    at data/cache/moda_nyc-governance-organizations.csv
# 2. python scripts/sync_moda.py        # normalize the export -> data/cache/records_moda.json (Active-only)
# 3. python scripts/build_registry.py   # merges sources + seed -> data/registry.json (idempotent)
# 4. python scripts/sync_greenbook.py    # reconcile Greenbook -> greenbook_enrichment.json + report
# 5. python scripts/sync_wikidata.py     # domain-anchor Wikidata -> wikidata_enrichment.json + report
# 6. python scripts/sync_boundaries.py   # extract nyc-boundaries layer vocabulary -> nyc_boundaries_layers.json
# 7. python scripts/build_registry.py    # re-build to fold in greenbook + wikidata + boundaries areas (idempotent)
python -m pytest    # offline: validates seed, migration, sync mappings, and build invariants
```

### About-page descriptions (phase 5 — the one networked step)

```bash
# 8. python scripts/crawl_targets.py             # offline: derive the crawl set -> data/crawl_targets.json
# 9. python scripts/crawl_about.py               # prints the crawl plan and EXITS (access gate; no fetch)
#    python scripts/crawl_about.py --operator-authorized   # perform the polite crawl -> data/descriptions.json
```

`crawl_about.py` is the **only** code in this repo that touches the network, and it refuses to
fetch without `--operator-authorized`. It honors robots.txt, identifies itself, rate-limits per
host, and abandons a host after repeated 403s (nyc.gov sits behind Akamai). It is idempotent and
resumable — an entity already in `descriptions.json` is skipped (`--force` to re-crawl), and the
file is rewritten after each entity. See [`docs/sources.md`](docs/sources.md) § Descriptions.

## Contributing

Bug reports and additions welcome — file an issue. Adding an entity? Only populate values you can verify against a cited source; leave the rest null. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Provenance

Scaffolded 2026-07-11 as part of BetaNYC's NYC/NYS government web-archiving initiative. Design rationale lives in the private workspace (`team/research/mayoral-executive-orders/2026-07-11-property-registry-design.md`).
