---
title: Source snapshots for the 2026-11-06 test pool
created: 2026-11-06
type: reference
status: active
tags: [ai-readability, sources, snapshot]
---

# Source snapshots, 2026-11-06

Raw copies of the lists used to build `../../pool-2026-11-06.json` and `../../websites.csv`, kept so the pool can be
rebuilt exactly. Pulled 2026-11-06. None of the sources states a license (see
`docs/sources-statewide.md`).

| File | Source |
|---|---|
| `cisa-dotgov-current-full.csv` | CISA .gov domain list, `current-full.csv` (github.com/cisagov/dotgov-data) |
| `its-locality-websites-55k6-h6qq.json` | NYS ITS "Locality Hierarchy with Websites", data.ny.gov `55k6-h6qq` (last updated 2023-03-06) |
| `abo-directory-4vym-q77x.json` | NYS ABO Directory of Public Authorities, data.ny.gov `4vym-q77x` |
| `state-agency-listing-websites.csv` | A state agency listing (shared privately; link private). Domain, URL and agency columns only |
| `kjw8-pupa-agency-contacts-agencies.json` | Agency Contacts, data.ny.gov `kjw8-pupa` (Office of the Governor): `agency` and `agency_contact_url` only, 79 agencies. Contact names, titles and phones were not fetched |
| `nygov-state-agencies-and-departments.json` | The ny.gov agency directory (`ny.gov/agencies`), 98 entries: name and directory page, read once in a browser |
| `public_suffix_list.dat` | Public Suffix List (publicsuffix.org, MPL-2.0), used by `scripts/ai_readability_export.py` for `base_domain` |

These began as test-pool inputs. Since 2026-11-06 the ABO directory, the state agency listing, Agency Contacts and the ny.gov directory are also registry sources (issue #24): copies sit in `data/cache/`, which the sync scripts read.
