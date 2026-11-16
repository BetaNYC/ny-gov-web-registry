# NY Gov Web Registry

**One machine-readable answer to a deceptively hard question: which New York government entities exist, what are they called, what do they do, where do they operate — and where do they live on the web?**

Built and stewarded by [BetaNYC](https://beta.nyc). Free to use, reuse, and build on — with credit, and with improvements shared back (see [License](#license)).

> 🚧 **Work in progress.** The registry is live and usable (567 entities), but coverage is still growing and the schema may evolve. [Issues](../../issues) and contributions welcome.

## Why this exists

Not all government lives on `.gov`. The MTA is on `mta.info`, the Economic Development Corporation on `nycedc.com`, Health + Hospitals on `nychealthandhospitals.org`, CUNY on `cuny.edu` — while more than 150 city agencies share `nyc.gov`. Agencies get renamed, merged, and reorganized; their old websites disappear without notice. Anyone who wants to archive government on the web, cite it, monitor it, or simply join two datasets that each spell "Department of Finance" differently first needs an authoritative list of *which entities exist and how to identify them*. No single official source provides that across city government, public authorities, and the web domains they've used over time.

This registry is that list — **567 entities and growing** (New York City, plus, since 2026-11-06, 53 state executive agencies and the 156 state and local public authorities), each carrying its official name and acronym, former names, canonical government identifiers, boundary relationships, web domains, legal context, and (for 207 of them so far) the agency's own self-description, captured verbatim from its website.

## Who it's for

- **Web archivists** — the registry's domain list is a ready-made crawl set (its first consumer is a forthcoming NYC/NYS Wayback Machine harvester).
- **Civic technologists and journalists** — join datasets across agencies without hand-reconciling name variants; look up who operates which district system.
- **Researchers and historians** — traverse agency renames and successions; connect entities to Wikidata and archival authority files.
- **Other cities** — the schema is deliberately generic (see [Reuse beyond New York](#reuse-beyond-new-york)); fork it and swap the sources.

## What a record looks like

Every entity is one JSON record in [`data/registry.json`](data/registry.json). A plain-language tour of the fields:

| Field | What it holds |
|---|---|
| `id`, `name`, `short_name`, `other_names[]` | Stable id, official name, acronym, and former/alternate names — so decades of renames resolve to one entity |
| `identifiers[]` | External keys as `{scheme, identifier}` pairs: the City's canonical `nyc_goid`, `wikidata` QIDs, and room for any future scheme without changing the schema ([scheme catalog](docs/schemes.md)) |
| `web_properties[]` | The domains the entity has used, tagged primary/legacy — the crawl set |
| `areas[]` | Geography by reference: which [boundary layers](https://boundaries.beta.nyc) it operates (NYPD → police precincts) or sits in (borough presidents → their county) — never embedded geometry |
| `mandates[]` | The legal authority that created or empowers it (Charter provision, local law, executive order, statute) |
| `relations[]` | Predecessor/successor links between entities |
| `contact_details[]`, `classification`, `provenance` | Contact scaffolding, entity type, and which source asserted every fact |

Alongside it, [`data/descriptions.json`](data/descriptions.json) holds each agency's **own words** — about-page text captured verbatim (never paraphrased, never generated), with source URL and fetch date. Where no description could be captured, the record says exactly why (`no_about_found`, `no_url`, `robots_disallowed`…): coverage gaps are explicit, never papered over.

## Where the data comes from

Every fact traces to a named source; identifiers are only ever filled when verified against their upstream. Full provenance, per-source caveats, and refresh cadence: [`docs/sources.md`](docs/sources.md) and [`docs/freshness.md`](docs/freshness.md).

| Source | What it contributes | Link |
|---|---|---|
| **NYC Agencies and Governance Organizations** (`t3jq-9nkf`) | The spine: 306 active city entities — official names, acronyms, principal officers, org types. Maintained by NYC's Office of Data Analytics | [NYC Open Data](https://data.cityofnewyork.us/City-Government/NYC-Agencies-and-Governance-Organizations/t3jq-9nkf) · [source repo](https://github.com/MODA-NYC/nyc-governance-organizations) |
| **NYC Greenbook** (`mdcw-n682`) | The City's official directory: addresses, phones, office structure (used as structural scaffolding; last updated 2023) | [NYC Open Data](https://data.cityofnewyork.us/City-Government/Greenbook/mdcw-n682) |
| **NYC.gov Agency Directory** | Validation — the directory turns out to be powered by the same canonical dataset | [nyc.gov](https://www.nyc.gov/main/your-government/agency-directory) |
| **ABO Directory of Public Authorities** (`4vym-q77x`) | The 156 State and Local public authorities (45 + 111) and their self-reported websites. The 452 IDAs and LDCs are a later batch | [data.ny.gov](https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x) |
| **NY.gov Agency Listing** | Names of the state executive agencies | [ny.gov/agencies](https://www.ny.gov/agencies) |
| **Agency Contacts** (`kjw8-pupa`) | The Governor's office list of 79 agencies: a second source for agency names and contact-page hosts (contact names are not used) | [data.ny.gov](https://data.ny.gov/d/kjw8-pupa) |
| **A state agency listing** | 307 New York State websites and the agency that runs each — the state agency sites under `ny.gov` that no public list carries. Seeds the state agencies' web properties in the registry and the [website list](data/ai-readability/websites.csv) (domain, URL and agency kept; nothing else) | Private (shared with BetaNYC; no public link) |
| **CISA .gov domain list** | Every registered `.gov` domain; the 1,451 New York rows seed the [website list](data/ai-readability/websites.csv) | [github.com/cisagov/dotgov-data](https://github.com/cisagov/dotgov-data) |
| **NYS ITS Locality Hierarchy with Websites** (`55k6-h6qq`) | Websites of counties, cities, towns and villages (last updated 2023, partly stale); one of the website leads for the counties, cities, towns and villages | [data.ny.gov](https://data.ny.gov/d/55k6-h6qq) |
| **NYS Civil Boundaries** (GIS) | Who the counties, cities, towns and villages are, with their SWIS, GNIS and Census codes: the spine for the next batch (sync built 2026-11-13, issue #23; not minted yet) | [ArcGIS REST service](https://gisservices.its.ny.gov/arcgis/rest/services/NYS_Civil_Boundaries/FeatureServer) |
| **OSC Local Government Entities** | The State Comptroller's list of every local government; the cross-check and municipal code for the same batch | [osc.ny.gov](https://web.osc.state.ny.us/localgov/web-entity-map/) |
| **Wikidata** | QIDs linking entities into linked open data (attached only via verified domain matches, with a place-vs-organization guard) | [wikidata.org](https://www.wikidata.org) |
| **IRS EIN / tax-exempt data** | Identifier scheme reserved for `.org` public benefit corporations | [IRS Tax Exempt Organization Search](https://apps.irs.gov/app/eos/) |
| **NYC Boundaries Map** | The district-layer vocabulary that `areas[]` points into | [boundaries.beta.nyc](https://boundaries.beta.nyc) · [repo](https://github.com/BetaNYC/nyc-boundaries) |
| **Agency websites themselves** | The verbatim self-descriptions | per-record `source_url` in `descriptions.json` |

## Related projects

| Project | Relationship |
|---|---|
| [BetaNYC/nyc-boundaries](https://github.com/BetaNYC/nyc-boundaries) | Boundaries Map — the sole home of district geometry; this registry references its layers, never copies them |
| [MODA-NYC/nyc-governance-organizations](https://github.com/MODA-NYC/nyc-governance-organizations) | The City's canonical entity dataset — this registry's spine |
| [MODA-NYC/nyc-entity-resolver](https://github.com/MODA-NYC/nyc-entity-resolver) | The City's own fuzzy-matching library, used (pinned) for reconciling messy agency names — we wrote no bespoke matcher |
| [CityOfNewYork/CROL-Overview](https://github.com/CityOfNewYork/CROL-Overview) · [CROL-Schema](https://github.com/CityOfNewYork/CROL-Schema) | The 2014–2016 City Record Online workgroup — the civic-data-standards lineage (Popolo, schema.org) this schema rejoins |
| BetaNYC City Record archive | Machine-readable archive of NYC's official journal (1873–present, in preparation for public release) — the registry's `other_names` and `relations` exist partly so a century and a half of agency name variants can resolve |
| NYC/NYS Wayback harvester | Forthcoming — reads this registry's `web_properties` as its crawl set |
| [GSA/site-scanning-documentation](https://github.com/GSA/site-scanning-documentation) · [Site Scanning data guide](https://digital.gov/guides/site-scanning/data) | The federal government's daily scan of its own websites. Our AI-readability output (`sites.csv`) and site list (`websites.csv`) follow its column layout and data dictionaries, so the two can be read side by side |
| [cisagov/dotgov-data](https://github.com/cisagov/dotgov-data) | CISA's list of every registered `.gov` domain — the source of the New York `.gov` domains in [`websites.csv`](data/ai-readability/websites.csv) |

## Using the data

Consuming the data is just reading two committed, schema-validated JSON files:

```
data/registry.json        # 567 entities
data/descriptions.json    # per-entity self-descriptions + coverage records
```

Rebuilding from sources requires Python 3.11+ and [uv](https://docs.astral.sh/uv/): `uv sync`, place the documented exports in `data/cache/` (**no script fetches the network on its own** — sources are operator-gated by design; see [`docs/sources.md`](docs/sources.md)), run the `sync_*` scripts, then `python scripts/build_registry.py`. The offline test suite (`pytest`, 471 tests) never touches the network.

Two artifacts invite human eyes: the [description review queue](docs/description-review-queue.md) (agencies whose about page needs a human to find) and the [site anomalies log](docs/site-anomalies.md) (defects we noticed on live government sites — including three Lorem-ipsum placeholder pages).

### `probe_bot_access.py` — how these hosts treat AI agents

`scripts/probe_bot_access.py` is a study built on the registry's crawl set rather than a step in
building the registry. It asks each host two questions and keeps them separate: **what it says**
(`robots.txt`, `llms.txt`) and **what it does** — the live response to each declared identity:
search crawlers, AI training crawlers, AI answer-engine crawlers, and **agentic** fetchers
(`ChatGPT-User`, `Claude-User`, `Perplexity-User`: a person asked, right now).

Every vendor User-Agent comes from the checked list of crawler names,
[`data/ai-readability/crawlers.csv`](data/ai-readability/crawlers.csv) ([how it is kept](docs/ai-readability/crawlers.md)),
each tied to its company's own page and reviewed twice. Labels are copied word for word, never
assembled, and a version stand-in such as `Chrome/W.X.Y.Z` is never filled in (`scripts/crawler_list.py`).
Only three controls live in code: the BetaNYC research crawler, the library default, and a
disclosed desktop-Chrome probe. Rules-only names (`Google-Extended`, `Applebot-Extended`, ...) are
read in robots.txt and never sent. Before a run starts, a guard refuses it if the list fails its own
checks, if any sent label is not in the list, or if a row was checked more than 30 days ago.

Network-touching, so it is **operator-gated like every other fetch here**: round-robin across hosts,
a global pace with jitter, per-host floors, stricter governors for the two concentrated `nyc.gov`
hosts, and a circuit breaker that quarantines a host on 429 while treating 403 as data rather than
failure. `--dry-run` prints the plan without a single request.

```
python3 scripts/probe_bot_access.py --dry-run                      # plan only, no network
python3 scripts/probe_bot_access.py --vantage residential --out ./probe-out
python3 scripts/probe_bot_access.py --vantage office --tier a --only-hosts @hosts.txt
```

Runs are checkpointed per request, so an interruption loses nothing and the same command resumes.
Offline tests: `python3 tests/test_probe_bot_access.py` (24 assertions, no network).

**AI-readability mode** (`--tier core|full`) runs the [test catalog](docs/ai-readability/tests.md)
from the same script: robots/llms/sitemap, the homepage once per identity, a 404 probe and an
`http://` check per host (11 requests in core), with the content tests parsed from the one
research-UA homepage by `scripts/ai_readability.py`. A host that pushes back is quarantined and
listed for review; only our own breakage stops a run, and `status.json` says `done` or `broke`.
Results go to `data/ai-readability/runs/<date>-<vantage>/`, which is git-ignored (results stay
private until the study concludes). Offline tests: `tests/test_ai_readability.py`.

The targets are `data/ai-readability/addresses.csv` (every address: main sites, sub-sites,
sections, data portals, APIs) when it exists, else `websites.json`. The full tier adds the data
endpoint checks (the RFC 9727 API catalog, a WordPress site's REST index, and for data portals the
platform's documented catalog, one dataset page, and the catalog per crawler name). Every
`run.json` records the network the run went out on (place, provider, ASN, public address, time; at
the start and the end) via `scripts/network_record.py` (RIPEstat). `--vantage vpn` is allowed only
for a small `--only-hosts` set. `--dry-run` prints the request total and an estimated wall clock.
Offline tests: `tests/test_rerun_scanner.py`.

**V1, the browser check:** `scripts/render_check.py` loads each homepage in headless Chrome twice:
once under the browser's own name ("HeadlessChrome") and once as plain "Chrome", a disclosed
control. Where the two differ, the site decides by the declared name. It needs Playwright and
refuses to run without `--operator-authorized`. Offline tests: `tests/test_render_check.py`.

**Why a site could not be read:** `scripts/reach_check.py` takes the sites a run could not read
(W4 `na`, `blocked` or `fail`) and checks each layer in order: does the name resolve (or its `www`
/ bare variant), does anything answer on 443 and 80, does the certificate verify, and what GET `/`
returns over HTTPS and plain http. Each site gets one diagnosis (`dns_missing`,
`bad_certificate_http_ok`, `refuses`, `answers_now`, ...). It writes `reach.jsonl` into the run
folder, and the export turns it into the `ai_reach_*` columns. A later run over the same failures
(`--retest-run-dir`) fills `ai_retest_*`. Offline tests: `tests/test_reach_check.py`.

**Would a person get in?** `scripts/headed_check.py` takes sites that refused every crawler. It
visits them in a real, visible Chrome that keeps its own name, and scores the same core tests on
what Chrome received. That separates "blocks bots" from "blocks everyone". The export's
`--person-run-dir` turns the run into the `ai_person_*` columns. Offline tests:
`tests/test_headed_check.py`.

**The website list:** [`data/ai-readability/websites.csv`](data/ai-readability/websites.csv) (and
`.json`) is every New York government website we know of, plus every open-data portal and data
API in BetaNYC's portal registry (`ny_kind`; nonprofit, research, library and university
portals carry `ny_government` FALSE and are never counted as government), one row per host, built by
`scripts/ai_readability_websites.py` from the registry (so the two stay in step) plus the
statewide lists in the table above. It uses the layout of GSA's Federal Website Index, with a
`source_list_*` column per source; fields are in
[`target-list-dictionary.csv`](data/ai-readability/target-list-dictionary.csv). A row means a
public list names the host, not that it is a registry entity. Test results stay private.
A host a list still names but that is no longer the body's site (a lookalike, a domain for
sale, a redirect elsewhere) is recorded by hand in
[`website-corrections.csv`](data/ai-readability/website-corrections.csv). Its row stays, with
`ny_host_issue` and `ny_superseded_by` set, and probe runs skip it.
Pages that are not in the registry but belong in the deep-page run (the community boards whose
only site is a `www.nyc.gov/site/<board>/` page) are listed by hand in
[`deep-pages-extra.csv`](data/ai-readability/deep-pages-extra.csv) (`url`, `name`, `source`,
`checked`). A listed page that a person checked in a browser and found moved or gone is recorded
in [`page-corrections.csv`](data/ai-readability/page-corrections.csv) (`page_url`, `issue`
`moved` or `gone`, `moved_to`, `checked`, `evidence`): the deep-page run probes a moved page at
its new URL and skips a gone one. The source lists keep the old URL as the sources state it.

**The address inventory:** [`data/ai-readability/addresses.csv`](data/ai-readability/addresses.csv)
is one row per address to scan (main site, sub-site, section, data portal, API), with its owning
body, source and date found, built by `scripts/address_inventory.py` from the website list, the
deep pages, the portal registry, files the last scan already fetched, BetaNYC's contact files and
public certificate logs (`scripts/cert_log_names.py`). Nothing is guessed. Columns, sources,
counts and the certificate-log filter: [`docs/ai-readability/addresses.md`](docs/ai-readability/addresses.md).
Offline tests: `tests/test_address_inventory.py`.

**Deep pages:** on hosts shared by several registry bodies (`www.nyc.gov`, `www1.nyc.gov`, ...),
`probe_bot_access.py --deep-pages` scores each body's own page rather than the host's homepage:
the three Tier B identities, page-level tests only, robots.txt once per host, the same pacing.
`headed_check.py --deep-pages` gives the person view of the same pages and lists any page that
needs a human click in `needs_person.txt`, and any page that is gone (404/410) in
`pages_not_found.txt`. **Every section:** `--pages-from addresses` takes the pages from
`addresses.csv` instead (every section, plus data portals and APIs on a path, that the host run
does not read as its own job), and `--page-identities full` sends every crawler name a
`--tier full` host run sends (default `tierb`, the three above). `ai_readability_export.py
--run-dir <page run>` writes `pages.csv`, one row per page. Method:
[`docs/ai-readability/tests.md`](docs/ai-readability/tests.md) § Deep pages. Offline tests:
`tests/test_deep_pages.py`, `tests/test_deep_pages_addresses.py`.

```
python3 scripts/probe_bot_access.py --deep-pages --dry-run
python3 scripts/probe_bot_access.py --pages-from addresses --page-identities full --dry-run
python3 scripts/probe_bot_access.py --deep-pages --vantage office --machine office \
    --run-dir data/ai-readability/runs/<YYYY-MM-DD>-office-pages
uv run --with playwright python scripts/headed_check.py --deep-pages \
    --run-dir data/ai-readability/runs/<YYYY-MM-DD>-office-pages-headed --vantage office \
    --operator-authorized
```

**Federal layout:** `scripts/ai_readability_export.py` turns a run folder into `sites.csv` and
`sites.json`, one row per website, in the layout of GSA's
[Site Scanning](https://digital.gov/guides/site-scanning/data) data: every GSA column in GSA's order
(filled only where our test measures the same thing, blank otherwise), then our `ny_` and
`ai_<test>_` columns. Every column is defined in
[`data/ai-readability/data-dictionary.csv`](data/ai-readability/data-dictionary.csv), in GSA's
dictionary format and generated by the script (`--write-dictionary`). GSA's own
dictionaries are copied in `data/ai-readability/gsa/`. It reads `addresses.csv` or `websites.json`,
adds a column pair per crawler name the run used and the run's network record, and
`--only-run-hosts` limits it to the hosts a trial run visited. Offline tests:
`tests/test_ai_readability_export.py`, `tests/test_ai_readability_websites.py`.

```
uv run --with playwright python scripts/render_check.py --targets-file pilot.json \
    --run-dir data/ai-readability/runs/<YYYY-MM-DD>-<vantage> --operator-authorized
```

```
python3 scripts/probe_bot_access.py --tier core --targets-file pilot.json --dry-run
python3 scripts/probe_bot_access.py --tier core --targets-file pilot.json --vantage office \
    --machine office --on-exit "bash notify.sh"
```

First results (2026-08-28, 116 hosts with a complete identity matrix): about three quarters of
NYC-area government hosts admit self-identifying AI agents at rates close to Googlebot, while
`www.nyc.gov` and `www1.nyc.gov` refuse every honestly-identified client and admit browsers —
uniformly across all 150 deep agency paths.

## Reuse beyond New York

Nothing in the schema is New York–specific. Field vocabulary follows [Popolo](https://www.popoloproject.com/) and the [W3C Organization ontology](https://www.w3.org/TR/vocab-org/); external keys are extensible `{scheme, identifier}` pairs; geography is by reference, with [Census GEOIDs](https://www.census.gov/programs-surveys/geography/guidance/geo-identifiers.html) as the documented national scheme; archival interoperability maps to [EAC-CPF](https://eac.staatsbibliothek-berlin.de/) ([field crosswalk](docs/eac-cpf-crosswalk.md)). Forking for another city means swapping the configured sources — not performing schema surgery.

## Part of BetaNYC's civic data tools

This registry anchors a family of free, open civic data assets [BetaNYC](https://beta.nyc) builds and stewards:

- [`ny-gov-web-archiver`](https://github.com/BetaNYC/ny-gov-web-archiver) — a throttled Wayback Machine harvester that consumes this registry's domain list.
- [`nyc-executive-orders`](https://github.com/BetaNYC/nyc-executive-orders) — the open archive of NYC mayoral executive orders, 1974–present; its `establishes_entity` links point at this registry's stable IDs.
- [`nyc-boundaries`](https://github.com/BetaNYC/nyc-boundaries) — NYC administrative boundaries, mapped and queryable (this registry references its boundary IDs rather than embedding geometry).
- Seven MCP servers giving AI agents direct access to NYC/NYS civic data: [Council legislation](https://github.com/BetaNYC/nyc-council-mcp), [City Record](https://github.com/BetaNYC/nyc-record-mcp), [Checkbook spending](https://github.com/BetaNYC/nyc-checkbook-mcp), [311](https://github.com/BetaNYC/nyc-311-mcp), [Charter/Code/Rules](https://github.com/BetaNYC/nyc-charter-laws-rules), [NYS legislation](https://github.com/BetaNYC/nys-openlegislation-mcp), and the [Council budget](https://github.com/BetaNYC/New-York-City-Budget).

## AI use in this project

BetaNYC uses AI tools openly and with human accountability. This registry was built by AI agents (Anthropic's Claude) working under the direction and review of BetaNYC staff: the specification was drafted, revised, and approved by a human operator; every pull request was human-merged; entity merges and identity judgments followed human-confirmed rules, and anything uncertain was routed to human-review queues rather than decided by the machine.

Two commitments specifically about the data:

- **No content in this registry is AI-generated.** Agency descriptions are captured verbatim from the agencies' own websites, and every identifier is verified against its named upstream — never inferred, never invented.
- **All crawling was polite and attributable.** Identified user-agent with a contact address, robots.txt honored, rate-limited, and access-blocked gaps recorded honestly rather than circumvented.

Questions about our approach: hello@beta.nyc.

## License

Two licenses, both chosen so that **credit is required** and **improvements flow back to the commons**:

- **Code** (`scripts/`, `tests/`): [GNU General Public License v3.0](LICENSE) — use and modify freely; derivative code must stay open under the same terms, with attribution preserved.
- **Data and documentation** (`data/`, `docs/`, `schema/`): [Creative Commons Attribution-ShareAlike 4.0](LICENSE-DATA) — reuse and adapt with credit to BetaNYC, and share adaptations under the same license.

The underlying government records (agency names, identifiers, the agencies' own published text) are public records of the City and State of New York; these licenses cover this compilation, its enrichment, and its tooling. If you improve the registry — new sources, corrections, a fork for your city worth upstreaming — please open a pull request or issue.

## Contributing

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Low-lift, high-value contributions: resolve a row in the [description review queue](docs/description-review-queue.md) (find an agency's about page we couldn't), report an entity we're missing, or confirm items in the reconciliation reports.

---

*Build-history detail formerly in this README (the phase-by-phase status narrative) lives in the merged pull requests (#2–#15), [issue #1](https://github.com/BetaNYC/ny-gov-web-registry/issues/1), and [`docs/freshness.md`](docs/freshness.md).*
