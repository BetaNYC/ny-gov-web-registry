"""sync_nys_localities.py — New York's counties, cities, towns and villages as registry records
(growth plan steps 3-6, docs/registry-growth-plan.md).

Who exists (the spine): the NYS Civil Boundaries GIS service (NYS ITS GIS Program Office),
    https://gisservices.its.ny.gov/arcgis/rest/services/NYS_Civil_Boundaries/FeatureServer
    layers Counties, Cities, Towns, Villages. Attribute queries only (returnGeometry=false).
    Fields read (documented in docs/sources-statewide.md § 3, from the service on 2026-11-06):
        all layers         NAME, SWIS, GNIS_ID, FIPS_CODE
        Counties           NYC   ('Y' marks the five NYC counties, which are not minted)
        Cities/Towns/Vill. COUNTY
    A layer missing one of these fields, or whose row count differs from the published count
    (62 / 62 / 933 / 532), is an error: the script stops rather than mint from a changed source.
    Input shape: the ArcGIS REST query response JSON ({"fields": [...], "features":
    [{"attributes": {...}}]}), one object or a list of paged responses per layer.

Cross-check and OSC id: OSC Local Government Entities (entity-tables.xls), extracted to JSON by
    scripts/extract_osc_entities.py. A GIS place takes an OSC row's 12-digit Municipal Code
    (`nys_osc_muni_code`) only when county AND normalized name match exactly one row each way.
    Anything else goes to the review file and nothing is applied.

Website leads (never a web property on their own):
    its       ITS "Locality Hierarchy with Websites" 55k6-h6qq (rows 2023-03-06), joined by SWIS
    dotgov    CISA .gov list via scripts/sync_dotgov.py, joined by tier + name
    grounding hand-checked city and county rows of BetaNYC/grounding-ai-with-ny-open-data
              resources/ny-data-portal-registry.json. A row's url is often a data portal or a
              vendor (ArcGIS Hub, Schneider Beacon, ...), not the government's site, so a row is a
              lead only when its host is a registrable domain or `www.` + one (Public Suffix
              List). A portal subdomain (data., gis., maps., *.hub.arcgis.com) is never a lead.
    Rule (growth plan § Finding and verifying websites, rule 2): a host becomes `primary` only
    when two different sources name it. A single lead, disagreeing leads, a host led for more than
    one place, or a host an existing entity already owns go to data/statewide_web_review.json §
    localities. A lead with a path (a page on someone's site) is a path-only presence; it is
    proposed in review as a url_conventions[] entry, not applied, because every such lead here is
    single-source. No lead is live-checked (the optional live check is not authorized).

Ids (growth plan § Ids): `albany-county`, `city-of-albany`, `town-of-colonie`,
`village-of-menands`. When two places in one tier share a name, EVERY member gets the county slug
appended (`town-of-greenville-greene-county`). The id is passed to the build as `_mint_id`.

NYC: the five NYC counties and the City of New York are skipped (the borough presidents and the
existing NYC entities already stand for them). Dissolved villages that only the stale ITS list
still carries are never minted: GIS is the spine.

INPUT (never fetches; all in data/cache/):
    nys_gis_counties.json, nys_gis_cities.json, nys_gis_towns.json, nys_gis_villages.json
    osc_entity_tables.json             (scripts/extract_osc_entities.py)
    its_55k6-h6qq.json                 (copy of the 2026-11-06 snapshot)
    leads_dotgov.json                  (scripts/sync_dotgov.py)
    grounding_ny_data_portal_localities.json   (copy of the 2026-11-13 snapshot)
    records_moda.json, records_abo.json, records_nygov.json   (the registry this checks against)
OUTPUT:
    data/cache/records_localities.json            (read by build_registry.py)
    data/statewide_web_review.json § localities   (committed; nothing in it is applied)
    data/ai-readability/localities-website-leads-2026-11-13.csv   (one row per lead host, for every
        place without a primary site; the AI-readability scan checks those hosts, this script does not)

SPINE (`--spine`): `osc` (default since 2026-11-13, Noel's decision while the GIS service is
down): OSC entity tables are who exists (57 / 61 / 933 / 532 outside NYC), and SWIS, GNIS and
county FIPS come from the matched ITS 55k6-h6qq row (type + county + normalized name, one-to-one
both ways; a code ITS gives to two places, or a SWIS curated as stale, is not attached). `gis`:
the NYS Civil Boundaries layers, once a snapshot exists; GIS then confirms or replaces the codes.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build_registry as br  # noqa: E402
import statewide_common as sc  # noqa: E402

CACHE = sc.CACHE
GIS_FILES = {t: CACHE / f"nys_gis_{t}.json" for t in ("counties", "cities", "towns", "villages")}
OSC = CACHE / "osc_entity_tables.json"
ITS = CACHE / "its_55k6-h6qq.json"
DOTGOV = CACHE / "leads_dotgov.json"
GROUNDING = CACHE / "grounding_ny_data_portal_localities.json"
OUT = CACHE / "records_localities.json"
LEADS_CSV = sc.ROOT / "data" / "ai-readability" / "localities-website-leads-2026-11-13.csv"

TIERS = ("county", "city", "town", "village")
LAYER = {"county": "counties", "city": "cities", "town": "towns", "village": "villages"}
# Published counts (sources-statewide.md: GIS 62/62/933/532 incl. NYC's five counties and NYC).
EXPECTED_GIS_ROWS = {"county": 62, "city": 62, "town": 933, "village": 532}
EXPECTED_NYC = {"county": 5, "city": 1}
REQUIRED_FIELDS = {"county": ("NAME", "SWIS", "GNIS_ID", "FIPS_CODE", "NYC"),
                   "city": ("NAME", "SWIS", "GNIS_ID", "FIPS_CODE", "COUNTY"),
                   "town": ("NAME", "SWIS", "GNIS_ID", "FIPS_CODE", "COUNTY"),
                   "village": ("NAME", "SWIS", "GNIS_ID", "FIPS_CODE", "COUNTY")}
ITS_TYPE = {"County": "county", "City": "city", "Town": "town", "Village": "village"}
GROUNDING_TYPE = {"city": "city", "county": "county"}

BUILD_DATE = "2026-11-13"
LABEL = {
    "its": "ITS Locality Hierarchy with Websites (data.ny.gov 55k6-h6qq, rows 2023-03-06)",
    "dotgov": "CISA .gov domain list (current-full.csv, snapshot 2026-11-06)",
    "grounding": "BetaNYC NY data portal registry, hand-checked city/county row "
                 "(grounding-ai-with-ny-open-data @ 6106598, 2026-11-13)",
}
PROVENANCE = {"its": "its_localities", "dotgov": "dotgov"}  # grounding has no provenance value

_INDEX_PATH_RE = re.compile(r"^/(?:(?:index|default|home)(?:\.[a-z]{2,5})?/?)?$", re.IGNORECASE)
_PREFIX_RE = re.compile(r"^(?:(?:county|city|town|village)\s+of\s+)+", re.IGNORECASE)


# --- names ---------------------------------------------------------------------------------

def name_key(s: str | None) -> str:
    """Comparison key for a place name: tier prefixes and a trailing 'County' dropped, 'Saint'
    -> 'st', a leading 'the' dropped, then every non-alphanumeric removed (so 'De Ruyter' ==
    'DeRuyter', 'Le Roy' == 'LeRoy', 'St. Lawrence' == 'St Lawrence', 'Mt' == 'Mount')."""
    s = (s or "").strip().lower().replace("&", " and ")
    s = _PREFIX_RE.sub("", s)
    s = re.sub(r"\s+county$", "", s)
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\bmt\b\.?", "mount", s)
    s = re.sub(r"^the\s+", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def official_name(tier: str, name: str) -> str:
    name = re.sub(r"\s+", " ", name.strip())
    return f"{name} County" if tier == "county" else f"{tier.capitalize()} of {name}"


def _digits(v, width: int | None = None) -> str | None:
    """A code field as a digit string. Integers are left-padded to `width` (SWIS is 6 digits;
    an integer field would have dropped the leading zero of counties 01-09)."""
    if v is None or v == "":
        return None
    s = str(int(v)) if isinstance(v, (int, float)) else str(v).strip()
    if width and s.isdigit() and len(s) < width:
        s = s.zfill(width)
    return s if s.isdigit() else None


# --- inputs --------------------------------------------------------------------------------

def gis_rows(doc) -> list[dict]:
    """Attributes of an ArcGIS query response, or of a list of paged responses."""
    pages = doc if isinstance(doc, list) else [doc]
    rows = []
    for p in pages:
        if "error" in p:
            raise ValueError(f"GIS response is an error object: {p['error']}")
        rows.extend(f["attributes"] for f in p.get("features", []))
    return rows


def gis_units(gis: dict[str, list[dict]], expected: dict[str, int] = EXPECTED_GIS_ROWS) -> tuple[list[dict], dict]:
    """GIS rows -> place units (NYC skipped). Raises on a missing field or a count mismatch.
    `expected` is the published row count per layer; tests pass their fixture's counts."""
    counts: dict = {}
    for tier in TIERS:
        rows = gis[tier]
        if len(rows) != expected[tier]:
            raise ValueError(f"GIS {LAYER[tier]}: {len(rows)} rows, expected {expected[tier]}")
        for f in REQUIRED_FIELDS[tier]:
            if any(f not in r for r in rows):
                raise ValueError(f"GIS {LAYER[tier]}: field {f!r} missing (documented fields changed?)")

    counties = gis["county"]
    flags = {r["NYC"] for r in counties}
    if not flags <= {"Y", "N"}:
        raise ValueError(f"GIS counties: NYC flag values {sorted(flags)} are not Y/N")
    county_name = {name_key(r["NAME"]): r["NAME"].strip() for r in counties}
    county_fips = {name_key(r["NAME"]): _digits(r["FIPS_CODE"]) for r in counties}
    nyc_counties = {name_key(r["NAME"]) for r in counties if r["NYC"] == "Y"}
    nyc_city = [r for r in gis["city"] if name_key(r["NAME"]) in ("newyork", "newyorkcity")]
    if len(nyc_counties) != EXPECTED_NYC["county"] or len(nyc_city) != EXPECTED_NYC["city"]:
        raise ValueError(f"GIS: found {len(nyc_counties)} NYC counties and {len(nyc_city)} NYC city "
                         "rows; expected 5 and 1")

    units = []
    for tier in TIERS:
        for r in gis[tier]:
            if tier == "county" and r["NYC"] == "Y":
                continue
            if tier == "city" and r is nyc_city[0]:
                continue
            swis, gnis = _digits(r["SWIS"], 6), _digits(r["GNIS_ID"])
            if not swis or len(swis) != 6:
                raise ValueError(f"GIS {LAYER[tier]} {r['NAME']!r}: SWIS {r['SWIS']!r} is not 6 digits")
            if tier == "county":
                county, ck = r["NAME"].strip(), name_key(r["NAME"])
            else:
                ck = name_key(r["COUNTY"])
                if ck not in county_name:
                    raise ValueError(f"GIS {LAYER[tier]} {r['NAME']!r}: COUNTY {r['COUNTY']!r} is not a "
                                     "county in the Counties layer")
                if ck in nyc_counties:
                    raise ValueError(f"GIS {LAYER[tier]} {r['NAME']!r} sits in an NYC county")
                county = county_name[ck]
            units.append({"tier": tier, "place": r["NAME"].strip(), "county": county,
                          "swis": swis, "its_swis": swis, "gnis": gnis, "fips": _digits(r["FIPS_CODE"]),
                          "county_fips": county_fips[ck], "spine": "nys_gis"})
    counts["units"] = {t: sum(u["tier"] == t for u in units) for t in TIERS}
    if len({u["swis"] for u in units}) != len(units):
        raise ValueError("GIS: a SWIS code repeats across rows")
    gnis = [u["gnis"] for u in units if u["gnis"]]
    if len(set(gnis)) != len(gnis):
        # build_registry matches on any shared identifier, so a repeat would merge two places.
        raise ValueError("GIS: a GNIS_ID repeats across rows")
    return units, counts


