# Contributing

Thanks for helping build an open map of NYC/NYS government on the web.

## The one hard rule: never invent data
Populate a field **only** with a value you can verify against a cited source. If you can't
verify it, leave it out — an absent value is correct; a guess is a bug.
- `identifiers[]` (`{scheme, identifier}`): only from the scheme's named authority. See the scheme catalog ([`docs/schemes.md`](docs/schemes.md)) for each scheme's authority, format, and verification rule.
- `web_properties[].domain`: a domain you can confirm resolves to that entity.
- `areas[]`: only a real `{layer, id}` from [nyc-boundaries](https://github.com/BetaNYC/nyc-boundaries) (or a real `us_census_geoid`). Never fabricate a boundary id.

## Adding or editing an entity
1. Edit `data/registry.seed.json` (hand-curated records) — keep it schema-valid.
2. Set `provenance.sources` (`manual` for hand-curated) and `last_verified` (today, ISO).
3. Give new records a fresh, opaque, lower-case slug `id` (`^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`); ids are immutable, never reused. Do **not** reuse the retired `BNYC-ORG-` form — that legacy key lives only in `identifiers[]` under `betanyc_org_legacy` for records migrated from v1.
4. Removed/renamed entities: change `status` + set `dissolution_date`. **Do not delete records.**
5. Validate: `python -m pytest` (schema + migration + build-seam checks, offline).

## Reporting issues
Open an issue for a missing entity, a wrong/dead domain, or a data-quality problem. Include the source
you'd verify against.

## Scope
NYC + NYS government and public-authority **web properties** across all TLDs. In scope: agencies,
authorities, PBCs, IDAs, LDCs, bi-state authorities operating in NY. Out of scope: federal entities,
private orgs, and geometry (that lives in nyc-boundaries — we reference it).
