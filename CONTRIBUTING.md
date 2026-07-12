# Contributing

Thanks for helping build an open map of NYC/NYS government on the web.

## The one hard rule: never invent data
Populate a field **only** with a value you can verify against a cited source. If you can't
verify it, leave it `null` — a null is correct; a guess is a bug.
- `crosswalk` ids (`moda_govid`, `abo_id`, `wikidata_qid`, `irs_ein`): only from the named upstream.
- `web_properties[].domain`: a domain you can confirm resolves to that entity.
- `jurisdiction.boundary_ref`: only a real `{layer, id}` from [nyc-boundaries](https://github.com/BetaNYC/nyc-boundaries). Never fabricate a boundary id.

## Adding or editing an entity
1. Edit `data/registry.seed.json` (hand-curated records) — keep it schema-valid.
2. Set `provenance.sources` (`manual` for hand-curated) and `last_verified` (today, ISO).
3. Give new records the next free `betanyc_id` (`BNYC-ORG-NNNNNN`); ids are immutable, never reused.
4. Removed/renamed entities: change `status` + set `valid_to`. **Do not delete records.**
5. Validate: `python -m pytest` (checks the seed against the schema, offline).

## Reporting issues
Open an issue for a missing entity, a wrong/dead domain, or a data-quality problem. Include the source
you'd verify against.

## Scope
NYC + NYS government and public-authority **web properties** across all TLDs. In scope: agencies,
authorities, PBCs, IDAs, LDCs, bi-state authorities operating in NY. Out of scope: federal entities,
private orgs, and geometry (that lives in nyc-boundaries — we reference it).