def assign_ids(units: list[dict], taken: set[str]) -> None:
    """Set u['name'] and u['id'] (growth plan § Ids). Mutates the units."""
    groups: dict[str, list[dict]] = {}
    for u in units:
        u["name"] = official_name(u["tier"], u["place"])
        groups.setdefault(br._slugify(u["name"]), []).append(u)
    for slug, members in groups.items():
        for u in members:
            u["id"] = slug if len(members) == 1 else f"{slug}-{br._slugify(u['county'])}-county"
            u["name_collision"] = len(members) > 1
    ids = [u["id"] for u in units]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"ids still collide after the county suffix: {dup}")
    clash = sorted(set(ids) & taken)
    if clash:
        raise ValueError(f"locality ids already used by existing entities: {clash}")


# --- OSC -----------------------------------------------------------------------------------

def match_osc(units: list[dict], osc: dict, review: list[dict]) -> None:
    """Attach u['osc'] (the single matching OSC row) where county + name match one-to-one."""
    def key(tier, county, name):
        return (tier, name_key(county), name_key(name))

    osc_by_key: dict[tuple, list[dict]] = {}
    for tier in TIERS:
        for row in osc[LAYER[tier]]:
            county = row["name"] if tier == "county" else row["county"]
            osc_by_key.setdefault(key(tier, county, row["name"]), []).append(row)
    units_by_key: dict[tuple, list[dict]] = {}
    for u in units:
        units_by_key.setdefault(key(u["tier"], u["county"], u["place"]), []).append(u)

    used: set[str] = set()
    for k, members in units_by_key.items():
        rows = osc_by_key.get(k, [])
        for u in members:
            if len(members) == 1 and len(rows) == 1:
                u["osc"] = rows[0]
                used.add(rows[0]["municipal_code"])
            elif not rows:
                review.append(_item(u, "no_osc_match", note="No OSC row with this county and name."))
            else:
                review.append(_item(u, "osc_match_ambiguous",
                                    osc_rows=[{"municipal_code": r["municipal_code"], "name": r["name"],
                                               "county": r["county"]} for r in rows],
                                    note=f"{len(members)} GIS place(s) and {len(rows)} OSC row(s) share "
                                         "this county and name; no code applied."))
    for tier in TIERS:
        for row in osc[LAYER[tier]]:
            if row["municipal_code"] not in used and not (
                    tier == "city" and name_key(row["name"]) in ("newyork", "newyorkcity")):
                review.append({"tier": tier, "reason": "osc_row_not_in_gis",
                               "osc_row": {"municipal_code": row["municipal_code"], "name": row["name"],
                                           "county": row["county"]}})


