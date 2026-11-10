---
title: Source snapshots for the address inventory, 2026-11-10
created: 2026-11-10
type: reference
status: active
tags: [ai-readability, sources, snapshot, addresses]
---

# Source snapshots, 2026-11-10

Inputs to `../../addresses.csv` and the portal rows of `../../websites.csv` (issue #41). Method:
`docs/ai-readability/addresses.md`.

| File | Source |
|---|---|
| `ny-data-portal-registry.json` | BetaNYC's NY data portal registry, `data/ny-data-portal-registry.json` in [BetaNYC/ny-data-registry-mcp](https://github.com/BetaNYC/ny-data-registry-mcp) at commit `3e9e7aa` (generated 2026-11-05), copied whole. Only `live` and `UNCONFIRMED` rows are read |
| `linked-from-fetched-files.csv` | Sub-site addresses found in files the 2026-11-06 office scan already fetched (robots.txt `Sitemap:` lines, homepage redirects). Address and listed host only; no result from the run |
| `contact-websites.csv` | Website addresses from BetaNYC's private contact files that no other source lists. Address, registry id and a short note only; nothing else from those files |
