# Freshness & maintenance

The registry is designed to **stay current**, not be a one-time dump (design doc §4).

## Three-tier refresh
1. **Auto (city):** re-sync from MODA (`sync_moda.py`) — MODA maintains its own QA pipeline; we track its record ids.
2. **Semi-automated (authorities):** re-sync from ABO (`sync_abo.py`); ABO updates the Directory annually. New/removed authorities land as **proposals**, not silent writes.
3. **Hand-curated (state agencies, non-.gov marquee entities, legacy domains):** editorial additions with `provenance.sources = ["manual"]`.

## Diff-as-proposals
`build_registry.py` never deletes. Adds and crosswalk enrichments are written to
`data/build_proposals.json` for human confirmation. Removals/renames upstream become
`status` changes + `valid_to`, preserving defunct-entity history.

## Versioning & cadence
- The built `data/registry.json` is committed; each rebuild is a reviewable diff.
- Suggested cadence: monthly CI rebuild (once sources are wired), plus an ad-hoc rebuild when ABO posts its annual update.
- **Not yet automated** — no CI is wired while live pulls remain gated.

## Ownership
- **Sync scripts / CI:** software-engineer (code correctness, dependency + version hygiene).
- **Editorial curation** (which entities, which domains, verification): research-intelligence + operator sign-off.
