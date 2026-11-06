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
| `public_suffix_list.dat` | Public Suffix List (publicsuffix.org, MPL-2.0), used by `scripts/ai_readability_export.py` for `base_domain` |

These are test-pool inputs, not registry sources. Registry sync scripts still read `data/cache/`.
