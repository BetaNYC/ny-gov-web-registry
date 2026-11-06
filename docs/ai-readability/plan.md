---
title: AI-readability telemetry for New York government websites — research plan
created: 2026-11-06
type: research-plan
status: draft
tags: [ai-readability, research-plan, registry, nys, telemetry]
---

# AI-readability telemetry for New York government websites — research plan

- **Report generated:** 2026-11-06
- **Status:** awaiting operator approval. Nothing below has been run. No crawl, no probe, no new
  entities until Noel says go.
- **Companion docs:** test catalog [`tests.md`](tests.md) · sources
  [`../sources-statewide.md`](../sources-statewide.md) · registry growth
  [`../registry-growth-plan.md`](../registry-growth-plan.md)

## 1. Goals

1. A **repeatable** measurement of how readable every New York government website is to AI
   agents: welcome, map, readable, identity, data, fresh, verify (catalog in `tests.md`).
2. Run it on a **schedule**, so the result is telemetry over time, not a one-day snapshot.
3. Cover **state, county and municipal** web properties, by first growing the registry from
   authoritative state sources (`registry-growth-plan.md`).
4. Keep raw results. Any score is a view on top of them, including a faithful rebuild of Abhi
   Nemani's 12-row score so our numbers can be compared with his.

## 2. Scope

**In:** state executive agencies; State and Local public authorities; 57 counties (+ the five NYC
borough presidents at the county level); 62 cities (NYC already in); 933 towns; 532 villages; all
existing NYC registry entities (287 web properties). Homepage-level tests only, plus `robots.txt`,
`llms.txt`, sitemap index, and one 404 probe per host.

