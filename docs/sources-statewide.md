---
title: Statewide sources for a NY government website registry (source telemetry)
created: 2026-11-06
type: research
status: draft
tags: [research, nys, local-government, open-data, registry, source-telemetry]
---

# Statewide sources for a NY government website registry

- **Report generated:** 2026-11-06
- **Data current as of:** 2026-11-06 (all API calls, downloads and page fetches made 2026-11-06,
  roughly 09:55 to 10:30 ET, from the `office` machine)
- **Method:** light lookups only. Socrata catalog and metadata APIs, `count(*)` aggregates, one
  bulk pull of a 1,605-row dataset, ArcGIS REST attribute queries (no geometry), one OSC
  spreadsheet, one DOS PDF, one CISA CSV, three Wikidata SPARQL queries, and one request to
  `ny.gov/agencies`. No municipal homepage was fetched.

## Headline answer

**Partial.** New York State does publish a list of local government websites: the ITS dataset
[*New York State Locality Hierarchy with Websites* (`55k6-h6qq`)][its] covers all counties,
cities, towns and villages, with SWIS, GNIS and FIPS codes, but its rows were last updated
2023-03-06 and the website column is visibly stale (only 118 of 1,418 URLs are `.gov`, while CISA
now lists 726 NY "Town of" organizations on `.gov`). There is no state-published list of *state
agency* websites in machine-readable form: [`ny.gov/agencies`][nygov] is a client-side search page
with no exposed feed, and the closest dataset, the Governor's office [*Agency Contacts*
(`kjw8-pupa`)][contacts], gives contact-page URLs for 79 agencies. The authoritative *entity*
lists (not websites) are current and agree with each other: OSC's entity tables ("Counts as of
2026", saved 2026-11-02) and the NYS Civil Boundaries GIS service (published March 2026).

## Source table

| # | Source | Publisher | Covers | Rows / entities | Identifiers | Website field (% populated) | Last updated | Access | License |
|---|---|---|---|---|---|---|---|---|---|
| 1 | [Locality Hierarchy with Websites `55k6-h6qq`][its] | NYS ITS | counties, NYC boroughs, cities, towns, villages | 1,605 | `swis_code`, `gnis_id`, `state_fips`, `county_fips`, `county_code` | yes, 1,418 / 1,605 = 88.3% (see notes) | rows 2023-03-06 | Socrata API / CSV | none stated on dataset |
| 2 | [OSC Local Government Entities (entity-tables.xls)][oscxls] via [entity map][oscmap] | Office of the State Comptroller | every class of local government and district | 57 counties, 62 cities, 933 towns, 532 villages, plus 689 school districts, 904 fire districts, etc. | 12-digit OSC **Municipal Code** | no | "Counts as of 2026"; file saved 2026-11-02 | XLS download | none stated |
| 3 | [NYS Civil Boundaries FeatureServer][gis] | NYS ITS GIS Program Office | State, Counties, Cities, Towns, Villages, Indian Territories | 62 / 62 / 933 / 532 / 10 | `SWIS`, `GNIS_ID`, `FIPS_CODE`, `MUNITYCODE`, `DOS_LL` (local law ref), `AIA_CODE` (tribal) | no | publication March 2026; max village `DATEMOD` 2026-01-13 | ArcGIS REST (JSON), also shapefile on gis.ny.gov | UNVERIFIED (not checked on gis.ny.gov) |
| 4 | [Directory of Public Authorities `4vym-q77x`][abo] | Authorities Budget Office | State, Local, IDA, LDC authorities | 608 | none (no stable authority id) | yes, 496 / 608 = 81.6% | 2026-01-15 | Socrata API / CSV | none stated |
| 5 | [Agency Contacts `kjw8-pupa`][contacts] | Office of the Governor | executive contacts in state agencies | 623 rows, 79 distinct agencies | none (agency name only) | `agency_contact_url`, 504 / 623 rows; one contact-page URL per agency, not a homepage | 2025-04-11 (stated cadence quarterly, so overdue) | Socrata API / CSV | none stated |
| 6 | [County Seats `4xc7-bukh`][seats] | NYS ITS | county seats | 63 | county name only | `county_website`, 63 / 63 | 2014-01-16 | Socrata API | none stated |
| 7 | [CISA .gov domain data `current-full.csv`][dotgov] | CISA (.gov registry) | every registered .gov domain | 17,034 total; **1,451 NY** | none (domain is the key) | the domain itself | last commit 2026-11-06T13:56Z | CSV on GitHub | public domain (US federal work); UNVERIFIED that repo states a license |
| 8 | [`ny.gov/agencies`][nygov] | NY.gov (ITS) | state agencies | not counted | none | per-agency links | live | HTML + SearchStax client-side search | no stated license |
| 9 | [Wikidata][wdqs] | community | NY counties, cities, towns, villages | see counts table | QID, P590 GNIS, P882 FIPS 6-4 (counties only) | P856 | live | SPARQL | CC0 |
| 10 | [Census of Governments 2022][cog] | US Census Bureau | independent governments | 57 counties, 1,525 municipal+township, 1,189 special districts (secondary citation) | Census government ID (in Public Use File, not checked) | no | 2022 survey | PDF / public use file | public domain |
| 11 | [DOS Local Government Handbook, 2024 ed.][handbook] | NYS Department of State | narrative description of local government | not a list | none | no | posted 2025-01 | PDF | none stated |
| 12 | [NYCOM City and Village Directory][nycom] | NY Conference of Mayors (association) | member cities and villages | not counted | NYCOM `muni=` id (UNVERIFIED meaning) | yes, per entry | live | HTML per municipality | association terms |

