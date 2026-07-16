# Description review queue — human in the loop

Entities where the about-crawler (phase 5 + WAF recovery) could not confidently extract a self-description and a human eye is needed. **Update this table whenever descriptions.json changes** (recovery batches must update it in their PRs). Requested by the operator 2026-07-15.

How to resolve a row: find the about/mission page URL by hand, then either give it to the maintainers (an operator-supplied URL gets crawled verbatim next pass) or edit the Resolution column and open a PR.

Not listed here: `fetch_failed` rows (WAF targets queued for recovery batches — transient) and `no_url` entities (no website in ANY source; an upstream-data gap, tracked separately).

| Entity | id | URL tried | Status | Needed / Resolution |
|---|---|---|---|---|
| Educational Construction Fund | `educational-construction-fund` | https://infohub.nyced.org/reports/financial/educational-construction-fund | `extraction_empty` | Human: check if page is JS-rendered or oddly structured; supply direct URL to the prose |
| Manhattan District Attorney's Office | `manhattan-district-attorney-s-office` | https://manhattanda.org/ | `extraction_empty` | Human: check if page is JS-rendered or oddly structured; supply direct URL to the prose |
| New York Hall of Science | `new-york-hall-of-science` | https://nysci.org/ | `extraction_empty` | Human: check if page is JS-rendered or oddly structured; supply direct URL to the prose |
| New York Public Library | `new-york-public-library` | https://www.nypl.org/ | `extraction_empty` | Human: check if page is JS-rendered or oddly structured; supply direct URL to the prose |
| Panel on Educational Policy | `panel-on-educational-policy` | https://www.schools.nyc.gov/about-us/leadership/panel-for-education-policy | `extraction_empty` | Human: check if page is JS-rendered or oddly structured; supply direct URL to the prose |
| Port Authority of New York and New Jersey | `panynj` | https://panynj.gov/ | `extraction_empty` | Human: check if page is JS-rendered or oddly structured; supply direct URL to the prose |
| School Construction Authority | `school-construction-authority` | https://www.nycsca.org/ | `extraction_empty` | Human: check if page is JS-rendered or oddly structured; supply direct URL to the prose |
| Audit Committee | `audit-committee` | https://nycauditcommittee.org/ | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Bronx District Attorney's Office | `bronx-district-attorney-s-office` | https://www.bronxda.nyc.gov/ | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Brooklyn District Attorney's Office | `brooklyn-district-attorney-s-office` | http://www.brooklynda.org/ | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| City Planning Commission | `city-planning-commission` | https://www.nyc.gov/content/planning/pages/commission | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Fire Department of the City of New York | `fire-department-of-the-city-of-new-york` | https://www.nyc.gov/site/fdny/index.page | `no_about_found` | RESOLVED — https://www.nyc.gov/site/fdny/about/overview/overview.page (Noel, 2026-07-15) — re-crawl in flight |
| Housing Development Corporation | `housing-development-corporation` | https://www.nychdc.com/ | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| MWBE Advisory Board | `mwbe-advisory-board` | https://www.nyc.gov/assets/mwbe/?page=mwbe-advisory-council | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Museum of Modern Art | `museum-of-modern-art` | https://www.moma.org/ | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| New York City Department of Transportation | `new-york-city-department-of-transportation` | https://www.nyc.gov/html/dot/html/home/home.shtml | `no_about_found` | RESOLVED — https://www.nyc.gov/html/dot/html/about/about.shtml (Noel, 2026-07-15) — re-crawl in flight |
| New York City Tourism + Conventions | `new-york-city-tourism-conventions` | https://www.nyctourism.com/ | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Office of the Borough President of Staten Island | `office-of-the-borough-president-of-staten-island` | https://www.statenislandusa.com/bp-office.html | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Public Administrator of Queens County | `public-administrator-of-queens-county` | https://www.queenscountypa.com/ | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Queens District Attorney's Office | `queens-district-attorney-s-office` | https://queensda.org/ | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Residential Mortgage Insurance Corporation | `residential-mortgage-insurance-corporation` | https://www.nychdc.com/subsidiaries/remic | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Sales Tax Asset Receivable Corporation | `sales-tax-asset-receivable-corporation` | https://www.nyc.gov/site/starcorp/index.page | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Staten Island Zoological Society | `staten-island-zoological-society` | https://www.statenislandzoo.org/ | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| Teachers' Retirement System of City of New York | `teachers-retirement-system-of-city-of-new-york` | https://www.trsnyc.org | `no_about_found` | Human: locate the about/mission page URL (site uses house conventions) |
| City University Construction Fund | `city-university-construction-fund` | https://www.cuny.edu/about/administration/offices/fpcm/cucf/ | `robots_disallowed` | Policy: robots.txt disallows crawling — decide whether to request permission or source text another way |
| City University of New York | `cuny` | https://www.cuny.edu/ | `robots_disallowed` | Policy: robots.txt disallows crawling — decide whether to request permission or source text another way |

_26 rows. Generated from data/descriptions.json (2026-07-15, post-PR #11)._