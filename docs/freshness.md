# Freshness & maintenance

The registry is designed to **stay current**, not be a one-time dump (design doc §4).

## Source export dates
| Source | Cache file | Export downloaded | Rows |
|---|---|---|---|
| MODA / NYC Open Data `t3jq-9nkf` | `data/cache/moda_nyc-governance-organizations.csv` | **2026-07-15** | 306 (all `operational_status = Active`) |
| NYC Greenbook `mdcw-n682` | `data/cache/greenbook_mdcw-n682.csv` | **2026-07-15** (source last refreshed **2023-12**) | 2,555 officer rows → 123 agencies |
| nyc.gov agency directory (same upstream as `t3jq-9nkf`) | `data/cache/nycgov_agencydirectory.json` | **2026-07-15** | 306 (validation only) |
| Wikidata NYC gov orgs (WDQS SPARQL) | `data/cache/wikidata_nyc_gov_orgs.json` | **2026-07-15** | 232 bindings → 109 QIDs (88 with `P856`) |
| nyc-boundaries layer index (`BoundaryId` union + `layers`) | `data/cache/nyc-boundaries_layers_index.ts` | **2026-07-15** | 22 published layer ids |
| About-page crawl (live agency sites) | *(no cache — `data/descriptions.json` is the output)* | **2026-07-15** | 285 targets crawled + 32 `no_url` |
| WAF recovery (browser-pane, operator-authorized) | `data/cache/waf_captures_raw/` + `waf_capture_meta.json` (git-ignored) | **2026-07-16** | batches 1–2: 50 of 152 (41 `ok`, 9 `no_about_found`); 102 remaining |

Cache files are git-ignored (`data/cache/`); only the built `data/registry.json` — plus the
committed, derived `data/curation.json`, `data/greenbook_enrichment.json`,
`data/greenbook_reconciliation.json`, `data/wikidata_enrichment.json`,
`data/nyc_boundaries_layers.json` (extracted layer vocabulary), and `data/boundaries_mapping.json`
(hand-authored layer→operator assertions) — travel in git.

Full phase-2 pipeline (offline; caches placed manually, access-gated):

