---
title: AI-readability test catalog (v0.2, draft)
created: 2026-11-06
updated: 2026-11-10
type: research-method
status: draft
tags: [ai-readability, method, robots, llms-txt, registry]
---

# AI-readability test catalog — v0.2 (draft)

- **Report generated:** 2026-11-06
- **Last revised:** 2026-11-10 (catalog 0.2: crawler list, C1, D2–D7; issues #43, #44)
- **Data current as of:** vendor crawler docs and IETF drafts read 2026-11-06; data-endpoint
  specs and the network-lookup docs read 2026-11-10 (see § Sources)
- **Status:** proposal. Nothing in this catalog has been run against any host yet. Running it
  needs the operator's explicit OK (see [`plan.md`](plan.md)).

Every test here is meant to be **re-run, unchanged, months apart**, so each one states exactly what
request it makes and what counts as pass. When a test changes, the catalog version changes, and
results from different versions are never compared without a note.

## Where the tests come from

Abhi Nemani, ["What Happens When AI Visits City Hall?"](https://abhinemani.com/blog/2026/10/20/AI-Readable/)
(2026-10-20). Read closely 2026-11-06. What he actually did:

- He asks four questions, following "the path an AI agent actually walks through a site": *Is it
  welcome? Is there a map? Does the content arrive as readable text? Does the site say clearly who
  it is, point to usable data, and stay current?*
- He scores **12 flat rows** (in the embed `city-ai-scorecard.html`): Pass 1, Partial 0.5, Fail 0,
  Blocked 0, out of 12. Five cities, one day, "an AI fetching tool" he does not name. He calls it
  "a smoke test rather than a ranking."
- **No code is published for the city audit.** His site's own test script
  ([`scripts/agent_test.py`](https://github.com/abhinemani/abhinemani.github.io)) tests only
  abhinemani.com; we borrow its Markdown-twin and llms.txt shape checks.
- His crawler User-Agent strings do **not** match the vendors' current docs (e.g. `GPTBot/1.2` vs
  OpenAI's `/1.4`; his ClaudeBot string is not published by Anthropic). We do not copy them.

The seven groups below (welcome, map, readable, identity, data, fresh, verify) are **our** grouping
of his four questions plus his closing advice ("test what a bot actually receives"). The column
`Abhi row` names the scored row each test reproduces, so his 12-point view can be rebuilt exactly.

Our own prior work it builds on: `scripts/probe_bot_access.py` (PR #19) and the 2026-08-28 /
2026-10-03 NYC runs (BetaNYC workspace, `team/research/agentic-access-nyc/`). Headline from those:
across 116 NYC hosts, AI bots are admitted at about the same rate as Googlebot, but `www.nyc.gov`
and `www1.nyc.gov` refuse every honestly identified client and admit a desktop browser; the robots
file says yes while the edge says no.

## Request budget (per host)

| Tier | Tests | Requests per host |
|---|---|---|
| **core** | all `W`, `M1–M4`, `R1`, `R3`, all `I`, `D1`, `F1`, `F2`, `F4`, `P1–P4` | **11** (robots 1, llms 1, sitemap 1, homepage × 6 identities, 404 probe 1, `http://` check 1 for I5) |
| **full** | core + `W4` with every identity (24 from 2026-11-10) + `M5` + `R2` + `F3` + `D3` + `D4`, plus `D5–D7` on data portals | **≈ 33–54**; a data portal adds up to 26 |
| **render** | `V1` (headless browser render) | 1 page load, sampled hosts only |

Content tests (`R`, `I`, `D1`, `D2`, `C1`, `F1`) parse the **one** homepage body fetched with the
research identity. They cost no extra requests.

**Catalog 0.2 (2026-11-10).** The full tier per host is: core (5 + identities), llms-full, two
markdown probes, the RFC 9727 API catalog, the WordPress REST index only when the homepage shows
WordPress (0–1), and up to 20 link checks (10 links, HEAD then GET on 405). With 24 identities that
is 33 to 54 requests. An address of kind `data portal` adds the portal tests: its catalog, a
Socrata Discovery lookup (Socrata only), one dataset page, and the catalog once per other identity:
up to 26 more. `--dry-run` prints the total and an estimated wall clock from the pacing.

### Addresses (issue #41)

The next run's list of addresses (main sites, sub-sites, sections, data portals, APIs) is
`data/ai-readability/addresses.csv`, built by `scripts/address_inventory.py`. How it is built,
and the certificate-log filter: [`addresses.md`](addresses.md). The scanner reads it from #44.

### Deep pages (`--deep-pages`, issue #35)

Some hosts carry many registry bodies on their own pages (`www.nyc.gov`, `www1.nyc.gov`,
`edc.nyc`, ...), and each of those bodies would otherwise inherit its host's homepage score. Deep-page
mode scores each such page. **Which pages:** every `ny_page_urls` entry of a
`websites.json` row whose `ny_entity_ids` lists more than one body (rows with `ny_host_issue`
skipped), plus `data/ai-readability/deep-pages-extra.csv` (pages that are not in the registry,
such as the community boards' `nyc.gov/site/<board>/` pages), after the hand checks in
`data/ai-readability/page-corrections.csv`: a page found `gone` (404, no new page anywhere) is
skipped, and a page that `moved` is probed at its `moved_to` URL with the same owners, recorded
as `moved_from` (paced on its new host when it moved off-host). A page whose path is the host's
homepage is left to the host run, and a page listed twice (`www.` and a trailing `/` ignored) is
sent once. Each page's owner names and registry ids come from the source records that list it
(`ai_readability_websites.page_owners()`); a page no source pairs keeps an empty name, never a
guessed one. **What runs per page:** the Tier B identities (`research_betanyc`, `claudebot`,
`mechanism_probe_chrome`), one GET of the page each, and only the page-level tests `W3` (the page's
own path against its host's robots.txt), `W4` per identity and the all-AI aggregate, `R1`, `R3`,
`I1–I4` (I4 against every name the sources give the page), `F1`, `F2`, `D1`, `P1–P4`. robots.txt
is fetched **once per page host per run** and shared by its pages; llms.txt, the sitemap, the 404
probe and the `http://` check stay with the host run. Rows carry `scope: "page"` and `page_url`
(host rows are unchanged). **Requests:** one robots.txt per distinct page host + pages × 3,
before retries; `--dry-run` prints the total and the busiest host. **Pacing:** the same as a host
run (global pace, ≥ 30 s per host, the `www.nyc.gov` / `www1.nyc.gov` governors, never two
requests in flight to one host), and every request for a page is paced on its list row's host,
so `nyc.gov/...` and `www.nyc.gov/...` share one clock. The runner keeps at most one page per
host in its window and round-robins across hosts; with `--max-workers`, each host's pages stay on
one worker. Because one host's requests are serialized, the busiest host sets the run's length.
Pushback quarantines the host, as in a host run; resume is per page. **The person view:**
`headed_check.py --deep-pages` runs the same page plan through a visible Chrome (`headed_chrome`,
its own User-Agent), one page load per page plus each page host's robots.txt, paced by the same
host clock, one browser context per host, no retries. A page that shows a challenge or block page
is recorded (`blocked`, `challenge: true`) and listed in `needs_person.txt` in the run folder for
a person to try by hand; it is never clicked and never retried. A page the server says is gone
(404 or 410) is a dead registry link, not a block: it is recorded as `not_found` and listed in
`pages_not_found.txt`. Both lists are written even when empty. Results stay in the git-ignored
run folder.

## Identities (who we say we are)

**From catalog 0.2 (2026-11-10, issue #44) the scanner reads [`data/ai-readability/crawlers.csv`](../../data/ai-readability/crawlers.csv)** (34 names, each checked twice against its company's page; rules and history in [`crawlers.md`](crawlers.md)). Every vendor identity comes from it through `scripts/crawler_list.py`; only three controls stay in code (`research_betanyc`, `baseline_default`, `mechanism_probe_chrome`). How a row becomes a sent label (one label per name, never a stand-in, name-only crawlers sent as the bare name) is in [`crawlers.md` § How a row becomes what the scanner sends](crawlers.md#how-a-row-becomes-what-the-scanner-sends-issue-44-2026-11-10). W2 and W3 read every name in the list, sent or rules-only, plus five legacy tokens from catalog 0.1 (`anthropic-ai`, `Claude-Web`, `cohere-ai`, `Diffbot`, `omgili`) kept read-only so W2 stays comparable with the first run.

The table below is the list the 2026-11-06 run (catalog 0.1) used.

Every string below is copied **verbatim from the vendor's own page** (accessed 2026-11-06) or is
marked token-only. Never add a guessed string (workspace engineering standards §0): a guessed UA
measures a bot that does not exist. Vendors label several strings "example, version may change," so
robots.txt matching is by **token**, never by full string.

| Key | Token | Class | Full string verified? | Vendor doc |
|---|---|---|---|---|
| `research_betanyc` | — | honest baseline | ours | this repo |
| `baseline_default` | — | honest baseline (library default) | n/a | — |
| `googlebot` | Googlebot | search (control) | yes | [Google](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers) |
| `bingbot` | bingbot | search (control) | yes | [Bing](https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0) |
| `gptbot` | GPTBot | AI training | yes (`/1.4`) | [OpenAI](https://developers.openai.com/api/docs/bots) |
| `ccbot` | CCBot | AI training corpus | yes | [Common Crawl](https://commoncrawl.org/ccbot) |
| `claudebot` | ClaudeBot | AI training | **token only** — Anthropic publishes no full string | [Anthropic](https://support.claude.com/en/articles/8896518) |
| `oai_searchbot` | OAI-SearchBot | AI search index | yes | OpenAI |
| `perplexitybot` | PerplexityBot | AI search index | yes | [Perplexity](https://docs.perplexity.ai/docs/resources/perplexity-crawlers) |
| `chatgpt_user` | ChatGPT-User | user-triggered live fetch | yes | OpenAI |
| `perplexity_user` | Perplexity-User | user-triggered live fetch | yes | Perplexity |
| `claude_user` | Claude-User | user-triggered live fetch | **token only** | Anthropic |
| `mechanism_probe_chrome` | — | control: desktop browser UA, disclosed in the dataset | n/a | — |

The **core** tier uses 6 of these: `research_betanyc`, `googlebot`, `gptbot`, `oai_searchbot`,
`chatgpt_user`, `mechanism_probe_chrome` (one per class, plus the browser control that shows a
block is about identity, not reachability).

**Robots-only tokens** (checked in robots.txt, never sent as a UA, because no request UA exists):
`Google-Extended`, `Applebot-Extended`, `Claude-SearchBot`. Also matched in robots.txt: `Applebot`,
`Amazonbot`, `Amzn-SearchBot`, `Amzn-User`, `meta-externalagent`, `meta-externalfetcher`,
`MistralAI-User`, `DuckAssistBot`, `Bytespider` (no vendor doc found; token match only).

What a UA test can and cannot prove: a 403 to a vendor string *suggests* an identity-based block;
a 200 does **not** prove the real bot gets in, because bot-management layers also check source IP
ranges, which we cannot and will not imitate.

## The tests

Columns: **id** · what it checks · **Abhi row** (the scored row it reproduces, or —) · method ·
pass rule · evidence kept · known false positives · cost · **probe** (already in
`probe_bot_access.py`? yes / partial / new).

### W — Welcome: may an agent come in?

| id | Checks | Abhi row | Method | Pass rule | Evidence | False positives | Cost | probe |
|---|---|---|---|---|---|---|---|---|
| **W1** | robots.txt reachable | "Has robots.txt" | `GET https://<host>/robots.txt`, research UA, no redirects followed off-host | 200, `text/plain`, body parses as robots (not HTML) | status, content-type, first 8 KB, sha256 | a WAF challenge page served with 200 (caught by body classifier); HTML 200 soft-errors | 1 | **yes** (core, v0.1 runner) |
| **W2** | robots.txt names AI crawlers | "Names AI crawlers" | parse W1 body per RFC 9309; match groups against the AI token list (case-insensitive) | ≥ 1 AI token has its own group | tokens named, each group's rules | a group copied from a template with no intent; naming to *block* still passes (it is a decision) | 0 | **yes** (core, v0.1 runner) |
| **W3** | What robots.txt *means* for each AI token at `/` | — | RFC 9309 longest-match evaluation of `/` and the homepage path for each token, falling back to `*` | reported per token: allow / disallow (no pass/fail) | per-token verdict | `Allow`/`Disallow` precedence bugs in hand-written files | 0 | **yes** (core, v0.1 runner) |
| **W4** | Edge admits each identity | "Homepage reachable by an agent" | `GET https://<host>/` once per identity; record status + body class | **admitted** = 200 **and** body not a challenge/block page | status, headers subset, WAF fingerprint, body class, 2 KB snippet | 200 challenge pages (classifier handles known vendors); geo/IP-reputation blocks that differ by vantage | 6 (core) / 13 (full) | **yes** (core, v0.1 runner) |
| **W5** | Stated vs enforced agree | — | derived: W3 says allow but W4 refuses (or the reverse) | reported as a divergence flag | per-token pair | single transient 5xx; re-checked on the next run before reporting | 0 | **yes** (core, v0.1 runner) |
| **W6** | AI-preference signals declared | — | parse W1 body for `Content-Signal:` ([Cloudflare](https://blog.cloudflare.com/content-signals-policy/)), `Content-Usage:` (IETF [draft-ietf-aipref-attach-05](https://datatracker.ietf.org/doc/draft-ietf-aipref-attach/)), and `__usage_*` user-agent groups ([draft-kelley-aipref-training-preference-signal-00](https://datatracker.ietf.org/doc/draft-kelley-aipref-training-preference-signal/)) | reported (signal present + values); none of these are standards yet | lines found | a CDN-managed robots.txt may add these lines for the site owner, so presence may not be a decision (UNVERIFIED which vendors do this by default) | 0 | **yes** (core, v0.1 runner) |

### M — Map: is there a way to find things?

| id | Checks | Abhi row | Method | Pass rule | Evidence | False positives | Cost | probe |
|---|---|---|---|---|---|---|---|---|
| **M1** | Sitemap declared in robots.txt | "Sitemap listed in robots.txt" | `Sitemap:` lines in W1 body | ≥ 1 absolute URL | URLs | a declared sitemap that 404s (caught by M2) | 0 | **yes** (core, v0.1 runner) |
| **M2** | Sitemap works | "Working sitemap.xml" | `GET` first M1 URL, else `/sitemap.xml`; parse XML; if an index, record child count but **do not** fetch children | 200 + valid `urlset` or `sitemapindex` XML | status, type, URL count or child count, sha256 | gzip sitemaps; huge indexes (we stop at the index) | 1 | **yes** (core, v0.1 runner) |
| **M3** | Sitemap is fresh | (part of "No stale content") | from M2: share of `<url>` with `<lastmod>`, newest and median lastmod | reported: newest lastmod age in days | dates | CMSs that stamp every URL with today's date (flag if > 90% share one date) | 0 | **yes** (core, v0.1 runner) |
| **M4** | llms.txt present and well formed | "Has llms.txt" | `GET /llms.txt`, research UA | 200, text (not HTML), starts with `# ` H1 then a `> ` summary ([llmstxt.org](https://llmstxt.org/)) | status, type, first 8 KB | soft-404 HTML served at 200 (shape check catches it) | 1 | **yes** (core, v0.1 runner) |
| **M5** | llms-full.txt present | — | `GET /llms-full.txt` | reported only | status, size | as M4 | 1 (full) | **yes** (full, on) |

**M5's source, checked 2026-11-10:** [llmstxt.org](https://llmstxt.org/) does **not** define
`llms-full.txt`; it names only `/llms.txt`. The file is a convention some documentation hosts
publish, with no spec behind it. M5 stays report-only and is never scored.

llms.txt is a 2024 community proposal by Jeremy Howard, not a standard, and the big AI vendors do
not say they read it. We record it because governments are adopting it (Maryland's
[maryland.gov/llms.txt](https://www.maryland.gov/llms.txt) is live, checked 2026-11-06), not
because it is known to work.

### R — Readable: does real text arrive without a browser?

| id | Checks | Abhi row | Method | Pass rule | Evidence | False positives | Cost | probe |
|---|---|---|---|---|---|---|---|---|
| **R1** | Text without JavaScript | "Text readable without JavaScript" | from the research-UA homepage body: strip `script/style/noscript/template`, count visible words in `<main>` or `<body>`; flag shell phrases ("Loading…", "enable JavaScript", empty root div) | ≥ 150 visible words **and** no shell phrase | word count, shell phrase hit, 1 KB of extracted text | splash/landing pages that are short by design (record, do not fail below 150 if `<main>` has links + headings — mark Partial) | 0 | **yes** (core, v0.1 runner) |
| **R2** | Markdown twin | — | `GET /` with `Accept: text/markdown`; also `GET /index.md`; and (0.2, free) any markdown version the homepage advertises with a `Link:` header or `<link>` of rel `alternate`, type `text/markdown` | reported: markdown served (type `text/markdown` and not HTML); `advertised_alternates` listed | status, type, `Vary` header, advertised hrefs | servers that ignore Accept and return HTML (that is a plain "no") | 2 (full) | **yes** (full, on) |
| **R3** | Page has structure and a language | — | from homepage body: `<html lang>`, one `<h1>`, a `<main>` or landmark, `<title>` | reported per item | booleans | — | 0 | **yes** (core, v0.1 runner) |

### I — Identity: does the site say clearly who it is?

| id | Checks | Abhi row | Method | Pass rule | Evidence | False positives | Cost | probe |
|---|---|---|---|---|---|---|---|---|
| **I1** | Meta description | "Meta description" | `<meta name="description">` | present, ≥ 50 chars, not a CMS default | text | boilerplate ("Just another WordPress site") — keep a short deny-list | 0 | **yes** (core, v0.1 runner) |
| **I2** | Open Graph | "Open Graph tags" | `og:title`, `og:description`, `og:site_name` | all three present = Pass; one or two = Partial | values | — | 0 | **yes** (core, v0.1 runner) |
| **I3** | Structured data says "government" | "Structured data (schema.org)" | parse JSON-LD and microdata; look for `GovernmentOrganization`, `GovernmentOffice`, `City`, `AdministrativeArea`, or `Organization` with a government name | a government type = Pass; any `Organization`/`WebSite` = Partial | types found, `name`, `url`, `sameAs` | theme-default `WebSite` blocks (Partial, not Pass) | 0 | **yes** (core, v0.1 runner) |
| **I4** | One consistent description | — | compare `<title>`, I1, I2 description, I3 `name`, and the registry's own name/`descriptions.json` text | reported: names agree? (normalized match) | the strings side by side | acronym vs full name; reported, never scored | 0 | **yes** (core, v0.1 runner) |
| **I5** | Official-domain signal | — | host ends in `.gov` or `ny.gov`/`nyc.gov`, or is in the CISA .gov list; HTTPS with a valid certificate; `http://` redirects to `https://` | reported per item (most NY towns are not on .gov; not a failure) | booleans, cert issuer + expiry | — | 1 (`http://` redirect check) | **yes** (core, v0.1 runner) |

### D — Data: does the site point to usable data?

| id | Checks | Abhi row | Method | Pass rule | Evidence | False positives | Cost | probe |
|---|---|---|---|---|---|---|---|---|
| **D1** | Homepage links to open data | "Open data linked from the homepage" | homepage anchors whose host is a known portal (list in `data/ai-readability/portals.json`, seeded from `data.ny.gov`, `data.cityofnewyork.us`, ArcGIS Hub domains, and the county/city portals in `BetaNYC/grounding-ai-with-ny-open-data` `resources/ny-open-data-portals.md`) or whose text says "open data" | ≥ 1 such link | matched hrefs | link in a mega-menu loaded by JS is missed (R1 tells you why); "data" pages that are PDFs | 0 | **yes** (core, v0.1 runner) |

### Catalog 0.2 additions: website software and data endpoints (issue #43, 2026-11-10)

Each row was written before its code, from the document named in **Source** (read 2026-11-10).
No path or field is used that its source does not document; where a source left something
unstated, the row says so and the test does without it. `C1` and `D2` read only what the host run
already fetched. `D3`–`D7` run in the **full** tier only.

| id | Checks | Source | Method | Pass rule | Cost (requests) |
|---|---|---|---|---|---|
| **C1** | Website software (CMS) | [GSA Site Scanning `cms`](https://digital.gov/guides/site-scanning/data) (field definition in `data/ai-readability/gsa/Site_Scanning_Data_Dictionary.csv`); [WordPress REST API handbook, Discovery](https://developer.wordpress.org/rest-api/using-the-rest-api/discovery/) | Keep the GSA Site Scanning `cms` value as is when the address list carries one (`cms` column). Fill a gap only from the homepage already fetched: P1's signals (meta generator, known asset paths, `x-powered-by`/Drupal headers) plus the WordPress REST link, rel `https://api.w.org/`, in a `Link:` header or `<link>` | reported: `cms`, `source` (`gsa` / `homepage` / none), the homepage signals | 0 |
| **D2** | Data and API links, pass 1 | none (a reading aid): sorts links into kinds by URL patterns kept in `ai_readability.D2_PATTERNS` | From the homepage's anchors and `<link>` tags, its `Link:` header, and llms.txt's markdown links: links to open data (portal list of D1, or the words "open data"), APIs, developer pages, catalogs (`data.json`, `/api/catalog/v1`, `/api/feed/dcat`, `/api/3/action/`, `/.well-known/api-catalog`), feeds (RSS/Atom types, `/feed`) and GIS services (`/arcgis/rest/services`, `MapServer`/`FeatureServer`, WMS/WFS) | reported: links, counts by kind. No link is followed | 0 |
| **D3** | WordPress REST index | [WordPress REST API handbook, Discovery](https://developer.wordpress.org/rest-api/using-the-rest-api/discovery/) | Only when C1 shows WordPress. GET the root the site itself advertises (rel `https://api.w.org/`, including the `?rest_route=/` form the handbook says to expect) when it is on the same host, else `/wp-json/` (the handbook's example root). Same-host redirects only | reported: `rest_index` true when 200 JSON carries `namespaces` ("The index of the API exposes the namespaces item"); namespaces and route count kept | 0–1 |
| **D4** | API catalog (RFC 9727) | [RFC 9727](https://www.rfc-editor.org/rfc/rfc9727.html) | Every site: GET `/.well-known/api-catalog` (§2: publishers "SHALL resolve an HTTPS GET request" there), `Accept: application/linkset+json` | reported: `present` = 200 with a Linkset (top-level `linkset` array); `media_type_ok` = served as `application/linkset+json` (§4.2, a MUST); `profile_rfc9727` = the profile parameter (a SHOULD); `items` = links of relation `item` (§3.1) | 1 |
| **D5** | Portal: a machine-readable catalog | Socrata [Metadata API](https://dev.socrata.com/docs/other/metadata) ("accessible on every Socrata domain"; list form "list all metadata for all public assets on a domain"); Esri [ArcGIS Hub, Federate data](https://doc.esri.com/en/arcgis-hub/latest/build/federate-data.html) ("DCAT US 1.1 (<siteURL>/api/feed/dcat-us/1.1.json)"); [CKAN Action API](https://docs.ckan.org/en/latest/api/index.html) (`/api/3/action/package_list`, `limit`); [DCAT-US v1.1](https://resources.data.gov/resources/dcat-us/) (`conformsTo`, `dataset` required) | Only for addresses of kind `data portal`. Platform read from the homepage already fetched (host `*.hub.arcgis.com`/`*.opendata.arcgis.com`, meta generator `ckan…`/`ArcGIS Hub`, the homepage naming Socrata) or the list's own `platform` value: the portal registry's words, mapped only where they name Socrata (not retired), ArcGIS Hub or CKAN outright; ArcGIS Enterprise Sites, ArcGIS Online apps and ArcGIS Server map to nothing (`ai_readability.registry_platform`). Added 2026-11-10 after the home trial run, where most portals' homepages named no platform. Then one GET at the platform's documented path on the portal's own host: Socrata `/api/views/metadata/v1?limit=1&page=1`, Hub `/api/feed/dcat-us/1.1.json`, CKAN `/api/3/action/package_list?limit=1`. An unrecognized platform is `na`: no path is tried that its vendor does not document, and `/data.json` is not tried (Esri's current docs do not mention it; DCAT-US does not fix its location) | pass: 200 and the documented shape (Socrata: JSON, since its list form has no published schema; Hub: DCAT-US with `conformsTo` and a `dataset` array; CKAN: `success` true and a `result` array). fail: answered otherwise. blocked / na as everywhere | 1 |
| **D6** | Portal: one dataset page readable without JavaScript | DCAT-US `landingPage`; Socrata [Discovery API](https://dev.socrata.com/docs/other/discovery) (`https://api.us.socrata.com/api/catalog/v1`, `domains`, `limit`; `results[].permalink`); CKAN docs' `/dataset/<name>` page form ([theming](https://docs.ckan.org/en/latest/theming/htmx.html)) | The first dataset the catalog names: Hub `dataset[0].landingPage`; Socrata `results[0].permalink` from one Discovery lookup (`domains=<host>&limit=1`), because the metadata list has no documented fields; CKAN `/dataset/<result[0]>`. GET it with the research identity | R1's rule on that page (≥ 150 words and no JS-shell phrase = pass) | 1, Socrata 2 |
| **D7** | Portal: the catalog reached by each crawler name | as D5 | The D5 catalog URL once per other identity of the full tier | W4's rule per identity (admitted = 200 and not a challenge page) and W4's all-AI aggregate row | identities − 1 (23 today) |

The Socrata Discovery lookup goes to Tyler's own API host (`api.us.socrata.com`, documented "for
domains in North America"), not to the portal; it is paced on its own host clock like any host.

### F — Fresh: is it current, and does it tell the truth about errors?

| id | Checks | Abhi row | Method | Pass rule | Evidence | False positives | Cost | probe |
|---|---|---|---|---|---|---|---|---|
| **F1** | Copyright / "updated" year | (part of "No stale or broken content") | regex `©\|copyright` + 4-digit year, and "last updated"/"updated" dates, in visible text | newest year found ≥ current year − 1 | years found | sites that never print a year (reported "none", not fail) | 0 | **yes** (core, v0.1 runner) |
| **F2** | Server freshness headers | — | `Last-Modified`, `Date`, `Age` on the homepage response | reported only | header values | CDNs rewrite these; context only, never scored | 0 | **yes** (core, v0.1 runner) |
| **F3** | Broken links on the homepage | (part of "No stale or broken content") | pick up to 10 same-host homepage links (deterministic: first 10 in document order); `HEAD`, fall back to `GET` on 405 | ≤ 1 of 10 returns 4xx/5xx | per-link status | WAF blocks on deep links look like breakage (cross-check W4) | ≤ 20 (full: 10 HEAD, plus a GET for each 405) | **yes** (full, on) |
| **F4** | Honest 404 | — | `GET /ai-readability-probe-<run-id>-does-not-exist` | 404 or 410 = Pass; 200 = soft-404 (Fail) | status, body class | sites that redirect unknown paths to home (302 → 200) count as soft-404 | 1 | **yes** (core, v0.1 runner) |

The combined Abhi row "No stale or broken content" = **F1 pass and F3 pass** (F3 only in the full
tier; in core, F1 alone decides and the row is marked "Partial evidence").

### V — Verify: is what a bot gets the same as what a person gets?

| id | Checks | Abhi row | Method | Pass rule | Evidence | False positives | Cost | probe |
|---|---|---|---|---|---|---|---|---|
| **V1** | Browser vs plain fetch, and does the declared name decide? | — (his closing advice) | `scripts/render_check.py`: headless Chrome loads the homepage twice — `headless_default` (the browser's own UA, contains "HeadlessChrome") and `headless_as_chrome` (same UA with only that marker changed to "Chrome"; a disclosed control). Records status, title, visible word count, challenge-page flag | per variant: pass = 200 and not a challenge page; blocked otherwise. Reported: hosts where the two variants differ (the name decides), and rendered vs R1 raw word counts | status, title, 600 chars of text, sha256 | consent banners; personalization; splash pages with few words | 2 page loads per host (both variants) | **yes** (`render_check.py`, 2026-11-06) |
| **V2** | Vantage parity | — | the same core tier from office **and** residential connections | reported: per identity, same admit verdict? | both rows | IP-reputation differences are the point, not an error | doubles W4 | yes (prior runs used both) |
| **V3** | Run-to-run drift | — | diff this run's results against the previous run of the **same catalog version** | reported: tests that flipped | flip list | transient outages (a flip must repeat next run before it is reported as change) | 0 | **new** |

**Reach diagnosis (not a scored test).** For every site a run could not read, `scripts/reach_check.py`
records why, one layer at a time: DNS (and the `www` / bare variant), TCP 443 and 80, certificate
verification, and GET `/` over HTTPS and plain http (at most 2 requests, research UA, no redirects
followed, no retries). The result is one diagnosis per site, defined in `reach_check.DIAGNOSES` and
in the `ai_reach_diagnosis` row of the data dictionary. It separates dead or misspelled list entries
and broken certificates from real refusals, so a failure is never read as "blocks AI" by default.
Added 2026-11-08.

**Person check (not a scored test).** For sites that refused every crawler, `scripts/headed_check.py`
asks whether a person would get in. It opens the installed Google Chrome as a normal, visible window,
under its own unchanged User-Agent, so no name is borrowed. It runs the probe's own core-tier request
plan and scoring (`probe_bot_access.host_plan`) on what Chrome receives. Rows are labeled identity
`headed_chrome`, never the research identity. It is not an evasion tool: no stealth plugins, no
fingerprint patching, no CAPTCHA solving, and no retries. It needs `--operator-authorized` and a
logged-in desktop session. Added 2026-11-08.

### P — While we are there (adopted 2026-11-06, all four, per Noel)

Noel asked what else to record, since every NY government site gets visited once. These read the
**same** homepage body and headers already fetched, so they cost **zero extra requests**. They are
observations, never scored.

| id | Records | Method | Why |
|---|---|---|---|
| **P1** | CMS and site platform | `<meta name="generator">`, telltale paths in the HTML (`/wp-content/`, `/sites/default/`, `/-/media/`), response headers (`x-powered-by`, `x-drupal-cache`), known government vendors (CivicPlus, Granicus, Revize, Municode/CivicEngage, Squarespace, Wix) | which vendors shape most local sites; one vendor fix can lift hundreds |
| **P2** | Civic vendor platforms linked | homepage links to Legistar, Granicus, eCode360, ArcGIS Hub, Laserfiche, PrimeGov, BoardDocs, SeeClickFix | where agendas, laws and data actually live; extends D1 |
| **P3** | Language access | translation widget present, links to other-language pages, `hreflang` | NY language-access rules; an agent also reads only what is served |
| **P4** | Third-party scripts | count of distinct script hosts (analytics, tag managers, chat widgets) | privacy and weight; a count only, no names published per site until reviewed |

CDN/WAF is already recorded by W4's fingerprint, so it is not listed again.

**probe:** P1–P4 are all **yes** (core, v0.1 runner), verdict `reported`. P4's host list is kept in
the private results (`hosts_private`) for review; only the count is meant for publication.

## Rebuilding Abhi's 12-row score

| Abhi row | Our test(s) |
|---|---|
| Has robots.txt | W1 |
| Names AI crawlers | W2 |
| Homepage reachable by an agent | W4 (Pass if every AI identity admitted; Partial if some) |
| Sitemap listed in robots.txt | M1 |
| Working sitemap.xml | M2 |
| Has llms.txt | M4 |
| Text readable without JavaScript | R1 |
| Meta description | I1 |
| Open Graph tags | I2 |
| Structured data (schema.org) | I3 |
| Open data linked from the homepage | D1 |
| No stale or broken content | F1 + F3 |

When W4 refuses every identity, content tests run on nothing, so those rows score **Blocked = 0**,
as Abhi's do. Our own view keeps **Blocked separate from Fail**, because "the edge refused us" and
"the page is bad" are different problems with different owners.

## Etiquette (unchanged from `probe_bot_access.py`, which was tuned on real runs)

- Identify honestly; the research UA carries the repo URL and a contact address.
- One request per (host, test, identity). Nothing recursive. Sitemap indexes are read, not walked.
- Round-robin across hosts with a global pace and jitter; ≥ 30 s between requests to the same host;
  stricter governors for hosts that carry many entities (`www.nyc.gov`, `www1.nyc.gov`, and, for the
  statewide set, shared municipal hosts such as county-hosted town pages).
- A 403 is data, never retried. A 429, timeout, reset, TLS or DNS failure quarantines the host for
  the rest of the run and goes to `review.jsonl`; **pushback never stops the run** (Noel,
  2026-11-06; replaces the earlier "many hosts 429ing stops the run" rule). One retry on
  5xx/timeout only. The run stops only on our own breakage (see § Runner below).
- No evasion: no cookie harvesting, TLS fingerprinting, header spoofing beyond the disclosed UA
  identities, or retry-until-through.

## Runner (v0.1, `scripts/probe_bot_access.py --tier core|full`)

Added 2026-11-06. Parsers and verdicts are in `scripts/ai_readability.py` (pure, tested offline in
`tests/test_ai_readability.py`); fetching, pacing and quarantine are in `probe_bot_access.py`.

**Verdicts.** `pass · partial · fail` are scored. `blocked` = the edge refused us (401, 403, 429
or a challenge page). `na` = not measured (transport failure, host quarantined, or the input does
not exist, e.g. F1 when no year is printed). `reported` = report-only tests (W3, W5, W6, M3, M5,
R2, R3, I4, I5, F2, P1–P4, and from 0.2 C1, D2, D3, D4). D5–D7 are scored like the rest, but are
not part of the Abhi-12 view. Content tests read only the research-UA homepage: if that one was
refused they are `blocked`, even when another identity got in.

**Where the code fills a gap in the pass rules above** (so a reviewer can disagree):

| test | rule the code applies |
|---|---|
| W1 | `partial` when the body parses as robots but the type is not `text/plain`, or when robots.txt redirects off-host (not followed) |
| W3 | no readable robots.txt (4xx) = allow-all per RFC 9309 §2.3.1.3; robots.txt 5xx = disallow-all per §2.3.1.4. Tokens evaluated: the W2 list plus Googlebot and bingbot |
| M2 | fetches **one** sitemap: the first absolute `Sitemap:` URL, else `/sitemap.xml` (so core is 11 requests per host, not 10–12) |
| M4 | `partial` when the H1 is there but no `> ` line follows it (valid per llmstxt.org, short of our pass rule) |
| R1 | `partial` when ≥ 150 words but a shell phrase is present, or < 150 words with headings and links and no shell phrase |
| I1 | `partial` when present but under 50 characters; CMS default text = `fail` |
| I5 | CISA .gov list membership is checked only with `--dotgov-file` (a local copy); otherwise "not checked" |
| F1+F3 | Abhi's last row: in core, F1 alone; in full, pass only if both pass, `partial` if one passes and the other is `na` |

**Requests per host:** core 11 (robots, llms, sitemap, homepage × 6, 404 probe, `http://`
check). Full adds llms-full, `Accept: text/markdown`, `/index.md` and ≤ 10 F3 link checks (HEAD,
GET on 405), with 12 identities plus bingbot (Bing's published legacy string).

**Pushback never stops the run.** 429, timeout, reset, TLS or DNS failure (after the one retry) →
that host is quarantined for the rest of the run, its remaining tests come back `na`, and a row
goes to `review.jsonl` (host, entity id, test, identity, what happened, time, action). 401/403 and
challenge pages are recorded in `review.jsonl` too, but do **not** quarantine the host: the other
identities' answers are the measurement.

**Redirects are cleaned the way a browser cleans them** (`redirect_url()`, WHATWG URL Standard):
tab and newline removed, edge spaces stripped, any other space or control character
percent-encoded. A redirect whose `Location` carries a bot manager's own marker
(`BLOCK_REDIRECT_MARKERS`: today `botmanager_support@radware.com`, seen on a live challenge
redirect 2026-11-10) makes the response a block page (`body_class` and a `waf` entry
`radware_botmanager`), so it scores `blocked`. Before this, a challenge `Location` holding spaces
failed in `http.client` as `InvalidURL`, read as a transport failure, and quarantined the host.

**The run stops only on our own breakage:** a crash (including disk full), SIGTERM/SIGINT, the
`--max-requests` cap, or our network down. "Network down" needs all three: 8 consecutive
transport failures (any success resets the count), spread over ≥ 4 distinct hosts (a single dead
site is quarantined after its first failure, so it can never fill the streak), and **no answer at
all** from neutral non-government check URLs (Apple's and Google's captive-portal endpoints, any
HTTP status counts), tried twice 60 s apart. If the check answers, the streak resets and the run
goes on.

**What it reads (0.2).** Targets: `--targets-file`, else `data/ai-readability/addresses.csv`
(issue #41: address, owning body, kind, level or entity_type, source, date found, notes; optional
`platform` and `cms`), else `websites.json`. One job per host; a host's homepage is read from its
`data portal` or `main site` address before a sub-site, API or section. Sections on a shared host
are scored page by page with `--deep-pages`. Crawlers: `--crawlers`, else
`data/ai-readability/crawlers.csv`.

**Pre-run guard (0.2).** The run refuses to start, and prints why, when crawlers.csv fails its own
checks, when any label it would send is not in the list (controls excepted), when a sent label
carries a version stand-in, or when any row's `checked` is more than 30 days old. `--dry-run` runs
the guard too.

**Network record (0.2).** `run.json` → `network`: `place` (office | home | vpn, from `--vantage`),
and a reading at the `start` and the `end` of the run, each with the provider, ASN, public
address, prefix and time, plus `changed` (ASN or address differ; null if either reading failed).
One documented service, the RIPEstat Data API, no token: `whats-my-ip` (`ip`), `network-info`
(`asns`, `prefix`), `as-overview` (`holder`), with `sourceapp` set as RIPEstat asks; 3 requests per
reading, never to a government site. A failed lookup is recorded with its `error` and null fields,
never guessed. (ipinfo.io was not used: its docs do not document `/json` without a token and show
no `org` field.) Every result row carries `place`.

**VPN (0.2).** `--vantage vpn` is a disclosed third vantage, used only to ask "is this block about
our network?" for a small named set: it needs `--only-hosts` and at most 25 hosts, and the probe
refuses a full VPN run. Its rows carry `place: vpn`, so they never mix with office or home.

**Output** (`data/ai-readability/runs/<YYYY-MM-DD>-<vantage>/`, git-ignored): `run.json`,
`results.jsonl` (one row per host × test × identity, stamped with `catalog_version` and git SHA),
`review.jsonl`, `status.json` (`state: done|broke`, counts, `error` and `resume_cmd` when broke),
`summary.md` (counts per test, the Abhi-12 view, the BetaNYC view), and `progress.jsonl` (hosts
finished; used to resume). `--on-exit "<cmd>"` runs `<cmd> <path to status.json>` at exit,
including after a crash. `latest.json` (plan §4.4) is not built yet.

**One row per website:** `python scripts/ai_readability_export.py --run-dir <run> --targets-file
<list>` writes `sites.csv` and `sites.json` in GSA's Site Scanning layout, plus `ai_<test id>_`
columns for this catalog. Column definitions: `data/ai-readability/data-dictionary.csv`.

**Parallel workers:** `--max-workers N` splits hosts into N disjoint groups, each with its own
global pace. A shared host clock still prevents two in-flight requests to one host and keeps the
per-host floor. Homepage redirects to another host are followed (as the legacy probe did) and are
the one case where that other host is not locked.

## Sources (accessed 2026-11-06)

Vendor crawler pages: [OpenAI](https://developers.openai.com/api/docs/bots) ·
[Anthropic](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler) ·
[Perplexity](https://docs.perplexity.ai/docs/resources/perplexity-crawlers) ·
[Google](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers) ·
[Apple](https://support.apple.com/en-us/119829) · [Common Crawl](https://commoncrawl.org/ccbot) ·
[Meta](https://developers.facebook.com/docs/sharing/webmasters/web-crawlers/) ·
[Bing](https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0) ·
[Amazon](https://developer.amazon.com/docs/amazonbot/amazonbot.html) ·
[Mistral](https://docs.mistral.ai/robots) ·
[DuckDuckGo](https://duckduckgo.com/duckduckgo-help-pages/results/duckassistbot).
Standards and conventions: [llmstxt.org](https://llmstxt.org/) · [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309) ·
[RFC 9727](https://www.rfc-editor.org/rfc/rfc9727.html) ·
[WordPress REST API, Discovery](https://developer.wordpress.org/rest-api/using-the-rest-api/discovery/) ·
Socrata [Discovery](https://dev.socrata.com/docs/other/discovery) and [Metadata](https://dev.socrata.com/docs/other/metadata) APIs (their bodies load from `https://dev.socrata.com/apis/discovery.yaml` and `metadata.yaml`) ·
Esri [ArcGIS Hub, Federate data](https://doc.esri.com/en/arcgis-hub/latest/build/federate-data.html) ·
[CKAN Action API](https://docs.ckan.org/en/latest/api/index.html) ·
[DCAT-US v1.1](https://resources.data.gov/resources/dcat-us/) ·
RIPEstat [Data API](https://stat.ripe.net/docs/02.data-api/) (network record) — all read 2026-11-10 ·
IETF [aipref](https://datatracker.ietf.org/wg/aipref/about/) and
[webbotauth](https://datatracker.ietf.org/wg/webbotauth/about/) working groups ·
[Cloudflare Content Signals](https://blog.cloudflare.com/content-signals-policy/).
Maryland figures (12M requests, 15 AI crawlers, 30 days): [StateScoop, 2026-01-12](https://statescoop.com/llmstxt-government-websites-ai/),
quoting a Maryland official's email. No Maryland primary source found; cite as reported.