# --- websites ------------------------------------------------------------------------------

def _grounding_host_ok(host: str, psl) -> bool:
    import ai_readability_export as ex
    bare = host.removeprefix("www.")
    return bool(bare) and ex.base_domain(bare, psl) == bare


def collect_leads(units: list[dict], its: list[dict], dotgov: list[dict], grounding: list[dict],
                  review: list[dict], tlds: set[str] | None, psl, report_unjoined_its: bool = True) -> None:
    """Attach u['leads'] = [{source, host, path, url, detail, ambiguous}]. ITS rows join on the
    SWIS of the ITS row matched to the place (`its_swis`). With the OSC spine, unmatched ITS rows
    are already reported by match_its, so `report_unjoined_its` is False there."""
    by_swis = {u["its_swis"]: u for u in units if u.get("its_swis")}
    by_tier_name: dict[tuple[str, str], list[dict]] = {}
    for u in units:
        u["leads"] = []
        by_tier_name.setdefault((u["tier"], name_key(u["place"])), []).append(u)

    def add(u, source, url, detail, ambiguous=False):
        host, path = sc.split_url(url)
        if not host:
            return
        if not sc.host_is_valid(host, tlds):
            review.append(_item(u, "website_malformed_lead", source=source, url=url))
            return
        if _INDEX_PATH_RE.match(path or "/"):
            path = ""
        u["leads"].append({"source": source, "host": host, "path": path, "url": url,
                           "detail": detail, "ambiguous": ambiguous})

    for r in its:
        tier = ITS_TYPE.get(r.get("type"))
        url = ((r.get("website") or {}).get("url") or "").strip() if isinstance(r.get("website"), dict) \
            else (r.get("website") or "").strip()
        if tier is None:
            continue
        u = by_swis.get(_digits(r.get("swis_code"), 6))
        if u is None or u["tier"] != tier:
            if report_unjoined_its and r.get("county") and not _is_nyc_its(r):
                review.append({"tier": tier, "reason": "its_row_not_in_gis",
                               "its_row": {"swis_code": r.get("swis_code"), "type": r.get("type"),
                                           "name": r.get("municipality"), "county": r.get("county"),
                                           "website": url or None},
                               "note": "Not minted: GIS (the spine) has no place with this SWIS and tier "
                                       "(dissolved, renumbered, or renamed since 2023)."})
            continue
        if url:
            add(u, "its", url, f"SWIS {r.get('swis_code')}")

    for lead in dotgov:
        members = by_tier_name.get((lead["tier"], name_key(lead["name"])), [])
        if not members:
            if not (lead["tier"] == "city" and name_key(lead["name"]) in ("newyork", "newyorkcity")):
                review.append({"tier": lead["tier"], "reason": "dotgov_lead_matches_no_place",
                               "domain": lead["domain"], "organization": lead["organization"]})
            continue
        for u in members:
            add(u, "dotgov", lead["domain"], f"organization {lead['organization']!r}",
                ambiguous=len(members) > 1)

    for row in grounding:
        tier = GROUNDING_TYPE.get(row.get("entity_type"))
        if tier is None or not row.get("url"):
            continue
        members = by_tier_name.get((tier, name_key(row["entity"])), [])
        if len(members) != 1:
            continue  # NYC, or a name that is not unique (none today)
        host, _ = sc.split_url(row["url"])
        if host and _grounding_host_ok(host, psl):
            add(members[0], "grounding", row["url"], f"entity {row['entity']!r}, status {row.get('status')}")