```sh
# 1. Operator manually places the gated exports in data/cache/ (no script fetches).
python scripts/sync_moda.py                 # t3jq rows.csv -> records_moda.json (Active-only)
python scripts/build_registry.py            # seed + moda + curation -> registry.json (317)
python scripts/sync_greenbook.py            # aggregate + nycresolver reconcile -> greenbook_enrichment.json + reconciliation.json
python scripts/sync_wikidata.py             # domain-anchor Wikidata QIDs -> wikidata_enrichment.json + reconciliation.json
python scripts/sync_boundaries.py           # extract nyc-boundaries layer vocabulary -> nyc_boundaries_layers.json
python scripts/build_registry.py            # re-apply with Greenbook + Wikidata + boundaries areas attached (idempotent)
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
proposals**, never auto-attachments.

**Place-vs-organization guard (operator re-point, 2026-07-15, PR #8).** A distinctive domain match is
still *not* sufficient when the `P856` item's class is a **place** rather than the office/organization
— Wikidata routinely lists an office's website as the `P856` of the *place* it administers. Operator
re-point directives in `data/curation.json § wikidata_repoints` override the raw domain match by
`entity_id + reject_qid`, applied by `sync_wikidata.apply_repoint` (which takes precedence over the
domain auto-match). The four borough-president office entities were auto-matched to their **borough
place** QIDs (Q18426 The Bronx, Q18419 Brooklyn, Q11299 Manhattan, Q18432 Staten Island) purely via
the office site in `P856`. Verified against Wikidata (raw results:
`data/cache/wikidata_borough_president_probe.json`) that **no per-borough Borough-President office
item exists** (0 instances/subclasses of the generic office `Q4946327`; the domains are claimed only
by borough/county place items), so all four were **dropped** (`no_suitable_item`) rather than attach
a place. Q564793 (Jacob K. Javits Convention Center) stays attached — it is dual-classed *convention
center* **and** *state agency of New York* with no separate operating-corporation item, so it is a
genuine organization match (flagged name-divergent, kept with note).

Phase-3 outcome (after re-point): **3 auto-attached** (NYC Parks Q1894232, H+H Q7013226, Javits
CCOC Q564793), **4 re-pointed → dropped**, **22 proposals, 1 ambiguous-name, 20 apex-only, 59
unmatched**; `data/wikidata_reconciliation.json` carries the `re_pointed` / `no_suitable_item`
decision trail (rejected QID + place class), proposals, and legacy-domain leads. **Coverage: 3 of
317 entities carry a `wikidata` identifier.**

## nyc-boundaries — layer vocabulary & areas linkage (phase 4, 2026-07-15)

Cache `data/cache/nyc-boundaries_layers_index.ts` is the Boundaries Map's
`frontend/src/assets/boundaries/index.ts`, placed by the operator on **2026-07-15** (this is the
authoritative layer vocabulary — the `BoundaryId` union of 22 published layer ids plus each layer's
human-readable name/description). `scripts/sync_boundaries.py` reads it only (never fetches) and
emits the committed `data/nyc_boundaries_layers.json`. To refresh, re-fetch `index.ts` from
BetaNYC/nyc-boundaries into the cache, re-run `sync_boundaries.py`, and rebuild.

`data/boundaries_mapping.json` (hand-authored) asserts the layer→operator and borough-president→
county links; `build_registry.apply_boundaries_mapping` applies them, validating every
`{scheme:"nyc-boundaries"}` layer against the extracted vocabulary (**unknown layer = build
error**). Phase-4 outcome: **13 area refs attached** across **12 entities** — 8 `operates_layer`
whole-layer refs (NYPD→`pp`+`ps`, DSNY→`dsny`, FDNY→`fb`, DOE→`sd`, City Council→`cc`, Community
Boards→`cd`, Board of Elections→`ed`) + 5 `us_census_geoid` `jurisdiction` refs (the borough
presidents). Entity count unchanged (**317** — no entities minted; per-board `cd` linkage deferred).
Each `areas[]`-attached entity's `provenance.sources` gains `manual`. Idempotent: build → syncs →
build is byte-identical.

## About-page descriptions — crawl date & re-run (phase 5, 2026-07-15)

`data/descriptions.json` was produced by an operator-authorized run of `scripts/crawl_about.py`
on **2026-07-15** (each entry also carries its own `fetched_at`). The crawl set
(`data/crawl_targets.json`) is derived offline first by `scripts/crawl_targets.py`.

Re-run (the two-step, access-gated pipeline):

```sh
python scripts/crawl_targets.py                      # offline: registry + MODA url column -> crawl_targets.json
python scripts/crawl_about.py                        # prints the plan and EXITS (no fetch without the flag)
python scripts/crawl_about.py --operator-authorized  # perform the polite crawl -> descriptions.json
```

The crawler is **idempotent and resumable**: it skips any entity already in `descriptions.json`
(`--force` re-crawls) and rewrites the file after each entity, so an interrupted run resumes where
it stopped. `--limit N` crawls at most N not-yet-done targets (smoke runs).

**Coverage (2026-07-15 run, 317 entities):** **91 `ok`** (extracted), **173 `fetch_failed`**,
**32 `no_url`**, **12 `no_about_found`**, **7 `extraction_empty`**, **2 `robots_disallowed`**.
The dominant failure is `fetch_failed`, and **153 of the 173** are on a `*.nyc.gov` host
(`www.nyc.gov` 131, `www1.nyc.gov` 21, `nyc.gov` 1): the citywide front is behind **Akamai** and
403s automated requests. The crawler's circuit-breaker abandoned the host after 4 consecutive 403s
(**152 targets short-circuited**, note `host_waf_blocked`) rather than hammering it, so those
entities are **recorded, not silently dropped**, and can be revisited via a different access path
(e.g. a Chrome-driven pass) in a later phase. Notably, some `*.nyc.gov` *subdomains* are NOT
WAF-walled — `ibo.nyc.gov`, `cityclerk.nyc.gov` extracted fine. Entities on their own distinctive
domains (authorities, PBCs, `.org`/`.edu`/`.com` marquee entities) are where the crawl actually
yields descriptions. 15 `ok` texts hit the 5,000-char cap (`truncated: true`). Run wall-clock:
~24 min for the networked portion.

## About-page descriptions — WAF recovery, browser-pane pass (2026-07-16, batch 1)

The 152 `www.nyc.gov` / `www1.nyc.gov` targets the phase-5 Akamai circuit-breaker recorded
(`host_waf_blocked`, never fetched) are being recovered through a **human-context browser** (the
app's Browser pane), which passes the WAF where stdlib/`urllib` gets a 403. This pass was
**explicitly operator-authorized (Noel, 2026-07-15)** — the standing rule is that no registry
component fetches without operator action, and a browser-driven crawl of live agency sites is a
fetch. The pass is **read-only, sequential, and politely paced** (~2s between navigations, one page
at a time); it defeats nothing — the browser pane *is* the sanctioned path, so no header-spoofing or
WAF-evasion was attempted.

**Method (offline-reproducible tooling around the browser):**
```bash
python scripts/plan_waf_candidates.py       # offline: waf_blocked.json -> ordered about-page candidates
# (browser-pane loop navigates candidates, saves raw page text to data/cache/waf_captures_raw/<id>.txt,
#  records the winning URL + status hint in data/cache/waf_capture_meta.json)
python scripts/assemble_waf_captures.py      # offline: raw txt + meta -> data/cache/waf_page_captures.json
python scripts/extract_waf_descriptions.py   # offline: verbatim prose -> data/descriptions_recovery.json
python scripts/merge_descriptions.py         # offline: fold recovery into data/descriptions.json (deterministic)
```
Candidate order per `/site/<slug>/` target: the nyc.gov Full Site Editing about conventions
`/about/about-<slug>.page` then `/about/about.page` (canonical `www` host), with a start URL that is
already a specific subpage tried first and the bare `index.page` landing last. A 404 is detected from
the tab title (`"NYC"` + the "We're Sorry / non-existing page" shell) with no extra fetch.
Extraction is **verbatim-or-nothing**: it isolates the agency's own about prose (dropping the nav
column, social-media / audio-description / playback boilerplate) and records `extraction_empty` /
`no_about_found` rather than guessing when a page has no clean descriptive block (link-farm homepages
like FDNY/DOT, or member rosters). `read_page`'s a11y tree comes back empty on nyc.gov's FSE pages,
so href-based link-scanning is unavailable — recovery relies on the deterministic path conventions.

**Merge rule:** a recovery record overrides a base record **only** when the base status is
`fetch_failed` (the exact WAF bucket this pass re-examined) — `ok` overrides it with recovered text,
and `no_about_found` / `extraction_empty` reclassify it (more honest than leaving `fetch_failed`,
which implies the WAF is still blocking). Any other base status (`ok`, `no_url`, `robots_disallowed`)
is never overwritten. Idempotent.

**Batch 1 coverage (25 of 152 targets):** **20 recovered `ok`** (verbatim agency prose) +
**5 reclassified `no_about_found`** (browser reached the site; no clean about block). Registry
totals moved **91 → 111 `ok`**.

**Widened candidate conventions (operator corrections, 2026-07-15).** Two nyc.gov about-page
families the batch-1 derivation missed, now probed by `plan_waf_candidates.py`:
- **`/site/<slug>/about/overview/overview.page`** (and `…/about/overview.page`) — where FDNY's
  About lives; and
- the **legacy pre-CMS family `/html/<slug>/html/about/about.shtml`** (with `.html` sibling) — where
  DOT's About lives. A `/html/` start URL derives its legacy slug from the URL itself (it can differ
  from the modern `/site/` slug); a `/site/` target also gets one same-slug legacy fallback.
  With these, FDNY and DOT (batch-1 `no_about_found`) were **recovered to `ok`** — a recovered `ok`
  now upgrades a prior non-terminal failure in the merge. Any entity whose About is served from a
  live legacy `/html/` path is itself a web-history datum, recorded in
  `descriptions_recovery.json._legacy_path_entities` (currently: DOT) for the Wayback harvester and
  reconciliation to inherit.

**Batch 2 coverage (25 targets, entities 26–50 + the FDNY/DOT batch-1 upgrades):** **21 recovered
`ok`** + **6 reclassified `no_about_found`** (CFCP and Get-Stuff-Built site paths removed; DCP moved
off `/site/`; NYC Her Future tagline-only; the Juvenile Justice board maps to an ACS reports page;
Childcare's about page is unpublished Lorem ipsum). CCHR was recovered via a legacy `/html/` redirect
stub that revealed its modern `inside-cchr.page` URL. Registry totals now **111 → 132 `ok`**;
`fetch_failed` 148 → 123; `no_about_found` 17 → 21. **Remaining: 102 WAF targets** (batches 3–6) with
the identical mechanical loop.

The **description review queue** (`docs/description-review-queue.md`) is regenerated from
`descriptions.json` after every batch by the deterministic `scripts/build_review_queue.py`
(preserves human-edited resolution notes; `--check` mode gates staleness). Raw captures and the meta
manifest live in git-ignored `data/cache/`; `data/descriptions_recovery.json`, the merged
`data/descriptions.json`, and the review queue are the committed artifacts.

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
