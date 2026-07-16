# District-instance web presence

**Operator notes (Noel Hidalgo, 2026-07-15), recorded during phase 4.** The `areas[]` linkage treats boundary layers as geography operated by registry entities — but many *district instances* are themselves organizations with their own web presence. This matters twice: each such website belongs in the Wayback harvester's crawl set, and many of these instances are candidates for future entity records. This file is the per-layer research map for that future phase.

| Layer | Instance web presence | Entity-minting posture |
|---|---|---|
| `cd` Community districts | **Every community board represents a community district.** Each community board district office is **its own agency**, and the district manager is in charge of a website. Domains vary widely (nyc.gov subsites and independent domains). | **Yes — 59 board entities**, the strongest minting case. Blocked only on a source that asserts them individually (t3jq carries one generic "Community Boards" entity). |
| `pp` Police precincts | Precincts carry their own websites/pages and sometimes feature independent content. | Pages of NYPD rather than separate agencies; harvest-relevant, minting unlikely. |
| `cc` Council districts | Every council member has their own website, nested under the City Council site (council.nyc.gov/district-N). | Members/offices belong to electeds tooling; the *sites* belong in the crawl set. |
| `sa` / `ss` / `nycongress` | Assembly members, state senators, and members of Congress each have their own websites (nyassembly.gov, nysenate.gov, house.gov member sites). | Outside city-entity scope; crawl-set relevant if the harvester's remit includes representation of NYC. |
| `bid` Business improvement districts | **BIDs have their own websites** — they are real organizations (nonprofits contracted with SBS). | **Yes — strong minting case** (~70+ orgs); SBS's BID directory is the natural source. |
| `ibz` Industrial business zones | Might have their own websites — case-by-case. | Case-by-case; survey before deciding. |
| `hd` Historic districts | Might have their own websites (neighborhood associations etc.) — case-by-case. | Districts are designations, not orgs; associated organizations may merit records. |
| `nda` Neighborhood development areas | **No own websites.** NDAs have their own membership (Neighborhood Advisory Boards), but the content is legacy and nested under DYCD: https://www.nyc.gov/site/dycd/involved/boards-and-councils/nab-members.page (observed 2026-07-15; content appears very old). | No — but the DYCD NAB page is itself a preservation-worthy URL (aging content). |

**Design note:** when district-instance entities are minted (community boards, BIDs), each will carry its own `web_properties[]` and a `jurisdiction` area ref to its specific district (`{scheme: nyc-boundaries, layer: cd, id: "301"}` etc.) — the per-instance linkage phase 4 deliberately deferred. The `relations[]` vocabulary will need a membership/part-of type at that point (currently only predecessor/successor).
