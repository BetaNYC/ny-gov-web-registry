"""qa_localities.py — check the minted counties, cities, towns and villages against three
published lists, and write down every disagreement.

Reads committed files only (never the cache, never the network):
    data/registry.json                         the minted entities (government_level county/city/town/village)
    data/ai-readability/sources/2026-11-13/osc-entity-tables-general-purpose.json   OSC (the spine)
    data/ai-readability/sources/2026-11-06/its-locality-websites-55k6-h6qq.json     ITS, data.ny.gov 55k6-h6qq
    data/ai-readability/sources/2026-11-13/census-population-sxhg-qquj.json         data.ny.gov sxhg-qquj
        ("Census 2010 and 2020 Population: Region, Counties, Cities, Towns, Villages"; columns
        area_type, area_name, _2010_census_population, _2020_census_population, ...)

Checks:
  OSC     every minted entity carries a nys_osc_muni_code that is in OSC at the same level, the
          entity's name is OSC's name (counties: "County of X" == "X County"), and every OSC row
          outside NYC is minted exactly once.
  ITS     an entity matches ITS when its nys_swis is an ITS row of the same type whose name and
          county agree. Unmatched both ways are listed.
  Census  the Census list has no county column and no codes, so it is matched by level + name
          only, counting duplicates (two "Greenville town" rows cover two Towns of Greenville).
          Unmatched both ways are listed. NYC's five counties and "New York City" are excluded
          and named as excluded.

Name comparison (stated, not loosened per case): lower case; a tier prefix ("Town of") removed,
and the one type word a source appends (Census " town", " village"; " County") removed; "Saint" -> "St", "Mt" -> "Mount"; a leading "The" dropped;
then every character that is not a letter or digit removed. So "De Ruyter" == "DeRuyter" and
"St. Lawrence" == "St Lawrence", but "Fallsburg" != "Fallsburgh".

OUTPUT:
    data/statewide_localities_qa.json             (machine-readable results)
    data/statewide_web_review.json § localities_census_qa   (every Census mismatch; the ITS
        mismatches are already in § localities, written by sync_nys_localities.py)
    docs/qa/2026-11-13-localities-qa.md           (the human report; with --report)
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import statewide_common as sc  # noqa: E402

ROOT = sc.ROOT
REGISTRY = ROOT / "data" / "registry.json"
SRC13 = ROOT / "data" / "ai-readability" / "sources" / "2026-11-13"
OSC = SRC13 / "osc-entity-tables-general-purpose.json"
CENSUS = SRC13 / "census-population-sxhg-qquj.json"
ITS = ROOT / "data" / "ai-readability" / "sources" / "2026-11-06" / "its-locality-websites-55k6-h6qq.json"
OUT = ROOT / "data" / "statewide_localities_qa.json"
REPORT = ROOT / "docs" / "qa" / "2026-11-13-localities-qa.md"

LEVELS = ("county", "city", "town", "village")
OSC_SHEET = {"county": "counties", "city": "cities", "town": "towns", "village": "villages"}
ITS_TYPE = {"County": "county", "City": "city", "Town": "town", "Village": "village"}
CENSUS_TYPE = {"County": "county", "City": "city", "Town": "town", "Village": "village"}
NYC_CENSUS = {("county", "Bronx County"), ("county", "Kings County"), ("county", "New York County"),
              ("county", "Queens County"), ("county", "Richmond County"), ("city", "New York City")}


def key(name: str | None, suffix: str | None = None) -> str:
    """Comparison key. `suffix` is the one trailing type word to drop ("town" for a Census
    "Colonie town", "county" for "Albany County"); it is never guessed from the name, so
    "Garden City village" keeps its "City"."""
    s = (name or "").strip().lower().replace("&", " and ")
    s = re.sub(r"^(?:(?:county|city|town|village)\s+of\s+)+", "", s)
    if suffix and s.endswith(" " + suffix):
        s = s[: -len(suffix) - 1]
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\bmt\b\.?", "mount", s)
    s = re.sub(r"^the\s+", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _code(e: dict, scheme: str) -> str | None:
    return next((i["identifier"] for i in e.get("identifiers", []) if i["scheme"] == scheme), None)


def check_osc(entities: list[dict], osc: dict) -> dict:
    rows = {}
    for lvl in LEVELS:
        for r in osc[OSC_SHEET[lvl]]:
            if lvl == "city" and key(r["name"]) in ("newyork", "newyorkcity"):
                continue
            rows[r["municipal_code"]] = (lvl, r)
    problems, used = [], collections.Counter()
    for e in entities:
        code = _code(e, "nys_osc_muni_code")
        hit = rows.get(code)
        if hit is None or hit[0] != e["government_level"]:
            problems.append({"entity_id": e["id"], "problem": "osc code missing or at another level", "code": code})
            continue
        used[code] += 1
        if key(hit[1]["name"]) != key(e["name"], "county" if hit[0] == "county" else None):
            problems.append({"entity_id": e["id"], "problem": "name differs from OSC", "osc_name": hit[1]["name"]})
    not_minted = [{"municipal_code": c, "level": lv, "name": r["name"]} for c, (lv, r) in rows.items() if not used[c]]
    twice = [c for c, n in used.items() if n > 1]
    return {"osc_rows": len(rows), "entities": len(entities), "problems": problems,
            "osc_rows_not_minted": not_minted, "osc_codes_minted_twice": twice}


def check_its(entities: list[dict], its: list[dict], osc: dict) -> dict:
    county_of = {r["municipal_code"]: (r["county"] or key(r["name"])) for s in OSC_SHEET.values() for r in osc[s]}
    rows = [r for r in its if r.get("type") in ITS_TYPE
            and not (r["type"] == "City" and key(r.get("municipality")) in ("newyork", "newyorkcity"))]
    by_swis = {r["swis_code"]: r for r in rows}
    per = {lvl: {"entities": 0, "matched": 0} for lvl in LEVELS}
    unmatched_entities, used = [], set()
    for e in entities:
        lvl = e["government_level"]
        per[lvl]["entities"] += 1
        r = by_swis.get(_code(e, "nys_swis"))
        name = (r or {}).get("county") if lvl == "county" else (r or {}).get("municipality")
        counties = {key((r or {}).get("county")), key((r or {}).get("_2nd_county"))}
        if r and ITS_TYPE[r["type"]] == lvl and key(name) == key(e["name"], "county" if lvl == "county" else None) \
                and key(county_of.get(_code(e, "nys_osc_muni_code"))) in counties:
            per[lvl]["matched"] += 1
            used.add(r["swis_code"])
        else:
            unmatched_entities.append({"entity_id": e["id"], "level": lvl, "name": e["name"],
                                       "county": county_of.get(_code(e, "nys_osc_muni_code"))})
    unmatched_rows = [{"swis_code": r["swis_code"], "level": ITS_TYPE[r["type"]],
                       "name": r.get("municipality") or r.get("county"), "county": r.get("county")}
                      for r in rows if r["swis_code"] not in used]
    for v in per.values():
        v["rate"] = round(v["matched"] / v["entities"], 4) if v["entities"] else None
    return {"its_rows": len(rows), "by_level": per, "entities_unmatched": unmatched_entities,
            "its_rows_unmatched": unmatched_rows}


def check_census(entities: list[dict], census: list[dict]) -> dict:
    rows = [(CENSUS_TYPE[r["area_type"]], r["area_name"]) for r in census if r.get("area_type") in CENSUS_TYPE]
    excluded = sorted(f"{lv}: {n}" for lv, n in rows if (lv, n) in NYC_CENSUS)
    rows = [(lv, n) for lv, n in rows if (lv, n) not in NYC_CENSUS]
    census_by = collections.defaultdict(list)
    for lv, n in rows:
        census_by[(lv, key(n, lv))].append(n)
    ent_by = collections.defaultdict(list)
    for e in entities:
        ent_by[(e["government_level"], key(e["name"], "county" if e["government_level"] == "county" else None))].append(e)
    per = {lvl: {"entities": 0, "matched": 0, "census_rows": 0} for lvl in LEVELS}
    for lv, _ in rows:
        per[lv]["census_rows"] += 1
    unmatched_entities, unmatched_rows = [], []
    for k in sorted(set(census_by) | set(ent_by)):
        es, cs = ent_by.get(k, []), census_by.get(k, [])
        for e in es:
            per[k[0]]["entities"] += 1
        n = min(len(es), len(cs))
        per[k[0]]["matched"] += n if len(es) == len(cs) else 0
        if len(es) != len(cs):
            for e in es:
                unmatched_entities.append({"entity_id": e["id"], "level": k[0], "name": e["name"],
                                           "entities_with_this_name": len(es), "census_rows_with_this_name": len(cs)})
            for c in (cs if len(es) == 0 or len(cs) > len(es) else []):
                unmatched_rows.append({"level": k[0], "census_name": c,
                                       "entities_with_this_name": len(es), "census_rows_with_this_name": len(cs)})
    for v in per.values():
        v["rate"] = round(v["matched"] / v["entities"], 4) if v["entities"] else None
    return {"census_rows": len(rows), "excluded_nyc": excluded, "by_level": per,
            "entities_unmatched": unmatched_entities, "census_rows_unmatched": unmatched_rows}


def run() -> dict:
    entities = [e for e in json.loads(REGISTRY.read_text(encoding="utf-8"))["entities"]
                if e["government_level"] in LEVELS]
    osc = json.loads(OSC.read_text(encoding="utf-8"))
    its = json.loads(ITS.read_text(encoding="utf-8"))
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    return {"_about": "QA of the minted counties, cities, towns and villages (scripts/qa_localities.py). "
                      "Report: docs/qa/2026-11-13-localities-qa.md.",
            "entities_by_level": dict(collections.Counter(e["government_level"] for e in entities)),
            "osc": check_osc(entities, osc), "its": check_its(entities, its, osc),
            "census": check_census(entities, census)}


def _rate(v: dict) -> str:
    return f"{v['matched']} / {v['entities']} ({v['rate']:.1%})"


def report(qa: dict, review_counts: dict[str, int], web: dict) -> str:
    osc, its, cen = qa["osc"], qa["its"], qa["census"]
    lines = [
        "---", "title: QA of the minted New York counties, cities, towns and villages", "created: 2026-11-13",
        "type: qa-report", "status: active", "tags: [registry, qa, nys, localities]", "---", "",
        "# QA: New York counties, cities, towns and villages (issue #25)", "",
        "- **Report generated:** 2026-11-13",
        ("- **Data current as of:** OSC entity tables downloaded 2026-11-13 (file dated 2026-11-02); ITS "
        "`55k6-h6qq` snapshot of 2026-11-06 (rows last updated 2023-03-06); data.ny.gov `sxhg-qquj` read "
        "2026-11-14T00:34Z UTC (2026-11-13, 7:34 pm ET; rows last updated 2022-01-03)"),
        ("- **Built by:** `scripts/qa_localities.py` from committed files only. Machine-readable: "
        "`data/statewide_localities_qa.json`. Every mismatch below is also in "
        "`data/statewide_web_review.json` (§ `localities` for ITS, § `localities_census_qa` for the Census list)."),
        "",
        "## Minted", "",
        "| Level | Entities |", "|---|---|",
        *[f"| {lv} | {qa['entities_by_level'].get(lv, 0)} |" for lv in LEVELS],
        f"| **total** | **{sum(qa['entities_by_level'].values())}** |", "",
        ("Spine: the OSC entity tables (57 counties outside NYC, 62 cities including NYC, 933 towns, 532 "
        "villages). NYC's five counties and the City of New York are not minted. The six coterminous "
        "town-villages OSC mentions are two entities each, one per government, so they need no special case."),
        "",
        "## OSC (the spine)", "",
        (f"- Entities whose OSC code, level and name agree with OSC: {osc['entities'] - len(osc['problems'])} "
        f"of {osc['entities']}."),
        (f"- OSC rows outside NYC not minted: {len(osc['osc_rows_not_minted'])}. Codes minted twice: "
        f"{len(osc['osc_codes_minted_twice'])}."), "",
        "## ITS `55k6-h6qq` (codes)", "",
        "An entity matches when its SWIS is an ITS row of the same type with the same name and county.", "",
        "| Level | Matched |", "|---|---|",
        *[f"| {lv} | {_rate(its['by_level'][lv])} |" for lv in LEVELS], "",
        f"**Entities with no ITS match ({len(its['entities_unmatched'])}):**", "",
        *[f"- `{x['entity_id']}` ({x['name']}, {x['county']})" for x in its["entities_unmatched"]], "",
        f"**ITS rows no entity matches ({len(its['its_rows_unmatched'])}):**", "",
        *[f"- {x['level']} {x['name']} ({x['county']}), SWIS {x['swis_code']}" for x in its["its_rows_unmatched"]],
        "",
        "## Census population list, data.ny.gov `sxhg-qquj` (names)", "",
        "No county column and no codes, so the match is level + name, counting duplicates. Excluded as "
        "NYC: " + ", ".join(cen["excluded_nyc"]) + ".", "",
        "| Level | Entities matched | Census rows |", "|---|---|---|",
        *[f"| {lv} | {_rate(cen['by_level'][lv])} | {cen['by_level'][lv]['census_rows']} |" for lv in LEVELS], "",
        f"**Entities with no Census match ({len(cen['entities_unmatched'])}):**", "",
        *[f"- `{x['entity_id']}` ({x['name']})" for x in cen["entities_unmatched"]], "",
        f"**Census rows no entity matches ({len(cen['census_rows_unmatched'])}):**", "",
        *[f"- {x['level']}: {x['census_name']}" for x in cen["census_rows_unmatched"]], "",
        "## Reading the mismatches (this run)", "",
        ("- **ITS, 18 villages that OSC no longer lists** (Cherry Creek through Macedon above): "
        "`docs/sources-statewide.md` § 1 found the same 18 missing from the March 2026 GIS layer and "
        "OSC, presumably dissolved. They are not minted."),
        ("- **ITS, two spellings:** OSC writes Fallsburg and Bloomingburg, ITS writes Fallsburgh and "
        "Bloomingburgh. The match does not bridge an added letter, so both stay unmatched and get no "
        "SWIS or GNIS from ITS; a person or the GIS read should settle them."),
        "- **ITS, newer than the 2023 list:** the Town of Palm Tree and the Village of Ateres.",
        ("- **ITS, Kiryas Joel:** matched by name, but its ITS SWIS (334005) is the old code; GIS gives "
        "336001 (`sources-statewide.md` § 1). Held in `data/curation.json` § `locality_its_swis_stale`, "
        "so no SWIS is attached and the ITS row counts as unmatched."),
        ("- **Census, three coterminous town-villages:** East Rochester, Mount Kisco and Scarsdale "
        "appear in `sxhg-qquj` only as villages. Harrison and Green Island appear as both. The registry "
        "keeps a town and a village for all five (one entity per government, per the growth plan)."),
        "- **Census, Sherrill:** the City of Sherrill is not in `sxhg-qquj` under any type.",
        "- **Census, Ateres:** not in the list under any type (ITS lacks it too).",
        ("- **Census, four villages OSC no longer lists:** Fort Johnson, Harrisville, Morristown and "
        "South Nyack, four of the 18 ITS villages above."),
        ("- **ITS GNIS shared by a city and its town:** ITS gives 24 towns the same GNIS id as the city "
        "of the same name (Amsterdam, Batavia, ...). A code that names two places names neither, so "
        "those 48 places get no `us_gnis` (review reason `its_code_shared_by_places`). They keep their SWIS."),
        "",
        "## Websites", "",
        f"- Places with a primary site: {web['primary']} of {web['places']}.",
        f"  - {web['two_lead']} because two independent leads named the same host (no live check).",
        (f"  - {web['scan']} confirmed by the BetaNYC AI-readability scan: the site's page title names the "
        "place (growth plan rule 4). The address, its role and the scan date are in "
        f"`data/locality_website_checks.json`, which also records {web['legacy']} former addresses (`legacy`)."),
        (f"- Places without a primary: {web['places'] - web['primary']}; of those, {web['csv_places']} have at "
         "least one lead host in `data/ai-readability/localities-website-leads-2026-11-13.csv` "
         f"({web['csv_rows']} rows, one per host and source) and the rest have no host lead (some only a "
         "path-only page, which stays in the review file)."),
        "- No municipal site was fetched for this work; the scan's 2026-11-10 results were reused.", "",
        "## Review file, § localities", "",
        "| Reason | Items |", "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in sorted(review_counts.items())], "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", action="store_true", help="also write docs/qa/2026-11-13-localities-qa.md")
    args = ap.parse_args(argv)
    qa = run()
    OUT.write_text(json.dumps(qa, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    cen = qa["census"]
    items = ([{"reason": "census_no_entity_match", **x} for x in cen["entities_unmatched"]]
             + [{"reason": "census_row_no_entity", **x} for x in cen["census_rows_unmatched"]])
    sc.write_review_section(
        "localities_census_qa", items,
        "Census 2010/2020 population list (data.ny.gov sxhg-qquj) names that do not match a minted "
        "county, city, town or village one-for-one, both ways (scripts/qa_localities.py). Nothing applied.")
    if args.report:
        review = json.loads(sc.REVIEW.read_text(encoding="utf-8"))["localities"]["items"]
        counts = collections.Counter(i["reason"] for i in review)
        ents = [e for e in json.loads(REGISTRY.read_text(encoding="utf-8"))["entities"]
                if e["government_level"] in LEVELS]
        leads = (ROOT / "data" / "ai-readability" / "localities-website-leads-2026-11-13.csv").read_text(
            encoding="utf-8").splitlines()[1:]
        prim = [w for e in ents for w in e["web_properties"] if w["role"] == "primary"]
        web = {"places": len(ents), "primary": len(prim),
               "two_lead": sum(1 for w in prim if w["notes"].startswith("Auto-accepted")),
               "scan": sum(1 for w in prim if w["notes"].startswith("Confirmed:")),
               "legacy": sum(1 for e in ents for w in e["web_properties"] if w["role"] == "legacy"),
               "csv_rows": len(leads), "csv_places": len({ln.split(",", 1)[0] for ln in leads})}
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report(qa, dict(counts), web), encoding="utf-8")
        print(f"wrote {REPORT}")
    its, osc = qa["its"], qa["osc"]
    print(f"OSC problems {len(osc['problems'])}, not minted {len(osc['osc_rows_not_minted'])}; "
          f"ITS unmatched {len(its['entities_unmatched'])} entities / {len(its['its_rows_unmatched'])} rows; "
          f"Census unmatched {len(cen['entities_unmatched'])} entities / {len(cen['census_rows_unmatched'])} rows")
    for name, part in (("ITS", its), ("Census", cen)):
        print(name, {lv: v["rate"] for lv, v in part["by_level"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
