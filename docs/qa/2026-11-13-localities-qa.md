---
title: QA of the minted New York counties, cities, towns and villages
created: 2026-11-13
type: qa-report
status: active
tags: [registry, qa, nys, localities]
---

# QA: New York counties, cities, towns and villages (issue #25)

- **Report generated:** 2026-11-13
- **Data current as of:** OSC entity tables downloaded 2026-11-13 (file dated 2026-11-02); ITS `55k6-h6qq` snapshot of 2026-11-06 (rows last updated 2023-03-06); data.ny.gov `sxhg-qquj` read 2026-11-14T00:34Z UTC (2026-11-13, 7:34 pm ET; rows last updated 2022-01-03)
- **Built by:** `scripts/qa_localities.py` from committed files only. Machine-readable: `data/statewide_localities_qa.json`. Every mismatch below is also in `data/statewide_web_review.json` (§ `localities` for ITS, § `localities_census_qa` for the Census list).

## Minted

| Level | Entities |
|---|---|
| county | 57 |
| city | 61 |
| town | 933 |
| village | 532 |
| **total** | **1583** |

Spine: the OSC entity tables (57 counties outside NYC, 62 cities including NYC, 933 towns, 532 villages). NYC's five counties and the City of New York are not minted. The six coterminous town-villages OSC mentions are two entities each, one per government, so they need no special case.

## OSC (the spine)

- Entities whose OSC code, level and name agree with OSC: 1583 of 1583.
- OSC rows outside NYC not minted: 0. Codes minted twice: 0.

## ITS `55k6-h6qq` (codes)

An entity matches when its SWIS is an ITS row of the same type with the same name and county.

| Level | Matched |
|---|---|
| county | 57 / 57 (100.0%) |
| city | 61 / 61 (100.0%) |
| town | 931 / 933 (99.8%) |
| village | 529 / 532 (99.4%) |

**Entities with no ITS match (5):**

- `town-of-fallsburg` (Town of Fallsburg, Sullivan)
- `town-of-palm-tree` (Town of Palm Tree, Orange)
- `village-of-ateres` (Village of Ateres, Sullivan)
- `village-of-bloomingburg` (Village of Bloomingburg, Sullivan)
- `village-of-kiryas-joel` (Village of Kiryas Joel, Orange)

**ITS rows no entity matches (21):**

- village Cherry Creek (Chautauqua), SWIS 063001
- village Forestville (Chautauqua), SWIS 064601
- village Van Etten (Chemung), SWIS 073801
- village Keeseville (Clinton), SWIS 092201
- village Port Henry (Essex), SWIS 153601
- village Herrings (Jefferson), SWIS 226005
- village Harrisville (Lewis), SWIS 232401
- village Fort Johnson (Montgomery), SWIS 272001
- village Bridgewater (Oneida), SWIS 302801
- village Barneveld (Oneida), SWIS 305805
- village Prospect (Oneida), SWIS 305803
- village Kiryas Joel (Orange), SWIS 334005
- village South Nyack (Rockland), SWIS 392407
- village Hermon (St Lawrence), SWIS 404401
- village Morristown (St Lawrence), SWIS 406001
- village Mastic Beach (Suffolk), SWIS 472219
- town Fallsburgh (Sullivan), SWIS 482800
- village Bloomingburgh (Sullivan), SWIS 484001
- village Salem (Washington), SWIS 534801
- village Lyons (Wayne), SWIS 542801
- village Macedon (Wayne), SWIS 543001

## Census population list, data.ny.gov `sxhg-qquj` (names)

No county column and no codes, so the match is level + name, counting duplicates. Excluded as NYC: city: New York City, county: Bronx County, county: Kings County, county: New York County, county: Queens County, county: Richmond County.

| Level | Entities matched | Census rows |
|---|---|---|
| county | 57 / 57 (100.0%) | 57 |
| city | 60 / 61 (98.4%) | 60 |
| town | 930 / 933 (99.7%) | 930 |
| village | 531 / 532 (99.8%) | 535 |

**Entities with no Census match (5):**

- `city-of-sherrill` (City of Sherrill)
- `town-of-east-rochester` (Town of East Rochester)
- `town-of-mount-kisco` (Town of Mount Kisco)
- `town-of-scarsdale` (Town of Scarsdale)
- `village-of-ateres` (Village of Ateres)

**Census rows no entity matches (4):**

- village: Fort Johnson village
- village: Harrisville village
- village: Morristown village
- village: South Nyack village

## Reading the mismatches (this run)

- **ITS, 18 villages that OSC no longer lists** (Cherry Creek through Macedon above): `docs/sources-statewide.md` § 1 found the same 18 missing from the March 2026 GIS layer and OSC, presumably dissolved. They are not minted.
- **ITS, two spellings:** OSC writes Fallsburg and Bloomingburg, ITS writes Fallsburgh and Bloomingburgh. The match does not bridge an added letter, so both stay unmatched and get no SWIS or GNIS from ITS; a person or the GIS read should settle them.
- **ITS, newer than the 2023 list:** the Town of Palm Tree and the Village of Ateres.
- **ITS, Kiryas Joel:** matched by name, but its ITS SWIS (334005) is the old code; GIS gives 336001 (`sources-statewide.md` § 1). Held in `data/curation.json` § `locality_its_swis_stale`, so no SWIS is attached and the ITS row counts as unmatched.
- **Census, three coterminous town-villages:** East Rochester, Mount Kisco and Scarsdale appear in `sxhg-qquj` only as villages. Harrison and Green Island appear as both. The registry keeps a town and a village for all five (one entity per government, per the growth plan).
- **Census, Sherrill:** the City of Sherrill is not in `sxhg-qquj` under any type.
- **Census, Ateres:** not in the list under any type (ITS lacks it too).
- **Census, four villages OSC no longer lists:** Fort Johnson, Harrisville, Morristown and South Nyack, four of the 18 ITS villages above.
- **ITS GNIS shared by a city and its town:** ITS gives 24 towns the same GNIS id as the city of the same name (Amsterdam, Batavia, ...). A code that names two places names neither, so those 48 places get no `us_gnis` (review reason `its_code_shared_by_places`). They keep their SWIS.

## Websites

- Primary site auto-accepted (two independent leads agree): 83 of 1583.
- Places without a primary: 1500; of those, 1333 have at least one lead host in `data/ai-readability/localities-website-leads-2026-11-13.csv` (2261 rows, one per host and source) and the rest have no lead at all.
- No municipal site was fetched. The AI-readability scan checks the hosts in the CSV.

## Review file, § localities

| Reason | Items |
|---|---|
| `dotgov_lead_matches_no_place` | 50 |
| `its_code_shared_by_places` | 48 |
| `its_no_match` | 4 |
| `its_row_unmatched` | 20 |
| `its_swis_known_stale` | 1 |
| `path_only_presence` | 212 |
| `website_additional_leads_not_applied` | 24 |
| `website_host_shared` | 1 |
| `website_leads_disagree` | 892 |
| `website_single_lead` | 440 |