Related, out of scope, noted only: ABO filtered views for [IDAs `2u8k-a2sw`][ida], [Local
Authorities `9zf7-z2m8`][localauth], [LDCs `nmqt-x5vm`][ldc]; [Fire Department Directory
`qfsu-zcpv`][fire] (DHSES, has `fdid_code`, no website); [County Emergency Management Offices
`jwkb-x5v6`][em]; [SUNY Campus Locations with Websites `3cij-nwhw`][suny]; [NYS AI Systems
Inventory `fgvt-qr4i`][ai] (agency names, no URLs); [Local Data Index `mxu7-dpdx`][ldi] (an index
of *datasets* by locality, 81,505 rows, not of governments).

## Per-source notes

### 1. ITS Locality Hierarchy with Websites (`55k6-h6qq`) — the only state "website registry"

- Description: "a hierarchal listing of New York State counties, cities, towns, and villages, as
  well as official locality websites." Disclaimer: "Websites are current as of last update. Where
  a website did not exist, the data element for the website is blank." ([metadata][itsmeta])
- Created 2014-03-04; rows last updated **2023-03-06**; posting frequency "As needed." Four
  filtered views share the same rows: [Counties `hnuz-89gw`][itsc], [Cities `vcy8-dghb`][itsci],
  [Towns `hnrk-kcnd`][itst], [Villages `aagd-6yb6`][itsv], plus [Cities, Towns, and Villages per
  County `y6cw-5z7p`][itsctv].
- Breakdown (verified by `$group` query):

  | type | rows | with website | with GNIS | with SWIS |
  |---|---|---|---|---|
  | Borough/County (NYC) | 5 | 0 | 5 | 5 |
  | County | 57 | 57 | 57 | 57 |
  | City | 62 | 62 | 62 | 62 |
  | Town | 932 | 830 (89.1%) | 932 | 932 |
  | Village | 549 | 469 (85.4%) | 549 | 549 |

