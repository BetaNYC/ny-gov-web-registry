# Data sources & provenance

Every record's `provenance.sources` names which upstream(s) asserted it. Values in
`identifiers[]` are populated **only** when verified against the scheme's named authority — never
inferred. Each scheme's authority, format, and verification rule is catalogued in [`schemes.md`](schemes.md).

## MODA — city entities (primary city source)
- **Repo:** https://github.com/MODA-NYC/nyc-governance-organizations · **Open Data:** `t3jq-9nkf`
- **License:** MIT.
- **Coverage:** NYC governance organizations (broader than mayoral agencies — boards, commissions, advisory/regulatory bodies). The 2026-07-15 export (`t3jq-9nkf` rows.csv) has **306 rows, all `operational_status = Active`**. Immutable record id. Has a `url` field (**current site only**, no legacy-domain history).
- **Caveat:** the multi-domain / legacy history is ours to build; MODA only carries the current URL.
- **`record_id` format:** the export carries the **`NYC_GOID_XXXXXX`** form (e.g. `NYC_GOID_000476`), not a bare 6-digit numeric. Stored **verbatim as a string** under scheme `nyc_goid` (see [`schemes.md`](schemes.md)); do not strip the prefix.
- **Sync:** `scripts/sync_moda.py` — field map verified **2026-07-15** against the actual 17-column export. Emits v2 records:
  - `record_id` → `identifiers[] {scheme: "nyc_goid"}` (verbatim)
  - `organization_type` → `classification`
  - `acronym` → `short_name` **and** `other_names[]` (noted `acronym`)
  - `alternate_or_former_names` / `alternate_or_former_acronyms` (`;`-delimited) → `other_names[]` (noted `alternate or former name` / `alternate or former acronym`)
  - `url` → `web_properties[] {role: "primary"}` (host only)
  - `operational_status` → **Active-only gate**; emitted `status` is `active`.
  - **Deferred (no phase-0 schema home; do not invent fields):** `principal_officer_*` (personnel identity — no person/officer structure; `contact_details[]` is for contact points, not identity → **phase 2**), `listed_in_nyc_gov_agency_directory` (the flag phase 2 validates against the nyc.gov scrape, user story 13), `reports_to` (hierarchy-by-name; needs a second pass to resolve to minted ids), `name_alphabetized` / `in_org_chart` (display-only).
  - **No mandate seed:** this export has no establishing-authority / Charter / EO column, so `mandates[]` cannot be seeded from it (contrary to earlier assumption). Left to City Record archive curation.
  - **Matching:** the build seam matches identifier-scheme-first then **exact normalized name**. Operator-confirmed near-duplicate merges (e.g. seed `nycedc` = export "Economic Development Corporation", `NYC_GOID_000177`) are recorded in `data/curation.json` and injected onto the seed at build time, so the identifier match enriches instead of minting a duplicate (phase 2). Messy *external-source* names (Greenbook) are reconciled via **nycresolver** — see below.
  - Re-verify the field map if MODA revises the schema.