def _is_nyc_its(r: dict) -> bool:
    return r.get("type") == "Borough/County" or (r.get("type") == "City"
                                                 and name_key(r.get("municipality")) in ("newyork", "newyorkcity"))


def decide_websites(units: list[dict], owner: dict[str, str], review: list[dict]) -> None:
    """Apply rule 2 (two sources agree on a host) and route everything else to review.
    Sets u['primary'] = {host, sources} or None, and u['used_sources']."""
    agreed: dict[str, dict] = {}
    root_claims: dict[str, set[str]] = {}
    for u in units:
        hosts: dict[str, set[str]] = {}
        for lead in u["leads"]:
            if lead["path"]:
                continue
            hosts.setdefault(sc.norm_host(lead["host"]), set()).add(lead["source"])
            root_claims.setdefault(sc.norm_host(lead["host"]), set()).add(u["id"])
        both = sorted(h for h, srcs in hosts.items() if len(srcs) >= 2)
        u["hosts"] = hosts
        u["agreed"] = both
        if len(both) == 1:
            agreed[u["id"]] = {"host": both[0], "sources": sorted(hosts[both[0]])}

    for u in units:
        u["primary"] = None
        u["used_sources"] = set()
        leads_out = [{"source": lead["source"], "url": lead["url"], "detail": lead["detail"],
                      **({"name_shared_by_several_places": True} if lead["ambiguous"] else {})}
                     for lead in u["leads"]]
        cand = agreed.get(u["id"])
        if len(u["agreed"]) > 1:
            review.append(_item(u, "website_several_hosts_agreed", leads=leads_out,
                                note="More than one host has two agreeing sources; none applied."))
        elif cand:
            others = sorted(root_claims[cand["host"]] - {u["id"]})
            if others:
                review.append(_item(u, "website_host_shared", host=cand["host"], shared_with=others,
                                    leads=leads_out, note="Two sources agree, but the host is also a lead "
                                    "for another place (a shared or county site); not applied."))
            elif cand["host"] in owner:
                review.append(_item(u, "website_host_owned_by_existing_entity", host=cand["host"],
                                    owned_by=owner[cand["host"]], leads=leads_out))
            else:
                u["primary"] = cand
                u["used_sources"] |= set(cand["sources"])
                extra = sorted(h for h in u["hosts"] if h != cand["host"])
                if extra:
                    review.append(_item(u, "website_additional_leads_not_applied", hosts=extra,
                                        primary=cand["host"], leads=leads_out))
        elif u["hosts"]:
            reason = "website_single_lead" if len({s for v in u["hosts"].values() for s in v}) == 1 \
                else "website_leads_disagree"
            review.append(_item(u, reason, leads=leads_out))
        for lead in u["leads"]:
            if lead["path"]:
                review.append(_item(u, "path_only_presence", source=lead["source"], url=lead["url"],
                                    proposed_url_convention=f"{lead['host']}{lead['path']}",
                                    note="A page on a host, from a single source; not applied."))