**Out (explicit):** deep crawling; any page beyond the homepage (except the ≤10 link checks in F3,
full tier); school districts, fire and other special districts; IDAs and LDCs (pending decision);
Indian nations; courts; legislators' member sites; anything behind a login; any attempt to get past
a block (a 403 is the answer, not an obstacle); asking AI assistants questions and tracking
citations (Abhi's monthly citation test — a different study, possible later phase).

## 3. Sources

- **Method:** Abhi Nemani, ["What Happens When AI Visits City Hall?"](https://abhinemani.com/blog/2026/10/20/AI-Readable/)
  (2026-10-20); our own `probe_bot_access.py` (PR #19) and the 2026-08-28 / 2026-10-03 NYC runs;
  vendor crawler docs and IETF drafts (listed in `tests.md`).
- **Who exists:** NYS Civil Boundaries GIS (March 2026), OSC entity tables (2026), ABO `4vym-q77x`,
  Governor's office `kjw8-pupa`, plus MODA `t3jq-9nkf` for NYC (unchanged).
- **Where their websites are:** CISA .gov list (1,451 NY domains, daily), ITS `55k6-h6qq` (2023,
  stale, leads only), ABO `website`, Wikidata P856 (cross-check only).

## 4. Method

### 4.1 One script, two modes

Extend `scripts/probe_bot_access.py` rather than writing a second crawler. It already has the
identity table, pacing, circuit breaker, checkpoint/resume, WAF fingerprinting and body classifier.
Add:

- `--tier core|full` from `tests.md` (core ≈ 10 requests per host, full ≈ 30).
- Parsers for the new tests (M1–M3 sitemap, M4 shape, R, I, D, F, W3 precedence, W6 signals). All
  content tests parse the one homepage body already fetched; they add no requests.
- A `--catalog-version` stamped into every row.
- V1 (headless render) as a separate, sampled step, because it needs a browser.

### 4.2 Vantage

Two connections, never merged: **office** (BetaNYC static IP) and **residential** (Noel's home).
Prior runs showed the same host can answer differently by vantage. Each run records its egress
class and the run host (`whichmachine.sh` name). No cloud, VPN or proxy vantage: those IPs are often
pre-blocked, which would measure the IP rather than the site.

### 4.3 Etiquette and load

Unchanged from the probe (≥30 s per host, global pace with jitter, 403 is data, 429 quarantines,
honest UA with contact address). Statewide core run ≈ 2,500 hosts × 10 ≈ **25,000 requests**.
**Timing, measured not guessed:** the 2026-10-03 office run made 916 requests in 4 h 22 m, about
**17 s per request**, because the probe is single-file (one request at a time) and the NYC set is
dominated by `www.nyc.gov`, which has its own 20 s governor. A statewide set spreads over thousands
of hosts, so the host floor binds less, but each request still costs the global pace (6 s) plus the
response time (slow or dead municipal sites can take up to the 25 s timeout). Realistic estimate
for 25,000 requests: **about 2 days per vantage** at today's settings (6–8 s each). Getting it to
one night needs a change: several workers in parallel, each on **different** hosts, so per-host
politeness is unchanged. Four workers ≈ 12–14 h (estimate; confirm on a pilot before promising it).
The full tier runs on a 10% sample plus every NYC host.

### 4.3a Pushback never stops the run (Noel, 2026-11-06)

When a site pushes back (403, 429, challenge page, timeouts, resets), the **run keeps going** on
every other site. The site that pushed back is not retried that run (that would be hammering it);
it is written to `review.jsonl` in the run folder: host, entity, test, what happened, time. That
list is reviewed by a person later. The run itself stops only for a real breakage of our own
(disk full, script crash, our network down). Notifications go to Noel twice at most:
**done** (with the counts and the review list) or **broke** (with the error and where to resume).

Built 2026-11-06 in `probe_bot_access.py --tier core|full`: how "our network down" is told apart
from dead sites, the `status.json` fields and the `--on-exit` hook are in
[`tests.md` § Runner](tests.md#runner-v01-scriptsprobe_bot_accesspy---tier-corefull).

### 4.4 Output

```
data/ai-readability/
  portals.json                       # D1 open-data portal hosts (seeded, curated)
  data-dictionary.csv                # every column of sites.csv, in GSA's dictionary format
  target-list-dictionary.csv         # the site-list fields, in GSA's target-list format
  gsa/                               # GSA's own two dictionaries, copied 2026-11-06
  runs/<YYYY-MM-DD>-<vantage>/
    run.json                         # manifest: catalog version, git SHA, identities + doc URLs,
                                     #   vantage, machine, start/end, request count, breaker events
    results.jsonl                    # one row per (host, test, identity): raw status, evidence,
                                     #   verdict (pass/partial/fail/blocked/na), evidence sha256
    summary.md                       # counts per test, Abhi-12 view, our view, flips since last run
    sites.csv / sites.json           # one row per website, GSA Site Scanning layout + our columns
                                     #   (scripts/ai_readability_export.py; data-dictionary.csv)
  latest.json                        # per entity id + host: newest verdict per test (sidecar)
```

**Attaching to entities:** a sidecar keyed by registry `id` and host, the same pattern as
`data/descriptions.json`. Telemetry is a time series; it does **not** go into `registry.json`
(the schema forbids extra fields, and a registry record should not change because a site had a bad
day).

### 4.5 Scoring

Raw verdicts are the dataset. Two views are computed from them, never stored as truth:

- **Abhi-12:** his 12 rows, Pass 1 / Partial 0.5 / Fail 0 / Blocked 0, out of 12. Comparable with
  his five cities.
- **BetaNYC view:** the same rows, but **Blocked kept separate from Fail**, and stated-vs-enforced
  divergence (W5) reported on its own line, because the edge and the page have different owners.

### 4.6 Cadence

- **Monthly:** core tier, office vantage, first weekend of the month.
- **Quarterly:** core tier from both vantages + full tier sample + V1 renders.
- **On change:** a catalog version bump reruns a baseline before any comparison.
- A result "flip" is reported only after it repeats on the next run (V3).

## 5. Output location

Everything lives in **BetaNYC/ny-gov-web-registry**: docs in `docs/ai-readability/`, data in
`data/ai-readability/`, code in `scripts/`, tests in `tests/`. The BetaNYC workspace keeps only a
pointer and the internal write-ups.

⚠️ **This repo is public.** Decided 2026-11-06 (Noel): **publish** the registry (every website
and government web endpoint), the test catalog and the code. **Keep results private** until the
study is concluded. So `data/ai-readability/runs/` and `latest.json` are git-ignored here and
copied to the private workspace (`team/research/ai-readability-ny/`) after each run. Only
`portals.json` and the docs are committed in this repo.

## 6. Effort

| Workstream | Work | Estimate |
|---|---|---|
| A. Catalog + probe extension | tests, parsers, tiers, fixtures | 2 sessions (software-engineer) |
| B. Source caches + sync scripts | `sync_nys_localities.py`, `sync_dotgov.py`, `sync_nygov.py`, schema/scheme docs | 2 sessions |
| C. Mint state agencies + authorities | curation of ~100 agencies, ABO State/Local | 1–2 sessions |
| D. Mint counties, cities, towns, villages | mechanical from GIS + OSC, collision handling | 1 session |
| E. Website leads + review | CISA/ITS matching, review file, optional 1-GET live check | 2 sessions + operator review time |
| F. Baseline run + summary | ~12–14 h unattended per vantage with 4 workers (estimate; the 281-host test pool took 1.5 h), then write-up | 1 session + 2 unattended runs |

About **9–10 working sessions** plus two unattended weekend runs. Everything before workstream F
makes **no requests to government websites** except the optional live check in E.

## 7. GitHub tracking (posted 2026-11-06)

Epic [#28](https://github.com/BetaNYC/ny-gov-web-registry/issues/28): **Statewide AI-readability telemetry and registry growth**, with one issue per workstream
A–F above: A #22, B #23, C #24, D #25, E #26, F #27 (on hold). Every PR names its issue; close each with a dated note. Existing issues this touches:
#1 (populate from canonical sources), #17 (Wikidata guard; no change proposed), #20 (officeholder
pointers; not needed for this work).

## 8. Open questions for Noel

Answered 2026-11-06: "Legistar" meant **listed** (everything lives in this repo); publish the
registry, tests and code, keep results private until concluded; mint the **156** State + Local
authorities now, with the other 452 (IDAs, LDCs) as a **stretch goal**.

4. **Mechanism probe.** Keep the disclosed desktop-browser control UA in the core tier? It is what
   proved the nyc.gov block is about identity. Recommendation: keep it.
5. **ITS dataset staleness.** Tell ITS that `55k6-h6qq` is 3.5 years stale (an upstream-gap issue,
   like #6)? Recommendation: yes, after our verified list exists to show them.