## Greenbook — city contact scaffolding (structure donor, STALE)
- **Source:** NYC Greenbook (Official Directory of the City of New York) · NYC Open Data `mdcw-n682`.
- **Shape:** one row **per officer** (2,555 rows in the 2026-07-15 export), each carrying the agency's name/acronym/website plus an office address + phones. `sync_greenbook.py` aggregates to **123 distinct agencies**.
- **Staleness:** last refreshed **2023-12**. Used as a **structure donor only** — agency-level website/address/phone scaffolding — **never** as current-officer data. Officer identities (names, titles) are **deliberately dropped**; every attached value is stamped `Greenbook 2023-12 (stale…)` in its `contact_details` note.
- **Matching:** aggregated agency names are reconciled against the registry with **nycresolver** (MODA's own matcher, github.com/MODA-NYC/nyc-entity-resolver, Apache-2.0), built **offline** from registry entities (no Socrata fetch). Tier-gated per PR #5:
  - **exact + same `government_level`** → contact scaffolding attached to the matched entity (`provenance.sources` gains `greenbook`);
  - **fuzzy / cross-`government_level` / no-match** → written to `data/greenbook_reconciliation.json` for human review, **never applied**. Cross-level is the guard that keeps a NYS authority (ESD) out of the city-EDC candidate set — it also holds legitimate marquee authorities/PBCs (CUNY, NYCHA, H+H, NYCEDC) for human confirmation rather than auto-attaching.
  - **Unmatched Greenbook agencies are never minted as entities** (a Greenbook division is not an entity).
- **Website leads:** an agency website whose registrable host (www/www1 normalized) **differs** from every domain the entity already owns is added as an **unverified `legacy` web-property lead** — never overwriting the current primary.
- **Sync:** `scripts/sync_greenbook.py` → `data/greenbook_enrichment.json` (applied by `build_registry.py`) + `data/greenbook_reconciliation.json` (review report).

## nyc.gov agency directory — same-upstream VALIDATION (not a new source)
- **Endpoint:** the nyc.gov agency-directory JSON (306 records; export fetched **2026-07-15**).
- **Discovery (2026-07-15):** this is the **same upstream** as the canonical dataset — identical `record_id` values (`NYC_GOID_XXXXXX`), i.e. the directory page is powered by the governance registry (`t3jq-9nkf`). It is therefore a **validation input, not new entities**.
- **Check:** `scripts/validate_nycgov_directory.py` asserts directory `record_id`s ⊆ registry `nyc_goid`s and reports drift both ways; it also surfaces the `listed_in_nyc_gov_agency` flag `sync_moda` deferred (user story 13). 2026-07-15 result: **306 ⊆ 306, zero drift**; 177 records flagged `listed_in_nyc_gov_agency = true`.

## ABO — public authorities (primary authority source)
- **Dataset:** https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x
- **License:** listed **"unspecified"** on data.ny.gov — resolve before redistributing raw ABO data in a public build.
- **Coverage:** 608 active authorities, four classes (State / Local / IDA / LDC). 9 columns (verified 2026-07-11): `public_authority_type`, `public_authority_name`, `address_line_1`, `address_line_2`, `city`, `state`, `zip`, `website`, `georeference`.
- **`website` column:** added **2026-01-15**; type URL; **self-reported via PARIS, blank where not provided** → partial coverage.
- **No id column:** the dataset exposes no stable authority id, so the `nys_abo` identifier scheme cannot be filled from it (see `schemes.md`). Backup per-class listings: `abo.ny.gov/paw/paw_weblisting{ST,LOCAL,IDA,LDC}.html`.
- **Snapshot:** `data/ai-readability/sources/2026-11-06/abo-directory-4vym-q77x.json` (Socrata JSON; `website` is an object `{"url": …}`). Copied to `data/cache/abo_4vym-q77x.json` for the build; `sync_abo.py` reads that JSON and still accepts a CSV export (`abo_4vym-q77x.csv`) as a fallback.
- **Sync:** `scripts/sync_abo.py` → `data/cache/records_abo.json` (v2 records; fixed 2026-11-06, it previously emitted the retired v1 shape) and `data/statewide_web_review.json § authorities`.
  - **Scope (Noel, 2026-11-06):** `State` and `Local` rows only (156). IDA and LDC rows are skipped (stretch goal).
  - `public_authority_name` → `name`; `public_authority_type` → `classification` `state-authority` / `local-authority`; `government_level` `authority`; address columns → one `contact_details` address (office address, noted with the snapshot date); `provenance.sources` `[abo]` (+ `state_listing` when the listing names a host).
  - **Dedupe:** an ABO row that is an existing entity is matched, never re-minted: by an operator-confirmed `data/curation.json § abo_matches` entry (all 18 are listed there, exact-name ones included), else by exact normalized name. A match only *adds*: `abo` to provenance, the ABO name to `other_names[]`, and listing hosts the entity lacks. It never changes the entity's level, classification or primary site. Possible duplicates go in `abo_holds` and are not emitted.
  - **Websites:** a root URL → `primary` web property (host only, self-reported). A URL with a path is a page on a host the authority does not own → `url_conventions[]`, no web property. When the state agency listing names hosts for the authority, the curated listing host is primary and a differing ABO host goes to review. Malformed hosts (checked against the Public Suffix List snapshot), a root host claimed by several authorities (`nyshcr.org`, `mvwa.us`), and municipal sites listed as an agency's website (`abo_website_holds`) go to review, never applied.

## ny.gov — state executive agencies
- **Directory:** https://www.ny.gov/agencies (redirects to `/state-agencies-and-departments`) — **no bulk export, no stated license.** The list renders client-side (a search widget), so a plain HTTP fetch returns no agencies.
- **Snapshot (2026-11-06):** read once in a browser, paging through all results: `data/ai-readability/sources/2026-11-06/nygov-state-agencies-and-departments.json` (98 entries: name + ny.gov directory page). It mixes agencies, authorities, the Legislature, elected offices and services (511NY).
- **Used for:** agency names (`nygov` in provenance) and each agency's `links[]` entry. No agency website was fetched.

## Agency Contacts — data.ny.gov `kjw8-pupa`
- **Publisher:** Office of the Governor. **License:** not stated in the dataset metadata. Rows last updated 2025-04-11.
- **Columns (verified 2026-11-06 from `/api/views/kjw8-pupa.json`):** `contact`, `primary_contact`, `title`, `phone_number`, `agency`, `agency_contact_url`.
- **What we keep:** `agency` and `agency_contact_url` only (SoQL `$select`), 79 distinct agencies, in `data/ai-readability/sources/2026-11-06/kjw8-pupa-agency-contacts-agencies.json`. Contact names, titles and phones were never fetched (the Greenbook rule: no officer identity).
- **Used for:** a second name source (`nygov_contacts` in provenance) and a second lead for an agency's primary host: when the contact URL's host equals the chosen primary, the web property's note says so.

## Sync: `scripts/sync_nygov.py` — state executive agencies (2026-11-06)
- Joins the three snapshots above through `data/curation.json`: `state_agencies` (one hand-curated entry per agency: name, short name, which directory names it, primary host), `state_listing_codes` (every one of the listing's 87 agency codes → an agency, an ABO authority, an existing entity, or `review:<reason>`), `state_listing_host_overrides` (a host filed under the wrong code), and `state_directory_covered_elsewhere`.
- **Names:** the directory's wording, with a `New York State` prefix where the bare name would collide with a city body (`Department of Transportation` exists for NYC too). The directory's own wording and the kjw8-pupa wording go in `other_names[]`. Every agency record is marked to mint new: a name collision with any existing entity fails the build instead of merging.
- **Websites:** the curated primary host + every other listing host for that code as `microsite` (the schema has no "application/sub-site" role; each note says what the host is when known).
- **Not minted (review file):** the Governor, Comptroller and Attorney General (statewide elected; no `government_level` fits), the Assembly and legislative ethics hosts, the courts, codes no directory names (`CJN`, `DCB`, `VDC`, `PCFB`, `IOLA`), `CC&F` (name unverified), the Commission on Judicial Conduct and Waterfront Commission (level unclear), two credit unions, and directory names with no listing host.
- **Output:** `data/cache/records_nygov.json`, `data/statewide_web_review.json § state_agencies`.

## A state agency listing — state websites (2026-11-06)
- **Credit as:** "a state agency listing". Shared with BetaNYC privately; **the link is private** and is not published.
- **Coverage:** 307 New York State websites with the agency that runs each, mostly executive agencies under `ny.gov` (264 hosts). It is the only list we have of `*.ny.gov` agency sites: CISA's .gov list carries `ny.gov` itself, not its subdomains. Missing: the Legislature, the courts, most authorities, and all local governments.
- **What we keep:** domain, URL and agency only, in `data/ai-readability/sources/2026-11-06/state-agency-listing-websites.csv`. The rest of the file (review scores, staff names, internal links) is not committed.
- **Used by:** `scripts/ai_readability_websites.py` → `data/ai-readability/websites.csv` (`source_list_state_agency`), and since 2026-11-06 as a registry source: `provenance.sources` value **`state_listing`**. It seeds the web properties of the 53 state agencies (`sync_nygov.py`) and the hosts of the authorities it names (`sync_abo.py`). Copied to `data/cache/state_agency_listing_websites.csv` for the build.
- **Its `agency` column is a short code** (`DOH`, `OMH`), sometimes a name (`Department of Law`). Every code is mapped by hand in `data/curation.json § state_listing_codes`; a new code in a refreshed listing fails the sync until it is curated.

## Counties, cities, towns and villages (issue #23, built 2026-11-13; not minted yet)
Source facts, counts and staleness: [`sources-statewide.md`](sources-statewide.md). Plan and website rule: [`registry-growth-plan.md`](registry-growth-plan.md) steps 3 to 6. Snapshots and the record of each read: `data/ai-readability/sources/2026-11-13/README.md`.
- **Spine: NYS Civil Boundaries GIS** (NYS ITS GIS Program Office), layers Counties, Cities, Towns, Villages, attribute queries only. Gives who exists and `SWIS`, `GNIS_ID`, `FIPS_CODE`, `COUNTY`, and the `NYC` flag on counties. `provenance.sources` value **`nys_gis`**. **The 2026-11-13 read failed (service not started), so there is no GIS snapshot yet and nothing is minted.**
- **Cross-check: OSC entity tables** (`entity-tables.xls`), extracted to JSON by `scripts/extract_osc_entities.py`. Gives the 12-digit municipal code (`nys_osc_muni_code`), attached only on a one-to-one county + name match. `provenance.sources` value **`osc`**.
- **Website leads:** ITS `55k6-h6qq` (joined by SWIS; `its_localities`), the CISA .gov list (`scripts/sync_dotgov.py`, joined by tier + name; `dotgov`), and the hand-checked city and county rows of BetaNYC's NY data portal registry (only a registrable host or `www.` + one; a portal or vendor subdomain is never a lead; no provenance value of its own, the web property's note names it).
- **Website rule:** a host is `primary` only when two different sources name it, and only if no other place is led to the same host and no existing entity owns it. Everything else goes to `data/statewide_web_review.json § localities`, including every path-only presence (proposed as a `url_conventions[]` entry, not applied). No lead was live-checked.
- **Sync:** `scripts/sync_dotgov.py` → `data/cache/leads_dotgov.json`; `scripts/sync_nys_localities.py` → `data/cache/records_localities.json` (read by `build_registry.py`) and the review section. Ids follow the plan (`albany-county`, `town-of-colonie`; a repeated name gets the county on every member) and are passed to the build as `_mint_id`, so same-name places never merge by name.
- **Not minted, by design:** NYC's five counties and the City of New York (the borough presidents and the existing NYC entities stand for them), and villages that only the 2023 ITS list still carries (dissolved).

## Wikidata / IRS — identifier enrichment
- `wikidata` — QIDs from the WDQS SPARQL endpoint (CC0). **Sync:** `scripts/sync_wikidata.py` (phase 3, 2026-07-15), **domain-anchored**: auto-attaches a QID only when its `P856` official-website host is owned by exactly one entity (a *distinctive* domain); apex-shared hosts like `nyc.gov` never match, and exact name/alias matches become review proposals, never auto-attachments. Reads the operator-placed cache `data/cache/wikidata_nyc_gov_orgs.json` (does not fetch); query + fetch date in `freshness.md § Wikidata`.
- `us_irs_ein` (for `.org` PBCs like H+H). Verified values only. See `schemes.md`.

## nyc-boundaries — geography by reference (vocabulary source, phase 4, 2026-07-15)
- https://github.com/BetaNYC/nyc-boundaries — the registry's `areas[]` point into this by `{scheme: "nyc-boundaries", layer, id}`; **geometry is never copied here** (geometry-by-reference: the Boundaries Map is the single home for boundaries, the registry only asserts which entity operates which layer).
- **Vocabulary source:** the map's `frontend/src/assets/boundaries/index.ts` (the `BoundaryId` union + `layers` object) is the authoritative layer-id vocabulary. The operator placed it at `data/cache/nyc-boundaries_layers_index.ts` (fetched **2026-07-15**); `scripts/sync_boundaries.py` extracts the 22 layer ids + names/descriptions into the committed `data/nyc_boundaries_layers.json`. Cache is git-ignored; the extracted vocabulary travels in git (parallel to the greenbook/wikidata seams — a sync reads uncommitted cache, the build reads the committed derivative).
- **Mapping:** `data/boundaries_mapping.json` (hand-authored, like `data/curation.json`) records the layer→operator and borough-president→county assertions. `build_registry.apply_boundaries_mapping` attaches them at build time (additive, idempotent, `provenance.sources` gains `manual`) and **rejects an unknown nyc-boundaries layer id** (validated against the extracted vocabulary — a typo is a build error, not a silent bad reference). Full disposition of all 22 layers (attached vs. `defines_not_operates` / `no_registry_entity` / `deferred`) lives in that file and in `schemes.md § Geographic schemes`.
- **Community boards minted 2026-11-04:** the 59 boards are hand-curated seed records (`provenance.sources: [manual]`), each with a `jurisdiction` area on the `cd` layer. The asserting source per board is the board's own website, cross-checked against CAU dataset `ruf7-3wgc` and the Borough Presidents' pages. The generic `community-boards` entity (MODA) stays as the whole-layer `operates_layer` reference; `parent_id` links are left unset until a membership/part-of relation type exists (see `docs/district-web-presence.md`). Officer names are not carried, consistent with the Greenbook rule.
- Jurisdictions that exceed the map (MTA region, bi-state PANYNJ, statewide) carry an `area_note` naming a candidate external source (Census TIGER / NYS GIS Clearinghouse) instead. `us_census_geoid` is the documented national geographic scheme; the 5 borough presidents use it (`county` + FIPS) because nyc-boundaries has no borough layer.

## Descriptions — about-crawler (phase 5, 2026-07-15)

The registry's **only networked component**. Two scripts, both offline-testable at a pure seam:

- **`scripts/crawl_targets.py`** (offline, no fetch) derives the crawl set into the committed
  `data/crawl_targets.json`. `web_properties[]` stores **bare hosts** (phase-1 limitation), so the
  deep path many NYC entities live at (`https://www.nyc.gov/site/<slug>/index.page`) survives only
  in MODA's `url` column. Derivation priority per entity: **(1)** full URL from the MODA export
  (`url_by_goid`, keyed by the entity's `nyc_goid`); **(2)** `https://<primary web_property host>/`;
  **(3)** any web_property host; **(4)** `no_url` — recorded explicitly, never guessed. The 2026-07-15
  derivation: **285 targets** (278 from a MODA URL, 7 from a seed web_property) + **32 `no_url`**.
- **`scripts/crawl_about.py`** (networked, **gated behind `--operator-authorized`**) fetches each
  target's homepage, discovers an about page, and extracts the entity's description.
  - **Discovery** (≤ 4 fetches/entity): a homepage **link-scan** (anchors whose text/href signal
    about/mission/who-we-are, ranked shallowest-path-first so a generic `/about` beats a deep
    program page) tried first, then deterministic **path-probes** (the nyc.gov FSE convention
    `/site/<slug>/about/about.page`, generic `/about`, `/about-us`).
  - **Extraction** is stdlib `html.parser` only — prefers `<main>`/`<article>`, strips
    nav/header/footer/aside/script/style. The stored text is the **agency's own words, verbatim**
    (whitespace trimmed, capped at 5,000 chars with a `truncated` flag). The crawler **never
    summarizes or generates** — that boundary is deliberate.

### The contract: `data/descriptions.json` is the boundary artifact

Downstream consumers (notably the BetaNYC workspace's **team/contacts Mission drafting**, issue #1
user story 26) read `data/descriptions.json` as a **file** — never by re-running the crawler. One
entry per entity id:

```json
"<entity id>": {
  "text": "<verbatim about-page prose, or null on failure>",
  "source_url": "<the URL the text came from>",
  "fetched_at": "YYYY-MM-DD",
  "method": "link-scan | path-probe | null",
  "status": "ok | no_url | robots_disallowed | fetch_failed | no_about_found | extraction_empty",
  "truncated": true|false
}
```

`status: "ok"` carries real text; every other value is an explicit failure reason so **coverage
gaps are visible, not silent**. A consumer that wants only usable descriptions filters on
`status == "ok"`; the failure reasons tell a maintainer *why* an entity has none.

### Etiquette (the crawler is a guest on every site it reads)

- Identified User-Agent: `ny-gov-web-registry crawler; https://github.com/BetaNYC/ny-gov-web-registry; noel@beta.nyc`.
- **robots.txt honored** per host (stdlib `urllib.robotparser`); a Disallow is recorded
  `robots_disallowed` and the page is **never** fetched.
- ≥ 1.5 s between requests to the same host + modest global pacing; 20 s timeout; one retry with
  backoff on 5xx/timeout; **HTTP 429 → hard 60 s backoff** (mirrors the workspace's Internet Archive
  practice).
- **WAF circuit-breaker:** after 4 consecutive 403s a host is abandoned and its remaining targets
  are recorded `fetch_failed` (note `host_waf_blocked`). `nyc.gov` sits behind Akamai and 403s
  automation — we record and report that, never fight the WAF.

### WAF recovery via a human-context browser (2026-07-16, operator-authorized)

The 152 `www.nyc.gov` / `www1.nyc.gov` targets the Akamai WAF 403'd in phase 5 were recovered
through the app's **Browser pane**, a real browser context that passes the WAF where `urllib` does
not. This is the *sanctioned* path, not WAF evasion: no header-spoofing or `curl` tricks — a human
browser loading public pages, **explicitly authorized by the operator (Noel, 2026-07-15)**, read-only,
sequential, ~2s between navigations. Tooling (`plan_waf_candidates.py` → browser loop →
`assemble_waf_captures.py` → `extract_waf_descriptions.py` → `merge_descriptions.py`) is all offline
except the browser loop itself; extraction is verbatim-or-nothing (records `no_about_found` /
`extraction_empty` rather than guessing). The recovered gap is folded into `data/descriptions.json`
under the same schema and consumer contract; `method: "browser-pane"` distinguishes these records
from the phase-5 `link-scan` / `path-probe` ones. See `docs/freshness.md` for the run log and
batch coverage.

## Access gate
Live pulls from any of these wait for explicit operator authorization. The sync scripts read
from a local `data/cache/` and never fetch on their own. The about-crawler is the one component
that fetches, and it refuses to without `--operator-authorized` (configured ≠ authorized).