# --- records -------------------------------------------------------------------------------

LEADS_CSV_COLUMNS = ("entity_id", "name", "county", "level", "host", "source")


def lead_rows(units: list[dict]) -> list[dict]:
    """One row per (place, lead host, source) for every place WITHOUT a primary site, for the
    AI-readability scan to check. Hosts only: a path-only lead (a page on someone else's host,
    usually the county's) is not a host lead and stays in the review file. Hosts are as the
    source gave them (www. kept). A place with no host lead has no row."""
    rows = []
    for u in units:
        if u["primary"]:
            continue
        seen = set()
        for lead in u["leads"]:
            key = (lead["host"], lead["source"])
            if lead["path"] or key in seen:
                continue
            seen.add(key)
            rows.append({"entity_id": u["id"], "name": u["name"], "county": u["county"],
                         "level": u["tier"], "host": lead["host"], "source": lead["source"]})
    rows.sort(key=lambda r: (r["entity_id"], r["host"], r["source"]))
    return rows


def _item(u: dict, reason: str, **kw) -> dict:
    return {"proposed_id": u.get("id"), "name": u.get("name"), "tier": u["tier"], "county": u["county"],
            "swis": u["swis"], "reason": reason, **kw}


def _areas(u: dict) -> list[dict]:
    """us_census_geoid areas from the GIS FIPS_CODE, only where the code's form is certain:
    counties (5 digits, SSCCC) and towns (10 digits, SSCCCMMMMM county subdivision). Cities and
    villages are left out: see docs/schemes.md § us_census_geoid."""
    f = u["fips"] or ""
    if u["tier"] == "county" and len(f) == 5 and f.startswith("36"):
        return [{"scheme": "us_census_geoid", "layer": "county", "id": f, "role": "jurisdiction"}]
    if u["tier"] == "town" and len(f) == 10 and f[:5] == u["county_fips"]:
        return [{"scheme": "us_census_geoid", "layer": "county_subdivision", "id": f, "role": "jurisdiction"}]
    return []


