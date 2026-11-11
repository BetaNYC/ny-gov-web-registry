---
title: The address inventory
created: 2026-11-10
type: reference
status: active
tags: [ai-readability, addresses, inventory]
---

# The address inventory

`data/ai-readability/addresses.csv` lists every address the next AI-readability run should scan:
main sites, sub-sites, sections, data portals and APIs (issue #41, epic #47). It is built by
`scripts/address_inventory.py`. The scanner does not read it yet; that is issue #44.

**Nothing is guessed.** No `data.`, `gis.` or `api.` prefix is ever tried on a domain. Every
address comes from a list, from a file the earlier scan already fetched, or from a public
certificate log, and a certificate-log name is listed only after it answered.

The file holds addresses only. No scan result, status or verdict is written into it.

## Columns

| Column | Meaning |
|---|---|
| `address` | The URL to scan, as its source wrote it (a portal URL's `#fragment` is dropped) |
| `owning_body_id` | The body's id in `data/registry.json`; several are joined with `; `; blank when the source names a body the registry does not hold |
| `owning_body` | The body's name, as the source gives it (or the registry's name for the id) |
| `kind` | `main site`, `sub-site`, `section`, `data portal` or `API` (below) |
| `level_or_entity_type` | The registry's `government_level` and the levels other lists give (`websites.json` `ny_levels`), or the portal registry's `entity_type` (`nonprofit`, `research-center`, `library`, `university`, `county`, ...) |
| `source` | Which source named it first; later sources that name the same address are added after `; ` |
| `date_found` | When the source was read: 2026-11-06 for `websites.json` and the earlier scan, 2026-11-10 for the portal registry and the contact files, the `checked` date for the deep-page list, the answer date for certificate logs |
| `notes` | Why a row looks the way it does: a moved page, `vendor-hosted`, `portal registry status UNCONFIRMED`, the domain a certificate-log name was found under |
| `platform` | For data portal registry rows only: the registry's own `platform` words, copied as written (for example `Socrata (Tyler Technologies)`, `ArcGIS Hub`). Empty for every other row. The portal tests (D5–D7) use it when the homepage does not name a platform |

One row per address. Two addresses are the same when they differ only by `www.`, a trailing
`/`, the scheme or a `#fragment`.

## Kinds

| Kind | Rule |
|---|---|
| `main site` | A host a list names as a body's website |
| `sub-site` | A host below another listed host of the same body (`able.dot.ny.gov` under `dot.ny.gov`), or a host found on the same registered domain (fetched files, certificate logs) |
| `section` | A page on a host: a page a list names (`ny_page_urls`), a deep page, a community board's page |
| `data portal` | A portal registry row that is a page for people |
| `API` | A portal registry row that is a machine endpoint: an ArcGIS `/rest/services` directory, a `.json` file, or the Legistar Web API (`webapi.legistar.com`, the NYC Council's second address, vendor-hosted) |

When the portal registry names an address another source already listed (`data.ny.gov`), the
row takes the portal's kind and adds its `entity_type`.

## Sources, in order

1. **`websites.json`** — every host row without a host issue (hosts in
   `website-corrections.csv` are left out, since they are no longer the body's site), and every
   page in `ny_page_urls`. A page that `page-corrections.csv` marks gone is dropped; a moved
   page is listed at its new address, with the old one in `notes`.
2. **The deep-page list** — `deep-pages-extra.csv`, 37 community-board pages on `www.nyc.gov`.
3. **The data portal registry** — `data/ny-data-portal-registry.json` in
   [BetaNYC/ny-data-registry-mcp](https://github.com/BetaNYC/ny-data-registry-mcp), commit
   `3e9e7aa`, copied to `sources/2026-11-10/`. Rows with status `live` (89) or `UNCONFIRMED`
   (11): 100 rows on 97 hosts. Of the 97 hosts, 15 were already in `websites.json`; the 82 new
   ones are 32 sub-sites of governments already listed, 19 on ArcGIS domains, 23 run by a
   nonprofit, research center, library or university, and 8 government sites on their own
   domains (`511ny.org`, `myrts.com`, `compstat.nypdonline.org`, `webcams.nyctmc.org`,
   `checkbooknyc.com`, `rocklandgis.com`, `webapi.legistar.com`, and `catalog.data.gov`, the
   federal catalog's New York page). All 82 are added to `websites.json` too (below).
4. **Linked from fetched files** — sub-sites the earlier scan (2026-11-06, office) already
   fetched, with no new request: a `Sitemap:` line in a robots.txt, or a homepage redirect,
   that points at another host on the same registered domain. Vendor platforms and
   infrastructure names (the certificate-log filter, below) are left out. The owner is the
   owner of the listed host, which holds the domain; a sub-site may serve a partner body.
   **Two parts of this source were skipped:** the scan did not store the links on each
   homepage, and it stored a hash of each sitemap but not the URLs in it, so neither could be
   read without fetching again. Only the address and the listed host are kept from the run
   (`sources/2026-11-10/linked-from-fetched-files.csv`).
5. **BetaNYC's contact files** — the `Website:` line of BetaNYC's agency, elected-office and
   community-board files, when the address is not already listed. Only the address and the
   registry id are copied (`sources/2026-11-10/contact-websites.csv`). Today only the
   community-board files and one Council district file carry a `Website:` line.
6. **Certificate logs** — see below. Written to `cert-log-addresses.csv`
   (`address`, `registered_domain`, `found`) by `scripts/cert_log_names.py --export`.

## Count of addresses per source

Built 2026-11-10. Counted by first source.

| Source | Addresses |
|---|---|
| websites.json | 3,815 |
| deep-page list | 37 |
| data portal registry | 95 |
| linked from fetched files | 6 |
| BetaNYC contact files | 11 |
| certificate logs | pending (second PR) |
| **Total** | **3,964** |

By kind: main site 3,178 · sub-site 136 · section 552 · data portal 90 · API 8.

`python3 scripts/address_inventory.py` prints both counts.

## The portals in `websites.json`

`scripts/ai_readability_websites.py` reads the portal registry snapshot as a sixth source
(`source_list_portal_registry`). New columns: `ny_kind` (`main site`, `data portal`, `API`),
`ny_government`, `ny_portal_entity_types`, `ny_portal_registry_ids`, `ny_portal_urls` and
`ny_hosting` (`vendor` for hosts on arcgis.com or legistar.com). Definitions are in
`target-list-dictionary.csv`.

**Non-government portals are never counted as government.** `ny_government` is FALSE when the
portal registry is the only source for a host and every portal row on it has `entity_type`
`nonprofit`, `research-center`, `library` or `university`. The registry's `entity_type` is kept
as written. A portal URL goes in `ny_portal_urls`, never `ny_page_urls`, so the deep-page run
(#35) is unchanged.

## Certificate logs

`scripts/cert_log_names.py` (approved by Noel, 2026-11-10).

**Which domains.** The registered domain of every `websites.json` host without a host issue,
when the body owns it: a `.gov` or `.ny.us` domain, or a host that is the domain itself (or
`www.` plus it). Shared platforms (wordpress.com, wixsite.com, yourtasc.com, arcgis.com,
legistar.com and the others in `VENDOR_DOMAINS`) and non-government portals are never queried.

**One query per domain** to `https://crt.sh/?q=%25.<domain>&output=json`, at least 6 seconds
apart, backing off (30, 90, 240, 600 seconds) on 429, 5xx and timeouts. A domain that still
fails is recorded and skipped. Raw answers are cached outside git, so a rerun never asks twice.

**The filter.** A name is kept for checking only when:

- it is the domain or ends in `.<domain>`;
- it is not a wildcard (`*`);
- no label below the domain is infrastructure: a label that is one of these words, or one of
  them followed by digits (`mail2`) or by `-` and more (`vpn-east`): `mail`, `email`, `webmail`, `smtp`, `imap`, `pop`, `pop3`,
  `mx`, `owa`, `exchange`, `autodiscover`, `autoconfig`, `lyncdiscover`, `lync`, `sip`,
  `msoid`, `enterpriseenrollment`, `enterpriseregistration`, `vpn`, `sslvpn`, `remote`,
  `rdweb`, `rdgateway`, `rds`, `citrix`, `gateway`, `fw`, `firewall`, `anyconnect`,
  `globalprotect`, `cpanel`, `whm`, `webdisk`, `cpcalendars`, `cpcontacts`, `webmin`, `plesk`,
  `ftp`, `sftp`, `ns`, `dns`, `adfs`, `sts`, `mdm`, `localhost`, `login`, `signin`, `sso`,
  `dev`, `test`, `testing`, `stage`, `staging`, `uat`, `qa`, `sandbox`, `demo`, `click`,
  `track`, `trk`, `links`, `mta-sts`, `_dmarc`, `_domainkey`; and no label contains `staging`;
- it is not already a host in `websites.json`.

**The answer.** Each name gets **one** GET to `https://<name>/` with the honest research user
agent (`research_betanyc` in `scripts/probe_bot_access.py`), redirects not followed, TLS
verified. One request per name, so no host is asked twice (the 30-second rule holds). It is
listed when it answers:

- 2xx, 401 or 403 with an HTML or API content type (`text/html`, `application/xhtml+xml`,
  JSON, XML); or
- a redirect to a path on the same host.

It is dropped when it does not answer (no DNS, refused, timeout, a TLS error), answers with
another status, redirects to another host (an alias of an address listed elsewhere), or serves
another content type. The answers stay outside git; only the listed address is published.

```
nohup python3 scripts/cert_log_names.py >> ~/tmp/g1-crtsh/run.log 2>&1 &
python3 scripts/cert_log_names.py --export data/ai-readability/cert-log-addresses.csv
python3 scripts/address_inventory.py
```

## Rebuilding

```
python3 scripts/ai_readability_websites.py            # websites.csv / websites.json
python3 scripts/ai_readability_websites.py --write-dictionary
python3 scripts/address_inventory.py                  # addresses.csv
# the private inputs, only when they change:
python3 scripts/address_inventory.py --contacts <BetaNYC workspace>/team/contacts \
    --fetched <private run folder>
```

Offline tests: `tests/test_address_inventory.py`.
