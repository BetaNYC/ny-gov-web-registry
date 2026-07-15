# ny-gov-web-registry

A curated, machine-readable registry of **New York City + New York State government and public-authority web properties** — every agency, authority, and public-benefit corporation, and the web domains each has owned over its life.

Built by [BetaNYC](https://beta.nyc). MIT-licensed.

> **Status: scaffold (2026-07-11).** The schema, a hand-authored anchor-set seed, and the source-sync scripts exist. The registry has **not** yet been populated from live sources — the sync scripts are written but not run (see *Data sources & access*). This repo is safe to read and build on; the dataset is provisional.

## Why this exists

Not all government lives on `.gov`. The MTA is on `mta.info`, NYC EDC on `nycedc.com`, Health + Hospitals on `nychealthandhospitals.org`, CUNY on `cuny.edu`, while NYCHA sits on the city's own `nyc.gov`. Any project that wants to reason about "NYC/NYS government on the web" — archiving it, monitoring it, citing it — first needs an authoritative list of *which entities exist and what domains they use*. No such list spans city + state + authorities across every TLD. This is that list.

The immediate consumer is a forthcoming **NYC/NYS Wayback Machine harvester** (which reads this registry's domains as its crawl set). But the registry stands on its own as a reusable civic dataset.

## What's here

```
schema/property.schema.json   JSON Schema for one entity record (the contract; steward-neutral, standards-aligned)
data/registry.seed.json       provisional hand-authored anchor set (17 entities)
data/registry.json            the BUILT dataset — produced by build_registry.py
scripts/                      source-sync + build + migration pipeline (see below)
docs/                         schema reference, scheme catalog, EAC-CPF crosswalk, source provenance, freshness
tests/                        offline validation (schema, lossless migration, build-seam invariants)
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
| ABO **Directory of Public Authorities** | 608 authorities (State/Local/IDA/LDC) + `Website` | data.ny.gov `4vym-q77x` | `Website` column added 2026-01-15; self-reported, **blank where not provided**. License "unspecified". No explicit id column. |
| `ny.gov/agencies` | State executive agencies | HTML directory | No bulk export → hand-curate. |
| Wikidata / IRS | identifiers (`wikidata`, `us_irs_ein`) | — | Verified values only. |

Full provenance, licenses, and cadence: [`docs/sources.md`](docs/sources.md).

> **Access gate.** Per BetaNYC's operating rule, live external API/data pulls wait for explicit authorization. The sync scripts therefore read from a local `data/cache/` and **do not fetch on their own** — see each script's header.

## Building the dataset (once sources are pulled)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 1. place source exports in data/cache/ (manual, gated)
# 2. python scripts/build_registry.py   # merges sources + seed -> data/registry.json
python -m pytest    # offline: validates the seed against the schema
```

## Contributing

Bug reports and additions welcome — file an issue. Adding an entity? Only populate values you can verify against a cited source; leave the rest null. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Provenance

Scaffolded 2026-07-11 as part of BetaNYC's NYC/NYS government web-archiving initiative. Design rationale lives in the private workspace (`team/research/mayoral-executive-orders/2026-07-11-property-registry-design.md`).