def to_record(u: dict) -> dict:
    ids = [{"scheme": "nys_swis", "identifier": u["swis"]}] if u["swis"] else []
    if u["gnis"]:
        ids.append({"scheme": "us_gnis", "identifier": u["gnis"]})
    other_names = []
    sources = [u["spine"]]
    if u.get("osc"):
        ids.append({"scheme": "nys_osc_muni_code", "identifier": u["osc"]["municipal_code"]})
        if "osc" not in sources:
            sources.append("osc")
        if u["spine"] == "osc" and (u["swis"] or u["gnis"]):
            sources.append("its_localities")
        osc_name = re.sub(r"^((?:Village|Town|City) of )\1+", r"\1", u["osc"]["name"])
        if br._norm(osc_name) != br._norm(u["name"]):
            other_names.append({"name": osc_name, "note": "Name in the OSC entity tables (2026)."})
    wps = []
    if u["primary"]:
        p = u["primary"]
        raw = next(lead["host"] for src in ("dotgov", "grounding", "its") for lead in u["leads"]
                   if lead["source"] == src and not lead["path"] and sc.norm_host(lead["host"]) == p["host"])
        agreeing = [lead for lead in u["leads"] if not lead["path"] and sc.norm_host(lead["host"]) == p["host"]]
        why = "; ".join(f"{LABEL[lead['source']]}: {lead['url']} ({lead['detail']})" for lead in agreeing)
        wps.append({"domain": raw, "role": "primary", "valid_from": None, "valid_to": None,
                    "notes": f"Auto-accepted {BUILD_DATE}: {len(p['sources'])} independent leads agree on "
                             f"this host [{', '.join(p['sources'])}]. {why}. Not live-checked."})
        sources += [PROVENANCE[s] for s in p["sources"] if s in PROVENANCE and PROVENANCE[s] not in sources]
    return {
        "name": u["name"], "short_name": None, "other_names": other_names,
        "government_level": u["tier"], "classification": None,
        "identifiers": ids, "web_properties": wps, "url_conventions": [],
        "areas": _areas(u), "area_note": None, "status": "active",
        "provenance": {"sources": sources, "last_verified": None},
        "_mint_id": u["id"], "_expect_new": True,
    }


def build_records(gis: dict[str, list[dict]], osc: dict, its: list[dict], dotgov: list[dict],
                  grounding: list[dict], base: list[dict], tlds: set[str] | None = None,
                  psl=None, expected_rows: dict[str, int] = EXPECTED_GIS_ROWS
                  ) -> tuple[list[dict], list[dict], dict]:
    """Pure: source snapshots + the existing registry -> (records, review items, summary)."""
    if psl is None:
        import ai_readability_export as ex
        psl = ex.load_psl()
    units, summary = gis_units(gis, expected_rows)
    review: list[dict] = []
    assign_ids(units, {e["id"] for e in base})
    match_osc(units, osc, review)
    return _finish(units, summary, review, its, dotgov, grounding, base, tlds, psl, report_unjoined_its=True)


def _finish(units, summary, review, its, dotgov, grounding, base, tlds, psl, report_unjoined_its):
    """Shared by both spines: name clash check, website leads and rule, records, summary."""
    base_names = {br._norm(e["name"]): e["id"] for e in base}
    clash = sorted((u["name"], base_names[br._norm(u["name"])]) for u in units if br._norm(u["name"]) in base_names)
    if clash:
        raise ValueError(f"locality names already used by existing entities: {clash}")
    owner = {}
    for e in base:
        for w in e.get("web_properties", []):
            owner.setdefault(sc.norm_host(w["domain"]), e["id"])
    collect_leads(units, its, dotgov, grounding, review, tlds, psl, report_unjoined_its)
    decide_websites(units, owner, review)
    order = {t: i for i, t in enumerate(TIERS)}
    units.sort(key=lambda u: (order[u["tier"]], u["id"]))
    records = [to_record(u) for u in units]
    summary["lead_rows"] = lead_rows(units)
    review.sort(key=lambda i: (order.get(i["tier"], 9), i.get("proposed_id") or "", i["reason"],
                               json.dumps(i, sort_keys=True)))
    summary["osc_matched"] = sum(1 for u in units if u.get("osc"))
    summary["codes"] = {"nys_swis": sum(1 for u in units if u["swis"]),
                        "us_gnis": sum(1 for u in units if u["gnis"]),
                        "nys_osc_muni_code": summary["osc_matched"], "places": len(units)}
    summary["primary_website"] = {t: sum(1 for u in units if u["tier"] == t and u["primary"]) for t in TIERS}
    summary["name_collisions"] = sorted(u["id"] for u in units if u["name_collision"])
    reasons: dict[str, int] = {}
    for i in review:
        reasons[i["reason"]] = reasons.get(i["reason"], 0) + 1
    summary["review_reasons"] = dict(sorted(reasons.items()))
    summary["places_with_a_website_review_item"] = len({i["proposed_id"] for i in review
                                                        if i["reason"].startswith(("website_", "path_only"))})
    return records, review, summary


# --- OSC spine (Noel, 2026-11-13: mint from OSC while the GIS service is down) ----------------

EXPECTED_OSC_UNITS = {"county": 57, "city": 61, "town": 933, "village": 532}  # NYC not counted
_ITS_KEEP = ("swis_code", "type", "municipality", "county", "_2nd_county", "gnis_id", "county_fips")


