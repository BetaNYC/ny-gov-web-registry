"""confirm_locality_websites.py — turn the AI-readability scan's results for the locality website
leads into a small, publishable list of confirmed addresses (growth plan § Finding and verifying
websites, rule 4: "accept if the <title> names the municipality"; the scan is the live check).

INPUT (passed on the command line; never committed, never copied into a repo): the scan
session's private CSV, one row per lead of data/ai-readability/localities-website-leads-*.csv,
with these columns (checked at startup):
    entity_id, name, county, level, host, source, default_body, default_final_host,
    chrome_body, chrome_final_host, title
Only those columns are read. Status codes, block outcomes and errors are never read or written.

OUTPUT: data/locality_website_checks.json (tracked). Each check names an entity, a host, its
role (primary or legacy), the scan date and the method. Nothing else from the scan is written.
scripts/sync_nys_localities.py reads it offline, so the build never needs the private file.

Rule:
  - A lead is live only if default_body or chrome_body is "content".
  - Its address is the final host (default_final_host, else chrome_final_host, else the lead
    host). It must be a registrable domain or www. + one (Public Suffix List), and not a social
    media site.
  - The page title must name the place: its name always; the level word ("Town of X", "X Town",
    "X County") when another county, city, town or village shares the name; and the county too
    when two places at the same level share it. A title that names a longer place containing the
    name ("Rye Brook" for Rye), or both levels ("Town and Village of X"), does not count.
  - One passing host for a place -> primary. Several passing hosts, or a host that passes for
    more than one place or is already another entity's site -> not confirmed (stays in review).
  - An ITS lead host (the place's 2023 address) that led to the confirmed host is recorded as
    `legacy`. Any other differing lead host is dropped.
  - Places that already have a primary from two agreeing leads are not touched.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import statewide_common as sc  # noqa: E402

ROOT = sc.ROOT
REGISTRY = ROOT / "data" / "registry.json"
OUT = ROOT / "data" / "locality_website_checks.json"
LEVELS = ("county", "city", "town", "village")
REQUIRED = ("entity_id", "name", "county", "level", "host", "source", "default_body",
            "default_final_host", "chrome_body", "chrome_final_host", "title")
METHOD = "page title names the place (BetaNYC AI-readability scan)"
SOCIAL = {"facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com", "youtube.com",
          "nextdoor.com", "tiktok.com"}


def norm(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\bmt\b", "mount", s)
    return re.sub(r"\s+", " ", s).strip()


def place_of(level: str, name: str) -> str:
    """'Town of Greenville' -> 'greenville'; 'St. Lawrence County' -> 'st lawrence'."""
    s = re.sub(r"^(?:(?:county|city|town|village) of )+", "", norm(name))
    return re.sub(r" county$", "", s) if level == "county" else s


def _spans(text: str, phrase: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text)]


def title_names(title: str, level: str, place: str, county: str, all_places: set[str],
                same_name_levels: set[str], same_level_count: int) -> bool:
    """True when the title names this place clearly enough (see the module docstring)."""
    t = norm(title)
    if not t or not place:
        return False
    own = _spans(t, place)
    # Drop occurrences inside a longer place name that also appears ("rye" inside "rye brook").
    longer = [sp for p in all_places if p != place and place in p for sp in _spans(t, p)]
    own = [s for s in own if not any(a <= s[0] and s[1] <= b and (b - a) > (s[1] - s[0]) for a, b in longer)]
    if not own:
        return False
    if re.search(r"\b(?:town|village|city) and (?:town|village|city) of " + re.escape(place) + r"\b", t):
        return False
    needs_level = len(same_name_levels) > 1 or same_level_count > 1 or level == "county"
    if needs_level:
        word = level
        if not (_spans(t, f"{word} of {place}") or _spans(t, f"{place} {word}")):
            return False
        for other in same_name_levels - {level}:
            if _spans(t, f"{other} of {place}") or (other != "county" and _spans(t, f"{place} {other}")):
                return False
    return not (same_level_count > 1 and not _spans(t, f"{norm(county)} county"))


def host_ok(host: str, psl) -> bool:
    import ai_readability_export as ex
    bare = host.removeprefix("www.")
    return bool(bare) and ex.base_domain(bare, psl) == bare and bare not in SOCIAL


def decide(rows: list[dict], entities: list[dict], psl, checked_on: str) -> tuple[list[dict], dict]:
    """Pure: scan rows + the registry -> (checks, aggregate counts). No scan outcome is returned."""
    loc = [e for e in entities if e["government_level"] in LEVELS]
    # Only a primary from two agreeing leads counts here; one this script confirmed earlier (and the
    # build applied) is recomputed, so a rerun gives the same result.
    with_primary = {e["id"] for e in loc if any(w["role"] == "primary" and (w.get("notes") or "").startswith(
        "Auto-accepted") for w in e["web_properties"])}
    owner = {}
    for e in entities:
        for w in e.get("web_properties", []):
            if e["government_level"] in LEVELS and e["id"] not in with_primary:
                continue  # a locality's own scan-confirmed hosts never block its own rerun
            owner.setdefault(sc.norm_host(w["domain"]), e["id"])
    places: dict[str, set[str]] = {}
    level_count: dict[tuple[str, str], int] = {}
    for e in loc:
        p = place_of(e["government_level"], e["name"])
        places.setdefault(p, set()).add(e["government_level"])
        level_count[(e["government_level"], p)] = level_count.get((e["government_level"], p), 0) + 1
    all_places = set(places)
    ids = {e["id"] for e in loc}

    passing: dict[str, dict[str, set[str]]] = {}   # entity -> final host -> ITS lead hosts that led there
    live_entities: set[str] = set()
    for r in rows:
        if r["entity_id"] not in ids:
            raise ValueError(f"scan row names unknown entity {r['entity_id']!r}")
        if r["entity_id"] in with_primary:
            continue
        if "content" not in (r["default_body"], r["chrome_body"]):
            continue
        live_entities.add(r["entity_id"])
        final = (r["default_final_host"] or r["chrome_final_host"] or r["host"]).strip().lower().rstrip(".")
        if not host_ok(final, psl):
            continue
        place = place_of(r["level"], r["name"])
        if not title_names(r["title"], r["level"], place, r["county"], all_places, places.get(place, set()),
                           level_count.get((r["level"], place), 0)):
            continue
        hosts = passing.setdefault(r["entity_id"], {})
        via = hosts.setdefault(final, set())
        if r["source"] == "its" and sc.norm_host(r["host"]) != sc.norm_host(final):
            via.add(r["host"].lower())

    by_host: dict[str, set[str]] = {}
    for eid, hosts in passing.items():
        for h in {sc.norm_host(h) for h in hosts}:
            by_host.setdefault(h, set()).add(eid)
    checks, counts = [], {"several_hosts": 0, "host_shared": 0, "host_owned_elsewhere": 0}
    for eid in sorted(passing):
        hosts = passing[eid]
        if len({sc.norm_host(h) for h in hosts}) > 1:
            counts["several_hosts"] += 1
            continue
        host = min(hosts)
        if len(by_host[sc.norm_host(host)]) > 1:
            counts["host_shared"] += 1
            continue
        if sc.norm_host(host) in owner:
            counts["host_owned_elsewhere"] += 1
            continue
        checks.append({"entity_id": eid, "host": host, "role": "primary", "checked_on": checked_on, "method": METHOD})
        legacy = {h for hs in hosts.values() for h in hs}
        for lh in sorted(legacy):
            if sc.norm_host(lh) in owner or sc.norm_host(lh) in by_host:
                continue
            checks.append({"entity_id": eid, "host": lh, "role": "legacy", "checked_on": checked_on,
                           "method": METHOD})
    without = {e["id"] for e in loc} - with_primary
    confirmed = {c["entity_id"] for c in checks if c["role"] == "primary"}
    counts.update({"places_without_two_lead_primary": len(without), "confirmed": len(confirmed),
                   "legacy_hosts": sum(c["role"] == "legacy" for c in checks),
                   "still_without_primary": len(without - confirmed),
                   "no_live_lead": len(without - live_entities),
                   "live_but_not_confirmed": len((live_entities & without) - confirmed)})
    return checks, counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scan-results", type=pathlib.Path, required=True,
                    help="the scan session's private per-lead results CSV (never commit it)")
    ap.add_argument("--checked-on", required=True, help="date of the scan run, YYYY-MM-DD")
    args = ap.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.checked_on):
        print("error: --checked-on must be YYYY-MM-DD", file=sys.stderr)
        return 2
    if not args.scan_results.exists():
        print(f"error: no file at {args.scan_results}", file=sys.stderr)
        return 2
    with args.scan_results.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            print(f"error: scan results lack column(s) {missing}", file=sys.stderr)
            return 2
        rows = [{k: (r.get(k) or "") for k in REQUIRED} for r in reader]
    import ai_readability_export as ex
    entities = json.loads(REGISTRY.read_text(encoding="utf-8"))["entities"]
    checks, counts = decide(rows, entities, ex.load_psl(), args.checked_on)
    OUT.write_text(json.dumps({
        "_about": ("Locality websites confirmed by the BetaNYC AI-readability scan: the page title names "
                   "the place (growth plan rule 4). Only the address, its role, the scan date and the method "
                   "are kept. Read by scripts/sync_nys_localities.py; written by "
                   "scripts/confirm_locality_websites.py."),
        "checks": checks}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"lead_rows": len(rows), **counts}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
