# NY Gov Web Registry

**One machine-readable answer to a deceptively hard question: which New York government entities exist, what are they called, what do they do, where do they operate — and where do they live on the web?**

Built and stewarded by [BetaNYC](https://beta.nyc). Free to use, reuse, and build on — with credit, and with improvements shared back (see [License](#license)).

> 🚧 **Work in progress.** The registry is live and usable (317 entities), but coverage is still growing and the schema may evolve. [Issues](../../issues) and contributions welcome.

## Why this exists

Not all government lives on `.gov`. The MTA is on `mta.info`, the Economic Development Corporation on `nycedc.com`, Health + Hospitals on `nychealthandhospitals.org`, CUNY on `cuny.edu` — while more than 150 city agencies share `nyc.gov`. Agencies get renamed, merged, and reorganized; their old websites disappear without notice. Anyone who wants to archive government on the web, cite it, monitor it, or simply join two datasets that each spell "Department of Finance" differently first needs an authoritative list of *which entities exist and how to identify them*. No single official source provides that across city government, public authorities, and the web domains they've used over time.

This registry is that list — **317 entities and growing**, each carrying its official name and acronym, former names, canonical government identifiers, boundary relationships, web domains, legal context, and (for 207 of them so far) the agency's own self-description, captured verbatim from its website.

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
| **ABO Directory of Public Authorities** (`4vym-q77x`) | 600+ state and local public authorities (future expansion) | [data.ny.gov](https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x) |
| **NY.gov Agency Listing** | State executive agencies (future expansion) | [ny.gov/agencies](https://www.ny.gov/agencies) |
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

## Using the data

Consuming the data is just reading two committed, schema-validated JSON files:

```
data/registry.json        # 317 entities
data/descriptions.json    # per-entity self-descriptions + coverage records
```

Rebuilding from sources requires Python 3.11+ and [uv](https://docs.astral.sh/uv/): `uv sync`, place the documented exports in `data/cache/` (**no script fetches the network on its own** — sources are operator-gated by design; see [`docs/sources.md`](docs/sources.md)), run the `sync_*` scripts, then `python scripts/build_registry.py`. The offline test suite (`pytest`, 171 tests) never touches the network.

Two artifacts invite human eyes: the [description review queue](docs/description-review-queue.md) (agencies whose about page needs a human to find) and the [site anomalies log](docs/site-anomalies.md) (defects we noticed on live government sites — including three Lorem-ipsum placeholder pages).

### `probe_bot_access.py` — how these hosts treat AI agents

`scripts/probe_bot_access.py` is a study built on the registry's crawl set rather than a step in
building the registry. It asks each host two questions and keeps them separate: **what it says**
(`robots.txt`, `llms.txt`) and **what it does** — the live response to each of 12 declared
identities spanning search crawlers, AI training crawlers, AI answer-engine crawlers, and
**agentic** fetchers (`ChatGPT-User`, `Claude-User`, `Perplexity-User`: a person asked, right now).

Every User-Agent is verified against its operator's published documentation, or explicitly flagged
`string_verified: false`. `Google-Extended` and `Applebot-Extended` are never sent as live agents —
they are robots.txt control tokens with no request user agent, and a test enforces that.

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
Offline tests: `python3 tests/test_probe_bot_access.py` (23 assertions, no network).

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
