# Freshness & maintenance

The registry is designed to **stay current**, not be a one-time dump (design doc §4).

## Source export dates
| Source | Cache file | Export downloaded | Rows |
|---|---|---|---|
| MODA / NYC Open Data `t3jq-9nkf` | `data/cache/moda_nyc-governance-organizations.csv` | **2026-07-15** | 306 (all `operational_status = Active`) |
| NYC Greenbook `mdcw-n682` | `data/cache/greenbook_mdcw-n682.csv` | **2026-07-15** (source last refreshed **2023-12**) | 2,555 officer rows → 123 agencies |
| nyc.gov agency directory (same upstream as `t3jq-9nkf`) | `data/cache/nycgov_agencydirectory.json` | **2026-07-15** | 306 (validation only) |
| Wikidata NYC gov orgs (WDQS SPARQL) | `data/cache/wikidata_nyc_gov_orgs.json` | **2026-07-15** | 232 bindings → 109 QIDs (88 with `P856`) |

Cache files are git-ignored (`data/cache/`); only the built `data/registry.json` — plus the
committed, derived `data/curation.json`, `data/greenbook_enrichment.json`, and
`data/greenbook_reconciliation.json` — travel in git.

Full phase-2 pipeline (offline; caches placed manually, access-gated):

```sh
# 1. Operator manually places the gated exports in data/cache/ (no script fetches).
python scripts/sync_moda.py                 # t3jq rows.csv -> records_moda.json (Active-only)
python scripts/build_registry.py            # seed + moda + curation -> registry.json (317)
python scripts/sync_greenbook.py            # aggregate + nycresolver reconcile -> greenbook_enrichment.json + reconciliation.json
python scripts/sync_wikidata.py             # domain-anchor Wikidata QIDs -> wikidata_enrichment.json + reconciliation.json
python scripts/build_registry.py            # re-apply with Greenbook + Wikidata enrichment attached (idempotent)
python scripts/validate_nycgov_directory.py # assert directory record_ids ⊆ registry nyc_goids
```

`sync_greenbook.py` and `sync_wikidata.py` both reconcile against an in-memory **un-enriched**
build (`build_unenriched()` = seed + moda + curation), so their matching never sees prior
enrichment and every full rebuild is byte-identical. Build once, run the syncs, build again to fold
the enrichment in — all builds idempotent.

## Wikidata — SPARQL query & reproducibility (phase 3, 2026-07-15)

Cache `data/cache/wikidata_nyc_gov_orgs.json` was captured (operator-authorized) from the Wikidata
Query Service, `https://query.wikidata.org/sparql`, on **2026-07-15**. Wikidata content is CC0.
The query captures NYC government organizations three ways (`P361` part-of the NYC government, or an
instance/subclass of *government agency* / *government organization* located in NYC), with the
English label, aliases, and `P856` official website:

```sparql
SELECT ?item ?itemLabel ?website ?altLabel WHERE {
  {
    ?item wdt:P361 wd:Q6284238 .              # part of: government of New York City
  } UNION {
    ?item wdt:P31/wdt:P279* wd:Q327333 .      # instance/subclass of: government agency
    ?item wdt:P131 wd:Q60 .                    # located in: New York City
  } UNION {
    ?item wdt:P31/wdt:P279* wd:Q2659904 .      # instance/subclass of: government organization
    ?item wdt:P131 wd:Q60 .
  }
  OPTIONAL { ?item wdt:P856 ?website . }
  OPTIONAL { ?item skos:altLabel ?altLabel . FILTER(LANG(?altLabel) = "en") }
  SERVICE wikibase:label { bd:serviceLabel language "en". ?item rdfs:label ?itemLabel. }
}
```

The SPARQL JSON returns one binding per `item × altLabel × website` combination (232 bindings →
109 distinct QIDs, 88 with a website). `scripts/sync_wikidata.py` reads the cache only — it does
**not** re-fetch. To refresh, re-run this query under explicit operator authorization, save the raw
result back to `data/cache/wikidata_nyc_gov_orgs.json`, and update the fetch date above.

**Matching (domain-anchored, tier-gated):** a QID auto-attaches an `identifiers[]{scheme:"wikidata"}`
**only** when its `P856` host (scheme/path stripped, `www`-normalized, host-only) is owned by exactly
one registry entity — a *distinctive* domain. Apex-shared hosts (`nyc.gov`, owned host-only by 154
entities) are never a match; they fall through to exact name/alias matching, which produces **review
proposals**, never auto-attachments. Phase-3 outcome: **7 auto-attached, 22 proposals, 1
ambiguous-name, 20 apex-only, 59 unmatched**; `data/wikidata_reconciliation.json` carries proposals,
legacy-domain leads, and name-divergence caveats (Wikidata conflates the four borough *place* items
with their borough-president *office* websites — auto-attached on the domain rule, flagged for
operator review).

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
