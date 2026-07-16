# Government-website anomalies

Defects and oddities observed on live government web properties during registry crawls — content problems on the *sites themselves*, distinct from crawl failures (see `description-review-queue.md`) and upstream data gaps (the `upstream-gap` issue label). Requested by the operator 2026-07-15.

**Purpose:** these are things the responsible agency (or MOA/OTI) would likely want to know about. Candidates for polite civic feedback; also preservation-relevant signals for the Wayback harvester (aging or placeholder content tends to disappear without notice).

**Rules:** one row per anomaly; verbatim evidence, dated observation; never speculate about cause; mark resolved with a date when a re-crawl or human check shows it fixed. Crawl agents: append rows here whenever a run surfaces one.

| Date observed | Entity | URL | Anomaly | Status |
|---|---|---|---|---|
| 2026-07-15 | Mayor's Office for Childcare and Early Childhood Education | https://www.nyc.gov/site/childcare/about/about.page | Live about page contains **unpublished Lorem ipsum placeholder text** instead of real copy. Excluded from descriptions.json by human judgment (verbatim-or-nothing would otherwise have stored filler as the agency's self-description). | Open |
| 2026-07-15 | Department of Youth and Community Development (Neighborhood Advisory Boards) | https://www.nyc.gov/site/dycd/involved/boards-and-councils/nab-members.page | NAB membership content appears **very old / unmaintained** on a live page (operator observation; see also docs/district-web-presence.md, `nda` row). Preservation-worthy before it vanishes. | Open |
| 2026-07-15 | New York City Department of Transportation | https://www.nyc.gov/html/dot/html/about/about.shtml | Agency still serves its site on the **legacy pre-CMS structure** (`/html/<slug>/…​.shtml`) — not a defect per se, but a migration-risk signal: this generation of pages tends to disappear wholesale when finally migrated. Tracked for the Wayback harvester (`descriptions_recovery.json._legacy_path_entities`). | Open |
| 2026-07-15 | Mayor's Office of Strategic Partnerships | https://www.nyc.gov/site/partnerships/about/about.page | Live about page contains **unpublished Lorem ipsum placeholder text** instead of real copy (second such page found, after Childcare). Excluded from descriptions.json by human judgment. | Open |
| 2026-07-15 | New York City Districting Commission | https://www.nyc.gov/site/districting/about/about.page | About page shows only a **stale 2013 news item** (the DOJ pre-clearance of the 2013 Council district plan, written in present/future tense) — no current description of the Commission. Abandoned content. | Open |
| 2026-07-15 | Board of Standards and Appeals | https://www.nyc.gov/site/bsa/about/about.page | About page **renders no body content** (only the section nav) via a normal browser load — the descriptive block appears to be missing or fails to render. No about text retrievable. | Open |
| 2026-07-15 | New York City Police Department | https://www.nyc.gov/site/nypd/about/about.page | About page **renders no body content** (only the section nav: About NYPD / Leadership / Police Academy / Memorials) via a normal browser load. No about text retrievable for a major agency. | Open |
