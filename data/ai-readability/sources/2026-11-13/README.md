---
title: Source snapshots for the counties, cities, towns and villages, 2026-11-13
created: 2026-11-13
type: reference
status: active
tags: [registry, sources, snapshot, nys, localities]
---

# Source snapshots, 2026-11-13

Inputs to `scripts/sync_nys_localities.py` (issue #23, epic #58): the counties, cities, towns and
villages of New York. Each read was authorized by Noel for 2026-11-13, once each. All requests
sent the User-Agent `BetaNYC-registry-research/1.0` from the `office` machine. Copies of these
files go in `data/cache/`, which the sync scripts read; nothing here is fetched by a script.

| File | Source |
|---|---|
| `osc-entity-tables-general-purpose.json` | OSC Local Government Entities, `entity-tables.xls`: the Counties, Cities, Towns and Villages sheets and the general-purpose rows of the Summary Table, extracted by `scripts/extract_osc_entities.py`. Copy to `data/cache/osc_entity_tables.json` |
| `grounding-ny-data-portal-registry-localities.json` | The 119 city and county rows of BetaNYC's NY data portal registry (`resources/ny-data-portal-registry.json` in [BetaNYC/grounding-ai-with-ny-open-data](https://github.com/BetaNYC/grounding-ai-with-ny-open-data) at `6106598`, the merge of PR #35). Fields kept: entity, entity_type, region, url, status, platform, last_verified. Copy to `data/cache/grounding_ny_data_portal_localities.json` |

Also used, from the 2026-11-06 folder (not refetched): `its-locality-websites-55k6-h6qq.json`
(copy to `data/cache/its_55k6-h6qq.json`) and `cisa-dotgov-current-full.csv` (copy to
`data/cache/cisa_dotgov_current-full.csv`).

## OSC entity tables

- **URL:** https://web.osc.state.ny.us/localgov/web-entity-map/excel/entity-tables.xls
- **Fetched:** 2026-11-13T23:26:26Z (6:26 pm ET), one GET, HTTP 200, 2,090,496 bytes,
  `Last-Modified: Mon, 02 Nov 2026 16:47:52 GMT`
- **SHA-256 of the .xls:** `78bc96e787d76a81c89c88ca7bed881cbce4b6dfdde67eb8ef4a42100669d4c9`
- **Rows:** 57 counties, 62 cities (includes NYC), 933 towns, 532 villages. These match the
  workbook's own Summary Table ("Counts as of 2026") and `docs/sources-statewide.md`.
- **Why only an extract is committed:** the workbook contains a very hidden Excel 4.0 macro
  sheet (`Macro1`, with an `Auto_Open` name). The extract reads cell values only; the raw
  workbook stays in `data/cache/osc_entity-tables.xls` and is not put in git. To rebuild the
  extract from a fresh download:

  ```
  python scripts/extract_osc_entities.py --fetched <UTC time> --last-modified "<header>" \
      --out data/cache/osc_entity_tables.json
  ```

## NYS Civil Boundaries GIS: not read (service down)

- **Service:** https://gisservices.its.ny.gov/arcgis/rest/services/NYS_Civil_Boundaries/FeatureServer
- **Attempts, 2026-11-13 between 23:25Z and 23:36Z:** the service description
  (`?f=json`), the `layers` resource, layers 0 to 2, and one `returnCountOnly` query on layer 2
  each returned HTTP 200 with the body `{"error":{"code":500,...}}` ("Error performing query
  operation" for the query). The MapServer of the same name answered
  `"Service NYS_Civil_Boundaries/MapServer not started"`, and the server's services directory
  (`/arcgis/rest/services?f=json`) lists no services outside its `Locators` and `Utilities`
  folders. The service was answering on 2026-11-06.
- **No snapshot was saved and no other source was substituted.** The GIS layers are the spine
  for who exists, so the localities are not minted until this read succeeds.
- **When it is back:** one attribute-only pass per layer (Counties, Cities, Towns, Villages):
  `query?where=1%3D1&outFields=*&returnGeometry=false&f=json`, paged with `resultOffset` and
  `resultRecordCount` (both apply only when the layer reports `supportsPagination`; see the
  [ArcGIS REST query reference](https://developers.arcgis.com/rest/services-reference/enterprise/query-feature-service-layer/)).
  Save each layer's pages here as `nys-gis-<layer>.json` (a JSON list of the raw responses), copy
  them to `data/cache/nys_gis_<layer>.json`, and record the fetch time and row counts in this
  file. Expected rows: 62 counties (5 flagged `NYC`), 62 cities, 933 towns, 532 villages.
