---
title: GSA Site Scanning dictionaries (copies)
created: 2026-11-06
type: reference
status: active
tags: [ai-readability, gsa, site-scanning, data-dictionary]
---

# GSA Site Scanning dictionaries

Copied 2026-11-06 from [GSA/site-scanning-documentation](https://github.com/GSA/site-scanning-documentation)
(`data/`), unchanged. They define the federal Site Scanning program's per-website fields
([digital.gov guide](https://digital.gov/guides/site-scanning/data)).

- `Site_Scanning_Data_Dictionary.csv` — the scan fields. `scripts/ai_readability_export.py` reads
  it for the column order of `sites.csv`, and copies its `name`, `description` and `data type`
  into our `../data-dictionary.csv`.
- `Target_URL_List_Data_Dictionary.csv` — the website-list fields; our `../target-list-dictionary.csv`
  follows its format.

The repository states no license. These are U.S. federal government works (17 U.S.C. § 105).
Refresh by re-downloading both files; `tests/test_ai_readability_export.py` then shows whether our
dictionary needs regenerating.

Not yet confirmed: how GSA writes list values in its CSV (the dictionary does not say). We use
comma-separated values.
