---
title: Growing the registry statewide (plan, not yet approved)
created: 2026-11-06
type: plan
status: draft
tags: [registry, nys, counties, municipalities, plan]
---

# Growing the registry statewide — plan

- **Report generated:** 2026-11-06
- **Status:** plan only. **No entity is minted** until the operator approves. Source facts and
  counts come from [`sources-statewide.md`](sources-statewide.md) (verified 2026-11-06).

## Today

`data/registry.json`: 317 entities, almost all New York City (`government_level`: `nyc` 303,
`authority` 12, `bi-state` 1, `pbc` 1). The five borough presidents already sit at the county
level: each carries `areas[] {scheme: us_census_geoid, layer: county, id: 360xx, role:
jurisdiction}`. The five district attorneys (also county-level offices) carry no county area yet.

## What we add, in order

| Step | Tier | Spine (who says it exists) | How many | Website seed |
|---|---|---|---|---|
| 1 | State executive agencies | Governor's office *Agency Contacts* [`kjw8-pupa`](https://data.ny.gov/d/kjw8-pupa) (79 agencies), curated by hand against [`ny.gov/agencies`](https://www.ny.gov/agencies) | ~80–120 (UNVERIFIED until curated) | host of `agency_contact_url`; CISA .gov "State or territory" (18 NY domains) |
| 2 | Public authorities | ABO [`4vym-q77x`](https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x) via the existing `scripts/sync_abo.py` | 608 (45 State, 111 Local, 106 IDA, 346 LDC) | ABO `website` (496 of 608) |
| 3 | Counties | NYS Civil Boundaries GIS, Counties layer, cross-checked to OSC (57) | 57 new + 5 NYC links | CISA "County" (87 NY domains), then ITS `55k6-h6qq` |
| 4 | Cities | GIS Cities + OSC | 61 new (NYC already exists) | CISA, then ITS |
| 5 | Towns | GIS Towns + OSC | 933 | CISA ("Town of …", 726 org names), then ITS |
| 6 | Villages | GIS Villages + OSC | 532 (ITS also lists 18 dissolved; do not mint those as active) | CISA ("Village of …", 383), then ITS |

Step 2 is big and mostly local development corporations. **Decided 2026-11-06 (Noel):** mint the
**State** and **Local** authorities (156) in step 2. **Stretch goal:** the other 452 (106 IDAs,
346 LDCs), after the six steps above are done. They are real public bodies, but many have no
website and they would double the registry on their own.

**Out of scope for now** (note only): school districts (689), fire districts (904), other special
districts, Indian nations (10 territories in GIS; sovereign governments, not NYS local government,
so they need their own decision), courts, and the Legislature's member sites.

## NYC: five counties, no county governments

NYC's five boroughs are the five counties (Bronx, Kings, New York, Queens, Richmond). NYC has no
county governments; the borough presidents are the county-level leaders. So:

- **Do not mint** "Kings County" etc. as government entities. That would duplicate the BPs.
- **Link**: the five BP entities keep their county `jurisdiction` areas (already there).
- **Add** the same county `jurisdiction` area to the five district attorney entities.
- A statewide "list of counties" view = the 57 new county entities **plus** the 5 BP entities,
  joined on `us_census_geoid` county. No new record type needed.

## Ids

Follow the existing convention: lower-case slug of the official name, immutable, never reused.

- Counties: `albany-county`, `st-lawrence-county`.
- Cities: `city-of-albany`. Towns: `town-of-colonie`. Villages: `village-of-menands`.
- **Same name twice** (NY has several towns with the same name in different counties): append
  the county slug to **every** member of a colliding name, e.g. `town-of-greenville-greene-county`
  and `town-of-greenville-orange-county`. We mint each tier in one batch, so every collision is
  known at mint time and no id has to change later.
- Six places are both a town and a village (OSC notes this). They get **two** entities, one per
  municipal corporation, because each has its own government and often its own website.

## Schema changes (small, additive)

1. **`government_level`** gains values: `county`, `city`, `town`, `village`. The schema already
   calls this enum "configuration." It is additive, but any consumer that hard-codes today's five
   values will see new ones. **Open question:** which consumers read `government_level`? (The
   workspace reads `descriptions.json`; the MCP and others UNVERIFIED.)