def osc_place(tier: str, osc_name: str) -> str:
    """'County of St. Lawrence' -> 'St. Lawrence'; 'Village of Village of The Branch' -> 'The Branch'."""
    return re.sub(r"^(?:(?:County|City|Town|Village) of\s+)+", "", osc_name.strip(), flags=re.IGNORECASE).strip()


def osc_units(osc: dict, expected: dict[str, int] = EXPECTED_OSC_UNITS) -> tuple[list[dict], dict]:
    """OSC rows -> place units. NYC (the 'City of New York City' row) is skipped; OSC has no rows
    for NYC's five counties. Raises on a count mismatch or a repeated municipal code."""
    units = []
    for tier in TIERS:
        for row in osc[LAYER[tier]]:
            place = osc_place(tier, row["name"])
            if tier == "city" and name_key(place) in ("newyork", "newyorkcity"):
                continue
            units.append({"tier": tier, "place": place,
                          "county": place if tier == "county" else row["county"], "osc": row,
                          "swis": None, "its_swis": None, "gnis": None, "fips": None,
                          "county_fips": None, "spine": "osc"})
    got = {t: sum(u["tier"] == t for u in units) for t in TIERS}
    if got != expected:
        raise ValueError(f"OSC places {got}, expected {expected}")
    codes = [u["osc"]["municipal_code"] for u in units]
    if len(set(codes)) != len(codes):
        raise ValueError("OSC: a municipal code repeats")
    return units, {"units": got}


def match_its(units: list[dict], its: list[dict], review: list[dict], stale_swis: dict[str, str]) -> dict:
    """Attach ITS codes (SWIS, GNIS, county FIPS) on a single unambiguous match of type + county +
    normalized name, both ways. ITS lists a second county for villages that cross a county line;
    a match on either county counts (it is the ITS row's own data). A SWIS in `stale_swis`
    (curation: known to have changed since 2023) is not attached. Returns match counts."""
    rows = [r for r in its if ITS_TYPE.get(r.get("type")) and not _is_nyc_its(r)]
    index: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows):
        tier = ITS_TYPE[r["type"]]
        name = r.get("county") if tier == "county" else r.get("municipality")
        for c in {r.get("county"), r.get("_2nd_county")} - {None, ""}:
            index.setdefault((tier, name_key(c), name_key(name)), []).append(i)
    keyed: dict[tuple, list[dict]] = {}
    for u in units:
        keyed.setdefault((u["tier"], name_key(u["county"]), name_key(u["place"])), []).append(u)

    claims: dict[int, list[dict]] = {}
    for k, members in keyed.items():
        hits = index.get(k, [])
        for u in members:
            u["_its_hits"] = hits if len(members) == 1 else hits + [-1]  # -1: shared key, ambiguous
            if len(members) == 1 and len(hits) == 1:
                claims.setdefault(hits[0], []).append(u)
    counts = {"matched": 0, "via_second_county": 0, "no_match": 0, "ambiguous": 0, "swis_held_stale": 0}
    used: set[int] = set()

    def brief(r):
        return {k: r.get(k) for k in _ITS_KEEP if r.get(k) is not None}

    for u in units:
        hits = u.pop("_its_hits")
        if len(hits) == 1 and len(claims.get(hits[0], [])) == 1:
            r = rows[hits[0]]
            used.add(hits[0])
            counts["matched"] += 1
            if name_key(r.get("county")) != name_key(u["county"]):
                counts["via_second_county"] += 1
            u["its_swis"] = _digits(r["swis_code"], 6)
            u["gnis"] = _digits(r.get("gnis_id"))
            u["county_fips"] = r.get("county_fips")
            if u["tier"] == "county":
                u["fips"] = r.get("county_fips")
            if u["its_swis"] in stale_swis:
                counts["swis_held_stale"] += 1
                review.append(_item(u, "its_swis_known_stale", its_row=brief(r), note=stale_swis[u["its_swis"]]))
            else:
                u["swis"] = u["its_swis"]
        elif not hits:
            counts["no_match"] += 1
            review.append(_item(u, "its_no_match", note="No row in ITS 55k6-h6qq (2023) with this type, county "
                                "and name; no SWIS or GNIS attached."))
        else:
            counts["ambiguous"] += 1
            review.append(_item(u, "its_match_ambiguous",
                                its_rows=[brief(rows[i]) for i in hits if i >= 0],
                                note="More than one ITS row or place shares this type, county and name; no "
                                     "code attached."))
    unmatched = [rows[i] for i in range(len(rows)) if i not in used]
    for r in unmatched:
        review.append({"tier": ITS_TYPE[r["type"]], "reason": "its_row_unmatched", "its_row": brief(r),
                       "note": "No OSC place takes this ITS row (dissolved, renamed or respelled since 2023)."})
    counts["its_rows_unmatched"] = len(unmatched)
    counts["its_rows"] = len(rows)
    return counts