- **Staleness, verified by SWIS diff against the March 2026 GIS layer:**
  - Missing the **Town of Palm Tree** (Orange, SWIS 336000), which exists in GIS and OSC. Palm Tree
    took effect in 2019 (date UNVERIFIED here), so the town list predates that.
  - Carries **18 villages absent from current GIS/OSC**, presumably dissolved (Cherry Creek,
    Forestville, Van Etten, Keeseville, Port Henry, Herrings, Harrisville, Fort Johnson,
    Bridgewater, Barneveld, Prospect, South Nyack, Hermon, Morristown, Mastic Beach, Salem,
    Lyons, Macedon), plus Kiryas Joel under its old SWIS 334005 (now 336001).
  - Missing the new **Village of Ateres** (Sullivan, SWIS 484689).
  - Website hosts: 1,418 URLs, only 118 on `.gov` and only 13 on `https`. 16 rows point at
    `alleganyco.com` (village rows pointing at the county site). 1,245 distinct hosts.
- NYC: five `Borough/County` rows (SWIS 600000 to 640000) with no website, plus one `City` row
  "New York City" (SWIS 650000, `http://www.nyc.gov`).
- License: none set on the dataset. data.ny.gov's general terms apply (not re-read for this
  report, UNVERIFIED).

### 2. OSC entity tables

- [Entity map page][oscmap] links a [summary PDF][oscpdf] and [entity-tables.xls][oscxls]. The XLS
  title row reads "Counts as of 2026"; HTTP `Last-Modified: Mon, 02 Nov 2026`.
- One sheet per class, each row a 12-digit **Municipal Code** plus name, county, economic region.
  The summary sheet's footnotes matter:
  - Counties: 57, "Does not include the five boroughs of NYC."
  - Cities: 62, "Includes NYC."
  - Towns: 933, "Includes six coterminous town-villages."
  - Villages: 532, "Includes six coterminous town-villages."
  - So 933 + 532 double-counts six entities that function as one government. Unique
    general-purpose governments = 57 + 62 + 933 + 532 − 6 = **1,578** (my arithmetic, from OSC's
    footnotes; which six is not listed in the summary).
- Name-diff against GIS villages: all 532 match except spelling (De Ruyter/DeRuyter, Le Roy/LeRoy,
  "Village of The Branch"). Palm Tree and Ateres are present. **OSC and GIS agree.**
- Also counts special-purpose units (for scoping later): 689 school districts, 904 fire districts,
  7,913 town special districts, 131 county special districts, 70 independent special districts,
  58 soil and water districts, 33 consolidated health districts, 132 BIDs, 144 housing
  authorities, 106 IDAs, 5 OTBs, 80 other local authorities, 41 urban renewal agencies, 37 BOCES,
  359 charter schools, 36 community colleges, 23 library systems, 415 public libraries.
- No website field. Open Book New York and the [financial data pages][oscfin] use the same
  municipal code, which makes it the natural join key for fiscal data.

### 3. NYS Civil Boundaries (GIS)

- [Service][gis] description: "Publication Date: March 2026. Updated as needed." Layers: State,
  State_Shoreline, Counties (62), Counties_Shoreline, Cities (62), Towns (933), Cities_Towns (995),
  Villages (532), Indian_Territories (10).
- Fields (attribute queries, no geometry):
  - Counties: `NAME, ABBREV, GNIS_ID, FIPS_CODE, SWIS, NYSP_ZONE, POP1990..2020, DOS_LL,
    DOSLL_DATE, NYC, CALC_SQ_MI, DATEMOD` (the `NYC` flag separates the five boroughs).
  - Cities/Towns: adds `MUNI_TYPE, MUNITYCODE, COUNTY`, 10-digit place/county-subdivision
    `FIPS_CODE`.
  - Villages: adds `TOWN`, 15-digit `FIPS_CODE`.
  - Indian Territories: `AIA_CODE` (Census American Indian Area code).
- Towns and villages: SWIS and GNIS are each unique per row (933/933, 532/532), and none is null.
- **No website or URL field.** `DOS_LL` and `DOSLL_DATE` cite the local law filed with DOS that
  last changed a boundary. That is useful provenance for dissolutions and incorporations.
- Ten Indian Territories: Allegany, Cattaraugus, Oil Springs, Oneida Nation, Onondaga Nation,
  Poospatuck, Saint Regis Mohawk, Shinnecock, Tonawanda, Tuscarora Nation. These are territories,
  not a list of nations or governments. Note only.

