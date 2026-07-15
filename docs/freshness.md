# Freshness & maintenance

The registry is designed to **stay current**, not be a one-time dump (design doc §4).

## Source export dates
| Source | Cache file | Export downloaded | Rows |
|---|---|---|---|
| MODA / NYC Open Data `t3jq-9nkf` | `data/cache/moda_nyc-governance-organizations.csv` | **2026-07-15** | 306 (all `operational_status = Active`) |

Cache files are git-ignored (`data/cache/`); only the built `data/registry.json` is committed.
Re-run the city sync after replacing the cache file:

```sh
# 1. Operator manually places a fresh t3jq-9nkf rows.csv at the path above (access-gated; no script fetches).
python scripts/sync_moda.py       # -> data/cache/records_moda.json  (Active-only)
python scripts/build_registry.py  # -> data/registry.json + data/build_proposals.json (idempotent)
```

## Three-tier refresh
1. **Auto (city):** re-sync from MODA (`sync_moda.py`) — MODA maintains its own QA pipeline; we track its record ids. Population landed 2026-07-15 (phase 1): 306 Active orgs merged with the 17-entity seed → 318 entities (5 seeds matched by name and gained a `nyc_goid`; 301 minted new).
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
