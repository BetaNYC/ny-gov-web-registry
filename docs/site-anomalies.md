# Government-website anomalies

Defects and oddities observed on live government web properties during registry crawls — content problems on the *sites themselves*, distinct from crawl failures (see `description-review-queue.md`) and upstream data gaps (the `upstream-gap` issue label). Requested by the operator 2026-07-15.

**Purpose:** these are things the responsible agency (or MOA/OTI) would likely want to know about. Candidates for polite civic feedback; also preservation-relevant signals for the Wayback harvester (aging or placeholder content tends to disappear without notice).

**Rules:** one row per anomaly; verbatim evidence, dated observation; never speculate about cause; mark resolved with a date when a re-crawl or human check shows it fixed. Crawl agents: append rows here whenever a run surfaces one.

| Date observed | Entity | URL | Anomaly | Status |
|---|---|---|---|---|
| 2026-07-15 | Mayor's Office for Childcare and Early Childhood Education | https://www.nyc.gov/site/childcare/about/about.page | Live about page contains **unpublished Lorem ipsum placeholder text** instead of real copy. Excluded from descriptions.json by human judgment (verbatim-or-nothing would otherwise have stored filler as the agency's self-description). | Open |
| 2026-07-15 | Department of Youth and Community Development (Neighborhood Advisory Boards) | https://www.nyc.gov/site/dycd/involved/boards-and-councils/nab-members.page | NAB membership content appears **very old / unmaintained** on a live page (operator observation; see also docs/district-web-presence.md, `nda` row). Preservation-worthy before it vanishes. | Open |
| 2026-07-15 | New York City Department of Transportation | https://www.nyc.gov/html/dot/html/about/about.shtml | Agency still serves its site on the **legacy pre-CMS structure** (`/html/<slug>/…​.shtml`) — not a defect per se, but a migration-risk signal: this generation of pages tends to disappear wholesale when finally migrated. Tracked for the Wayback harvester (`descriptions_recovery.json._legacy_path_entities`). | Open |