2. **`provenance.sources`** gains `nys_gis`, `osc`, `its_localities`, `dotgov`, `nygov_contacts`.
3. **No new fields.** Everything else fits: ids, `identifiers[]`, `areas[]`, `web_properties[]`,
   `parent_id`.

## Identifiers (new schemes, documented in `schemes.md` before use)

| Scheme | Authority | Format | Verification rule |
|---|---|---|---|
| `nys_swis` | NYS ORPTS / ITS (SWIS code) | 6 digits | Present on the entity's own row in the GIS Civil Boundaries layer |
| `nys_osc_muni_code` | Office of the State Comptroller | 12 digits | OSC entity tables row matched by county **and** normalized name, single match only; ambiguous → review file, never applied |
| `us_gnis` | USGS GNIS | numeric | Present on the GIS row |
| `us_census_geoid` (area, already documented) | Census Bureau | county 5, county subdivision / place 10 | From the GIS row (`FIPS_CODE`); towns use county subdivision, cities and villages use place |
| `wikidata` | Wikidata | QID | **Existing guard rules apply unchanged** (see below) |

**Wikidata and municipalities.** The repo's place-vs-organization guard says a QID must be the
*organization*, not the place it sits in. For a NY city, town or village, the Wikidata item is the
*place*, and usually no separate "government of X" item exists. Proposal: do **not** loosen the
guard. Attach the municipality's place QID as an **area**, not an identifier:
`areas[] {scheme: wikidata, layer: municipality, id: Q…, role: jurisdiction}`, and only through a
GNIS match (Wikidata P590 = the GIS row's GNIS id), never a name match. Wikidata stays
cross-check and context only; it is never the only source for any fact.

## Parent links

`parent_id` stays an *org* hierarchy (MTA → LIRR). A town is not a child organization of its
county, so `parent_id` is **not** used for geography. Containment (village inside town inside
county) is carried by `areas[]` and the GIS/SWIS codes. This matches the community-board decision
in `district-web-presence.md`, which left `parent_id` unset until a membership/part-of relation
exists.

## Finding and verifying websites

The ITS website list is from 2023 and visibly stale. So a website becomes a `web_property` only by
these rules:

1. **Leads** come from: CISA .gov list (match on organization name + county/city), ITS `55k6`,
   ABO `website`, `kjw8-pupa` contact URL, Wikidata P856, NYCOM directory, and the portal list in
   `BetaNYC/grounding-ai-with-ny-open-data` (`resources/ny-open-data-portals.md`).
2. A lead is **auto-accepted** as `primary` only when **two independent leads agree on the host**
   (e.g. CISA + ITS). The `notes` field records which leads agreed and the date.
3. **Single-source leads** and disagreements go to a review file
   (`data/statewide_web_review.json`), never applied silently.
4. **Optional live check** (needs operator OK, it is a crawl): one `GET /` per lead host with the
   research UA; accept if the `<title>` or `<h1>` names the municipality. One request per host,
   same etiquette as the probe.
5. An ITS URL that now redirects to a different host becomes `legacy` with `valid_to` unknown,
   and the new host becomes the lead.
6. **Shared hosts**: many towns live as a page on their county's site, or on a vendor platform
   (e.g. eCode360 for laws, not sites). A web property is a host, so a path-only presence is
   recorded in `url_conventions[]`, not as a new domain.

## How this plugs into the existing pipeline

- New sync scripts follow the repo's rule: **they read `data/cache/`, they never fetch**. The
  operator places the GIS JSON, OSC spreadsheet, ITS CSV and CISA CSV in the cache.
  - `scripts/sync_nys_localities.py` → counties, cities, towns, villages (GIS spine + OSC + ITS)
  - `scripts/sync_dotgov.py` → CISA leads
  - `scripts/sync_abo.py` (exists) → authorities
  - `scripts/sync_nygov.py` (stub today) → state agencies from `kjw8-pupa` plus a hand-curated
    `data/curation.json` section
- `build_registry.py` merges them; `crawl_targets.py` derives the larger crawl set.
- Tests: fixture-based, like the existing `tests/test_sync_*.py`, plus a seed-validates check for
  the new enum values and id collisions.

## Size after growth

About **2,500 entities** (317 + ~100 state agencies + 156 authorities + 57 counties + 61 cities +
933 towns + 532 villages), roughly 8× today. Website coverage expected well under 100% for towns
and villages; the review file will be the largest artifact.