def build_records_osc(osc: dict, its: list[dict], dotgov: list[dict], grounding: list[dict],
                      base: list[dict], stale_swis: dict[str, str] | None = None,
                      tlds: set[str] | None = None, psl=None,
                      expected: dict[str, int] = EXPECTED_OSC_UNITS) -> tuple[list[dict], list[dict], dict]:
    """Pure, OSC spine: OSC places + ITS codes + website leads -> (records, review, summary)."""
    if psl is None:
        import ai_readability_export as ex
        psl = ex.load_psl()
    units, summary = osc_units(osc, expected)
    review: list[dict] = []
    assign_ids(units, {e["id"] for e in base})
    summary["its"] = match_its(units, its, review, stale_swis or {})
    # A code ITS gives to two places identifies neither (ITS gives 24 towns their city's GNIS id).
    # build_registry also matches on any shared identifier, so a repeat would merge two places.
    summary["its"]["codes_shared_not_attached"] = {}
    for scheme, key in (("nys_swis", "swis"), ("us_gnis", "gnis")):
        seen: dict[str, list[dict]] = {}
        for u in units:
            if u[key]:
                seen.setdefault(u[key], []).append(u)
        shared = {v: us for v, us in seen.items() if len(us) > 1}
        for v, us in shared.items():
            for u in us:
                u[key] = None
                review.append(_item(u, "its_code_shared_by_places", scheme=scheme, value=v,
                                    shared_with=sorted(o["id"] for o in us if o is not u),
                                    note="ITS gives this code to more than one place; attached to none."))
        summary["its"]["codes_shared_not_attached"][scheme] = sum(len(us) for us in shared.values())
    return _finish(units, summary, review, its, dotgov, grounding, base, tlds, psl, report_unjoined_its=False)


def base_registry() -> list[dict]:
    """Seed + curation + every source batch except this one: what a locality must not collide with."""
    if not (CACHE / "records_moda.json").exists():
        raise SystemExit(f"error: {CACHE / 'records_moda.json'} is missing; this sync checks ids, names "
                         "and hosts against the built registry and refuses to run without it.")
    curation = br._load_obj(br.CURATION)
    seed = br.apply_seed_curation(br._load(br.SEED, "entities"), curation)
    batches = [br._load(CACHE / n, "records") for n in br.SOURCE_FILES if n != OUT.name]
    entities, _ = br.build_entities(seed, batches)
    return br.apply_entity_curation(entities, curation)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="NY counties, cities, towns and villages -> registry records")
    ap.add_argument("--spine", choices=("osc", "gis"), default="osc",
                    help="osc (default since 2026-11-13: OSC entity tables, ITS codes) or gis (NYS Civil "
                         "Boundaries, once a GIS snapshot exists)")
    args = ap.parse_args(argv)
    needed = [OSC, ITS, DOTGOV, GROUNDING] + (list(GIS_FILES.values()) if args.spine == "gis" else [])
    missing = [p for p in needed if not p.exists()]
    if missing:
        print("[gated] missing input(s) in data/cache/:\n  " + "\n  ".join(p.name for p in missing)
              + "\n  See data/ai-readability/sources/2026-11-13/README.md for where each comes from.",
              file=sys.stderr)
        return 2
    osc = json.loads(OSC.read_text(encoding="utf-8"))
    its = json.loads(ITS.read_text(encoding="utf-8"))
    dotgov = json.loads(DOTGOV.read_text(encoding="utf-8"))["leads"]
    grounding = json.loads(GROUNDING.read_text(encoding="utf-8"))["rows"]
    import ai_readability_export as ex
    psl = ex.load_psl()
    if args.spine == "gis":
        gis = {t: gis_rows(json.loads(GIS_FILES[t].read_text(encoding="utf-8"))) for t in TIERS}
        records, review, summary = build_records(gis, osc, its, dotgov, grounding, base_registry(),
                                                 sc.psl_tlds(), psl)
    else:
        stale = {h["swis_code"]: h["note"] for h in sc.load_curation().get("locality_its_swis_stale", [])}
        records, review, summary = build_records_osc(osc, its, dotgov, grounding, base_registry(),
                                                     stale, sc.psl_tlds(), psl)
    OUT.write_text(json.dumps({"records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    rows = summary.pop("lead_rows")
    import csv
    with LEADS_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LEADS_CSV_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    summary["leads_csv_rows"] = len(rows)
    summary["leads_csv_places"] = len({r["entity_id"] for r in rows})
    sc.write_review_section(
        "localities", review,
        "Counties, cities, towns and villages (sync_nys_localities.py): places whose codes did not "
        "match one-to-one (OSC or ITS), ITS rows no place took, and website leads not applied "
        "(single lead, disagreeing, shared, path-only).")
    print(json.dumps(summary, indent=1))
    print(f"{len(records)} locality records, {len(review)} review item(s) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