### 4. ABO Directory of Public Authorities (`4vym-q77x`)

- 608 rows, last updated 2026-01-15 ([metadata][abometa]).

  | type | rows | with website |
  |---|---|---|
  | State | 45 | 44 (97.8%) |
  | Local | 111 | 89 (80.2%) |
  | IDA | 106 | 90 (84.9%) |
  | LDC | 346 | 273 (78.9%) |
  | **Total** | **608** | **496 (81.6%)** |

- Still no stable id column. The website is self-reported via PARIS (per the existing
  [`sources.md`](sources.md)). OSC's spreadsheet lists IDAs, housing authorities and other local
  authorities *with* municipal codes, which is a possible id crosswalk (UNVERIFIED match rate).

### 5. Governor's Agency Contacts (`kjw8-pupa`)

- "List of executive contacts within state agencies." 623 rows, 79 distinct agencies, stated
  cadence quarterly, last update 2025-04-11 ([metadata][contactsmeta]).
- One `agency_contact_url` per agency, pointing to a contact page (e.g.
  `https://dps.ny.gov/contact-us`). Strip the path to get a host. 57 distinct hosts across the
  agency/URL pairs. It is the best machine-readable seed for state *agency* hosts, but it is
  staff-level personal data (names, titles, phones). Use the agency and host columns only.

### 6. ITS County Seats (`4xc7-bukh`)

- 63 rows, `county_website` 100% populated, but last updated 2014-01-16. Superseded by #1 and #7.

### 7. CISA .gov domain data

- [`current-full.csv`][dotgovcsv]: columns `Domain name, Domain type, Organization name,
  Suborganization name, City, State, Security contact email`. Repo last commit to that file
  2026-11-06T13:56Z, so it updates daily.
- NY rows (`State = NY`): **1,451**.

  | Domain type | NY domains |
  |---|---|
  | City (CISA uses this for all municipalities: cities, towns, villages) | 1,240 |
  | Special district | 97 |
  | County | 87 |
  | State or territory | 18 |
  | Tribal | 5 |
  | Interstate | 2 (panynj.gov, pathtrain.gov) |
  | Federal - Executive | 1 |
  | School district | 1 |

- Inside "City": 1,213 distinct organization names; by name prefix 726 "Town of", 383 "Village
  of", 66 "City of", and 38 other (police and fire departments, a museum, NYC agencies such as OTI
  and NYCERS). That is a **name-prefix heuristic, not a match to SWIS**.
- Inside "County": 84 distinct orgs, covering about 56 of 57 counties by name heuristic (Madison
  not seen). A few rows are mis-typed (four towns and villages filed under "County").
- "State or territory" has 18 domains, but the state's agencies mostly live as *subdomains* of
  `ny.gov` (e.g. `dps.ny.gov`), which this file does not list. **It cannot inventory state
  agencies.**
- Caveats: `.gov` only, so a municipality on `.org`, `.com`, `.us` or `*.ny.us` is absent. A
  domain registration is not proof the site is live or primary, and one government can hold many
  domains (sheriff, DA, emergency sites).

### 8. `ny.gov/agencies`

- One GET, 200 OK, 72 KB HTML. The agency list is rendered client-side by a SearchStax (hosted
  Solr) widget: Drupal settings contain only `nygov_searchstax.templates`, and the static HTML has
  23 `.ny.gov` links. No JSON feed, API key or endpoint is exposed in the page settings. A
  search endpoint probably exists in a loaded JS file. **UNVERIFIED, not probed** (outside the
  one-to-two-request budget). No ITS-published inventory of `ny.gov` subdomains was found.

### 9. Wikidata (cross-check only)

Queried 2026-11-06 with User-Agent `BetaNYC-registry-research/1.0`.

