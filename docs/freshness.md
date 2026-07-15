# Freshness & maintenance

The registry is designed to **stay current**, not be a one-time dump (design doc §4).

## Source export dates
| Source | Cache file | Export downloaded | Rows |
|---|---|---|---|
| MODA / NYC Open Data `t3jq-9nkf` | `data/cache/moda_nyc-governance-organizations.csv` | **2026-07-15** | 306 (all `operational_status = Active`) |
| NYC Greenbook `mdcw-n682` | `data/cache/greenbook_mdcw-n682.csv` | **2026-07-15** (source last refreshed **2023-12**) | 2,555 officer rows → 123 agencies |
| nyc.gov agency directory (same upstream as `t3jq-9nkf`) | `data/cache/nycgov_agencydirectory.json` | **2026-07-15** | 306 (validation only) |

Cache files are git-ignored (`data/cache/`); only the built `data/registry.json` — plus the
committed, derived `data/curation.json`, `data/greenbook_enrichment.json`, and
`data/greenbook_reconciliation.json` — travel in git.

Full phase-2 pipeline (offline; caches placed manually, access-gated):

```sh
# 1. Operator manually places the gated exports in data/cache/ (no script fetches).
python scripts/sync_moda.py                 # t3jq rows.csv -> records_moda.json (Active-only)
python scripts/build_registry.py            # seed + moda + curation -> registry.json (317)
python scripts/sync_greenbook.py            # aggregate + nycresolver reconcile -> greenbook_enrichment.json + reconciliation.json
python scripts/build_registry.py            # re-apply with Greenbook contact scaffolding attached (idempotent)
python scripts/validate_nycgov_directory.py # assert directory record_ids ⊆ registry nyc_goids
```

`sync_greenbook.py` reads the built `registry.json` as its match target, so build once before it,
then build again to fold in the enrichment. Both builds are idempotent.

## Three-tier refresh
1. **Auto (city):** re-sync from MODA (`sync_moda.py`) — MODA maintains its own QA pipeline; we track its record ids. Population landed 2026-07-15 (phase 1): 306 Active orgs merged with the 17-entity seed → 318 entities. **Phase 2 (2026-07-15):** the operator-confirmed EDC merge (`data/curation.json`) collapses the separately-minted "Economic Development Corporation" into seed `nycedc` → **317 entities** (5 seeds matched by name + 1 by curated identifier gained a `nyc_goid`; 300 minted new). Greenbook contact scaffolding then attaches to 27 exact-matched city entities (no change to the count).
2. **Semi-automated (authorities):** re-sync from ABO (`sync_abo.py`); ABO updates the Directory annually. New/removed authorities land as **proposals**, not silent writes.
3. **Hand-curated (state agencies, non-.gov marquee entities, legacy domains):** editorial additions with `provenance.sources = ["manual"]`.

## Diff-as-proposals
`build_registry.py` never deletes. Adds and identifier enrichments are written to
`data/build_proposals.json` for human confirmation. Removals/renames upstream become
`status` changes + `dissolution_date`, preserving defunct-entity history.

## Versioning & cadence
- The built `data/registry.json` is committed; each rebuild is a reviewable diff.
- Suggested cadence: monthly CI rebuild (once sources are wired), plus an ad-hoc rebuild when ABO posts its annual update.
- **Not yet automated** — no CI is wired while live pulls remain gated.

## Ownership
- **Sync scripts / CI:** software-engineer (code correctness, dependency + version hygiene).
- **Editorial curation** (which entities, which domains, verification): research-intelligence + operator sign-off.
