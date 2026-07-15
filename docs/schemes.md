# Scheme catalog

Every external key in a record's `identifiers[]` is a `{scheme, identifier}` pair, and every
geographic reference in `areas[]` is a `{scheme, layer, id, role}`. This catalog documents each
scheme's **authority** (who mints the value), **format** (its shape), and **verification rule**
(how a contributor confirms a value before adding it).

**Schemes are added here, not in the schema.** A new upstream becomes a new row in this file — the
JSON Schema does not enum-lock scheme names, so adding one is never a breaking change. Values are
populated **only when verified** against the named authority — never invented (see `CONTRIBUTING.md`).

## `id` syntax (not a scheme — the record's own key)

The native `id` is an opaque, stable, steward-neutral slug (`^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`). It is
**configuration**: a fork may choose a different opaque convention. The steward's identity lives in
`provenance` and stewardship docs, never in the id syntax. The retired `BNYC-ORG-NNNNNN` key is not the
native id — it is carried as an identifier under `betanyc_org_legacy` (below).

## Identifier schemes

| Scheme | Authority | Format | Verification rule |
|---|---|---|---|
| `nyc_goid` | NYC Mayor's Office of Data Analytics — canonical dataset NYC Open Data `t3jq-9nkf` / [MODA-NYC/nyc-governance-organizations](https://github.com/MODA-NYC/nyc-governance-organizations) | 6-digit numeric string in Phase II (`NYC_GOID_XXXXXX` in Phase I); stored verbatim | Match the entity's name (and `url` where present) to the `record_id` row in `t3jq-9nkf`. |
| `wikidata` | [Wikidata](https://www.wikidata.org) | QID, `^Q[0-9]+$` | The QID's item **is this entity** — not merely a place it sits in. **Attached only via a distinctive `P856` official-website domain match (host owned by exactly one entity) or explicit operator confirmation — never on a name/alias match alone** (name matches are recorded as review proposals). **Place-vs-organization guard:** a domain match alone is *not* sufficient when the P856 item's class is a place/geographic entity (a borough, county) rather than the office/organization — Wikidata routinely lists an office's site as the P856 of the *place*. Operator re-point directives (`data/curation.json § wikidata_repoints`) override such matches, dropping the place QID (or swapping in a verified office QID). Apex-shared hosts (`nyc.gov`) are not a match. See `scripts/sync_wikidata.py` and `docs/freshness.md § Wikidata`. |
| `us_irs_ein` | US Internal Revenue Service | 9-digit EIN, conventionally `NN-NNNNNNN` | Confirm against IRS Tax Exempt Organization Search / a filed Form 990 for the entity. |
| `nys_abo` | NYS Authorities Budget Office — [Directory of Public Authorities](https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x) | **Unconfirmed** — the public `4vym-q77x` dataset exposes **no stable id column** (see `docs/sources.md`). Reserved; populate only from an ABO source that carries a stable id (e.g. PARIS). | Confirm the id is stable and ABO-issued before use; leave absent otherwise. |
| `socrata_dataset` | The hosting Socrata portal (e.g. NYC Open Data, data.ny.gov) | Socrata four-by-four dataset id, `xxxx-xxxx` | The dataset resolves on its portal and is the entity's own dataset. |
| `archive_org` | Internet Archive | archive.org item/collection identifier (the `<id>` in `archive.org/details/<id>`) | The identifier resolves and is a dedicated IA item/collection for the entity. |
| `archive_it_collection` | Internet Archive — Archive-It | numeric Archive-It collection id (`archive-it.org/collections/<id>`) | The collection resolves and captures the entity's sites. |
| `snac_ark` | [SNAC](https://snaccooperative.org) (Social Networks and Archival Context) | ARK, `ark:/99166/…` | The ARK resolves to the entity's SNAC constellation. |
| `viaf` | OCLC [VIAF](https://viaf.org) | numeric cluster id (`viaf.org/viaf/<id>`) | The VIAF cluster is this entity. |
| `lcnaf` | Library of Congress Name Authority File | LCCN-style id, e.g. `n79021164` (`id.loc.gov/authorities/names/<id>`) | The authority record resolves and names this entity. |
| `betanyc_org_legacy` | **Legacy — this registry (historical).** | `BNYC-ORG-NNNNNN` | Carried verbatim from the v1 seed during the v1→v2 migration. **Not minted for new records** — new records get a neutral `id` only. |

Authority-file schemes (`snac_ark`, `viaf`, `lcnaf`) support the archival-interoperability commitment
(`docs/eac-cpf-crosswalk.md`); IA schemes (`archive_org`, `archive_it_collection`) complement
`web_properties[]` as the Wayback/CDX join surface.

## Geographic schemes (`areas[]`)

| Scheme | Authority | Layer / id | Verification rule |
|---|---|---|---|
| `nyc-boundaries` | [BetaNYC/nyc-boundaries](https://github.com/BetaNYC/nyc-boundaries) | `layer` = the map's published `BoundaryId` (see the full list below); `id` = a district id within the layer, or `null` for a whole-layer reference (an entity that operates the entire layer) | The `layer` is a member of the extracted vocabulary (`data/nyc_boundaries_layers.json`); the build **rejects** an unknown layer id. The `{layer, id}` exists in nyc-boundaries. **No geometry is copied** — the map is the single home for boundaries. |
| `us_census_geoid` | US Census Bureau | `layer` = summary-level name (e.g. `county`, `place`, `state`); `id` = the GEOID | The GEOID resolves in Census TIGER / the Gazetteer for the named summary level. NYC county GEOIDs (state FIPS `36` + 3-digit county): `36061` New York/Manhattan, `36005` Bronx, `36047` Kings/Brooklyn, `36081` Queens, `36085` Richmond/Staten Island. |

**`nyc-boundaries` layer vocabulary (the `BoundaryId` union, extracted 2026-07-15).** The authority
is the Boundaries Map's own `frontend/src/assets/boundaries/index.ts`. `scripts/sync_boundaries.py`
parses that file (placed at `data/cache/nyc-boundaries_layers_index.ts`) and emits the committed
`data/nyc_boundaries_layers.json`; the build validates every `{scheme:"nyc-boundaries"}` `areas[]`
layer against it. The 22 published ids:

`bid`, `cc`, `cd`, `cdta`, `dsny`, `ed`, `fb`, `hc`, `hd`, `ibz`, `mc`, `nda`, `nta`, `nycongress`,
`pp`, `ps`, `puma`, `sa`, `sd`, `ss`, `uhf`, `zipcode`.

**Which entities carry which layer** is an operator-curated assertion in
`data/boundaries_mapping.json` (hand-authored, parallel to `data/curation.json`), applied by
`build_registry.apply_boundaries_mapping`. `operates_layer` refs (whole-layer, `id:null`) go to the
administering agency — NYPD→`pp`+`ps`, DSNY→`dsny`, FDNY→`fb`, DOE→`sd`, City Council→`cc`, Community
Boards→`cd`, Board of Elections→`ed`. The 5 borough-president offices get `jurisdiction` refs under
`us_census_geoid` (`county`) because nyc-boundaries has **no borough layer**. The mapping file also
records, for coverage, every layer that was *not* attached and why (`defines_not_operates` for
statistical/reporting geographies like `nta`/`cdta`/`hc`/`uhf` that DCP/DOHMH *define* but do not
operate as a governed district system; `no_registry_entity` for state/federal layers whose operator
is not in this NYC registry — `sa`/`ss`/`nycongress`/`puma`/`zipcode`/`mc`; `deferred` for
`bid`/`hd`/`ibz`/`nda`). Statistical geographies are **defined**, not **operated** — `operates_layer`
is deliberately not stretched to cover them.

If nyc-boundaries adds a layer, re-run `sync_boundaries.py` and this catalog gains a layer value —
no schema change. `us_census_geoid` is documented so the geographic axis generalizes beyond one
city's map.