| Class (QID) | items | P856 website | P590 GNIS | P882 FIPS | P576 dissolved |
|---|---|---|---|---|---|
| county of New York ([Q13414757][q-county]) | 64 | 58 | 62 | 62 | 2 |
| city in the state of New York ([Q15063611][q-city]) | 61 | 61 | 61 | 0 | 0 |
| town of New York ([Q106071004][q-town]) | 931 | 260 | 928 | 0 | 2 |
| village of New York ([Q55237813][q-village]) | 527 | 186 | 526 | 0 | 0 |
| borough of New York City ([Q408804][q-borough]) | 5 | 5 | 5 | 0 | 0 |

- P882 is FIPS 6-4 (county) only, so 0 for other classes is expected, not a gap.
- 62 counties are not dissolved (the 62 includes NYC's five). The city class shows 61, likely
  because NYC itself (Q60) is typed differently (UNVERIFIED).
- Website coverage is weak below county level (28% of towns, 35% of villages). Useful for QIDs
  and GNIS cross-checks, not as a website source.

### 10. Census of Governments 2022

- Counts for NY, via secondary sources citing Census: 57 counties, 1,525 "municipal and township"
  governments (595 municipalities + 930 townships), 1,189 special districts, 676 school districts
  ([Ballotpedia][ballot]; [KU table][ku]). **UNVERIFIED against a Census primary table.** The
  [2022 & 2025 Government Organization Summary Report][cog] (May 2026) gives no NY row. The
  [Public Use File][gus] lists every unit with a Census government ID, but it was not checked.
- Census counts towns as "townships" and treats the six coterminous town-villages differently,
  which explains why 930 + 595 is smaller than OSC's 933 + 62 + 532 (interpretation, not verified).

### 11. DOS Local Government Handbook (2024 ed.)

- [PDF][handbook] posted January 2025. **Internally inconsistent on villages:** the cities chapter says "the
  state's 544 villages"; the village chapter says "existing 550 villages" and "There were 535
  villages in New York State in 2018"; the assessment chapter says "550 villages." It is
  consistent on 57 counties outside NYC, 62 cities and 933 towns. **Do not use the Handbook for
  counts.** Cite it for structure (NYC boroughs as counties, coterminous town-villages, reservations
  outside towns and cities).

### 12. Associations (NYSAC, NYCOM, Association of Towns)

- NYCOM's [City and Village Directory][nycom] has per-municipality pages with a website field
  (example: City of Auburn, `www.auburnny.gov`). HTML only, one page per municipality, keyed by a
  `muni=` number whose scheme is UNVERIFIED. Scraping it would be a crawl, so it was not done.
- NYSAC and Association of Towns directories: **not checked** (one search did not surface them).
- NYS Board of Elections county boards page returned **403** to WebFetch. Not checked. The
  operator's browser can read it.

## Verified counts

| Entity type | OSC entity tables | NYS Civil Boundaries GIS | ITS `55k6-h6qq` | DOS Handbook | Wikidata | Census 2022 |
|---|---|---|---|---|---|---|
| Counties outside NYC | 57 | 57 (62 with `NYC` flag) | 57 + 5 boroughs | 57 | 62 incl. boroughs | 57 |
| Cities (incl. NYC) | 62 | 62 | 62 | 62 | 61 | — |
| Towns | 933 | 933 | 932 (no Palm Tree) | 933 | 931 (2 dissolved) | 930 "townships" (UNVERIFIED) |
| Villages | 532 | 532 | 549 (stale) | 535 / 544 / 550 | 527 | — |
| Cities + villages | 594 | 594 | — | — | — | 595 "municipalities" (UNVERIFIED) |
| Indian territories | — | 10 | — | — | — | — |
| School districts (out of scope) | 689 | — | — | — | — | 676 (UNVERIFIED) |
| Fire districts (out of scope) | 904 | — | — | — | — | — |
| As of | 2026 (file 2026-11-02) | March 2026 publication | rows 2023-03-06 | 2024 ed. (posted 2025-01) | 2026-11-06 | 2022 |

ABO's own total: **608** (45 State, 111 Local, 106 IDA, 346 LDC), as of 2026-01-15.

**NYC special case.** The five boroughs are coextensive with Bronx, Kings, New York, Queens and
Richmond counties, and NYC has no county governments. OSC counts 57 counties because it excludes
them ([OSC summary][oscxls]). GIS carries 62 county polygons and marks five with `NYC = Y`. ITS
types them `Borough/County` with no website. Borough presidents are the borough-level elected
officials (OSC and DOS do not list them as county governments). For this registry, the five
borough-president entities already stand at the county level: each carries a `jurisdiction` area
under `us_census_geoid` / `county` (see [`schemes.md`](schemes.md)). The growth plan links to them
rather than minting five new county records; see [`registry-growth-plan.md`](registry-growth-plan.md).

## Identifier crosswalk options

| Identifier | Format | In | Joins |
|---|---|---|---|
| **SWIS code** | 6 digits (county 2 + muni 4) | ITS `55k6`, GIS (all layers but Indian Territories) | ITS ↔ GIS directly; also Tax and Finance real-property datasets (e.g. [`iq85-sdzs`][rpt]), UNVERIFIED column name |
| **GNIS ID** | numeric | ITS, GIS, Wikidata P590 | ITS ↔ GIS ↔ Wikidata |
| **FIPS** | county 5 digits; place/county-subdivision 10; village 15 (GIS) | GIS, ITS (`county_fips` only), Wikidata P882 (counties) | Census datasets, GIS |
| **OSC Municipal Code** | 12 digits | OSC entity tables, Open Book, OSC financial data | **no direct join** to SWIS/GNIS. Join by county + normalized name (De Ruyter / DeRuyter, Le Roy / LeRoy differ) |
| **Wikidata QID** | Q-number | Wikidata | everything else through GNIS |
| **.gov domain** | hostname | CISA `current-full.csv` | websites only. Join by normalized org name + county/city, heuristic |
| **AIA code** | 4 digits | GIS Indian Territories | Census tribal geographies |
| **ABO** | none | — | name only. OSC IDA/authority sheets carry municipal codes, a possible bridge |

Recommended keys: **SWIS** primary for counties, cities, towns and villages (GIS has it, unique,
current), **GNIS** secondary (the bridge to Wikidata), **OSC municipal code** stored as an
additional verified identifier after a name match.

## Gaps

1. **No current state-published list of local government websites.** The ITS dataset is the only
   one, and it is about three and a half years stale with no `.gov` migrations reflected.
2. **No machine-readable list of state agency websites.** Agency Contacts gives contact pages for
   79 agencies (overdue on its quarterly cadence). `ny.gov/agencies` exposes no feed.
3. **No ITS inventory of `ny.gov` subdomains** found. CISA lists only the apex `ny.gov`.
4. **ABO has no stable id**, and about 18% of authorities have no website.
5. **OSC municipal codes don't join to SWIS** without name matching.
6. **License is unstated** on every data.ny.gov dataset checked (`55k6`, `4vym`, `kjw8`, `4xc7`)
   and on the OSC spreadsheet.
7. Not checked: Board of Elections county boards (403), NYSAC, Association of Towns, the Census
   Public Use File, GIS download licensing.

## Recommended spine per tier

| Tier | Entity spine | Website seed | Verify against |
|---|---|---|---|
| **State agencies** | Agency Contacts `kjw8-pupa` agency names, curated by hand (the existing approach in [`sources.md`](sources.md)) | host from `agency_contact_url` | `ny.gov/agencies` (manual), CISA "State or territory" for non-`ny.gov` apexes |
| **Public authorities** | ABO `4vym-q77x` (608) | ABO `website` (81.6%) | OSC authority sheets for municipal codes, CISA "Special district"/"Interstate" |
| **Counties** | GIS Counties layer (SWIS, GNIS, FIPS, `NYC` flag), cross-checked to OSC (57) | CISA "County" first, then ITS `55k6` (100% populated, stale) | Wikidata P856 (58 of 64) |
| **NYC boroughs** | GIS Counties with `NYC = Y` (5), as a borough entity type | none from state sources; borough president sites curated | Wikidata Q408804 (5 of 5 with P856) |
| **Cities** | GIS Cities (62) + OSC municipal code | CISA "City" matched to "City of …", then ITS | Wikidata (61 of 61) |
| **Towns** | GIS Towns (933) + OSC municipal code | CISA "Town of …" (726 org names), then ITS (830) | Wikidata (260 of 931) |
| **Villages** | GIS Villages (532) + OSC municipal code. Drop ITS's 18 dissolved villages | CISA "Village of …" (383), then ITS (469, includes dissolved) | Wikidata (186 of 527) |

For every tier, treat any website from ITS `55k6` as an unverified lead that must be confirmed
live, since it predates 2023.

<!-- links -->
[its]: https://data.ny.gov/Government-Finance/New-York-State-Locality-Hierarchy-with-Websites/55k6-h6qq
[itsmeta]: https://data.ny.gov/api/views/55k6-h6qq.json
[itsc]: https://data.ny.gov/d/hnuz-89gw
[itsci]: https://data.ny.gov/d/vcy8-dghb
[itst]: https://data.ny.gov/d/hnrk-kcnd
[itsv]: https://data.ny.gov/d/aagd-6yb6
[itsctv]: https://data.ny.gov/d/y6cw-5z7p
[oscmap]: https://web.osc.state.ny.us/localgov/web-entity-map/
[oscxls]: https://web.osc.state.ny.us/localgov/web-entity-map/excel/entity-tables.xls
[oscpdf]: https://web.osc.state.ny.us/localgov/web-entity-map/pdf/entity-table-summary.pdf
[oscfin]: https://wwe1.osc.state.ny.us/localgov/findata/financial-data-for-local-governments.cfm
[gis]: https://gisservices.its.ny.gov/arcgis/rest/services/NYS_Civil_Boundaries/FeatureServer
[abo]: https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x
[abometa]: https://data.ny.gov/api/views/4vym-q77x.json
[contacts]: https://data.ny.gov/d/kjw8-pupa
[contactsmeta]: https://data.ny.gov/api/views/kjw8-pupa.json
[seats]: https://data.ny.gov/d/4xc7-bukh
[dotgov]: https://github.com/cisagov/dotgov-data
[dotgovcsv]: https://raw.githubusercontent.com/cisagov/dotgov-data/main/current-full.csv
[nygov]: https://www.ny.gov/agencies
[wdqs]: https://query.wikidata.org/
[q-county]: https://www.wikidata.org/wiki/Q13414757
[q-city]: https://www.wikidata.org/wiki/Q15063611
[q-town]: https://www.wikidata.org/wiki/Q106071004
[q-village]: https://www.wikidata.org/wiki/Q55237813
[q-borough]: https://www.wikidata.org/wiki/Q408804
[cog]: https://www.census.gov/library/publications/2026/econ/govtorg2225.html
[gus]: https://www.census.gov/programs-surveys/gus/data/publicusefiles.html
[ballot]: https://ballotpedia.org/New_York_counties
[ku]: https://ksdata.ku.edu/ksdata/ksah/govt/11state1.pdf
[handbook]: https://dos.ny.gov/system/files/documents/2025/01/localgovernmenthandbook_2024.pdf
[nycom]: https://nycom.org/about/city-village-directory/municipality?muni=14304
[ida]: https://data.ny.gov/d/2u8k-a2sw
[localauth]: https://data.ny.gov/d/9zf7-z2m8
[ldc]: https://data.ny.gov/d/nmqt-x5vm
[fire]: https://data.ny.gov/d/qfsu-zcpv
[em]: https://data.ny.gov/d/jwkb-x5v6
[suny]: https://data.ny.gov/d/3cij-nwhw
[ai]: https://data.ny.gov/d/fgvt-qr4i
[ldi]: https://data.ny.gov/d/mxu7-dpdx
[rpt]: https://data.ny.gov/d/iq85-sdzs
