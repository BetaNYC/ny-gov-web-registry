"""sync_abo.py — map the ABO Directory of Public Authorities into v2 registry records.

Source: NYS Authorities Budget Office, "Directory of Public Authorities"
        data.ny.gov dataset 4vym-q77x  (608 authorities; State 45 / Local 111 / IDA 106 / LDC 346)
        https://data.ny.gov/Transparency/Directory-of-Public-Authorities/4vym-q77x

Columns are DOCUMENTED and were verified from the dataset's about page on 2026-07-11, and against
the 2026-11-06 JSON snapshot (data/ai-readability/sources/2026-11-06/abo-directory-4vym-q77x.json):
    public_authority_type, public_authority_name,
    address_line_1, address_line_2, city, state, zip,
    website (type URL; added 2026-01-15; self-reported, blank where not provided),
    georeference
In the Socrata JSON form `website` is an object {"url": ...}; in the CSV form it is a string.
Both are accepted. There is NO authority-id column, so no `nys_abo` identifier is emitted.

SCOPE (Noel, 2026-11-06): only `State` and `Local` rows (156) are emitted. IDA and LDC rows are
the stretch goal and are deliberately skipped (MINT_TYPES).

INPUT (this script never fetches; the operator places the files in data/cache/):
    data/cache/abo_4vym-q77x.json            (preferred; a copy of the dated snapshot)
    data/cache/abo_4vym-q77x.csv             (fallback; a CSV export of the same dataset)
    data/cache/state_agency_listing_websites.csv   (hosts some authorities carry; see sync_nygov)
    data/cache/records_moda.json             (the base registry this matches against)
    data/curation.json                       (abo_matches, abo_holds, abo_website_holds,
                                              state_listing_codes, state_listing_host_overrides)
OUTPUT:
    data/cache/records_abo.json              (read by build_registry.py)
    data/statewide_web_review.json § authorities   (committed; nothing in it is applied)

MATCHING (dedupe against the registry, never fuzzy):
  1. `abo_matches` in curation.json — operator-confirmed: this ABO row IS that existing entity.
  2. exact normalized name against the base registry (the build's own rule).
  A matched row ENRICHES the existing entity (adds `abo` to provenance, the ABO name to
  other_names, and any state-listing hosts it lacks). It never changes the entity's level,
  classification or primary site. An ABO host that differs from what the entity owns goes to
  the review file. Rows named in `abo_holds` (possible duplicates) are not emitted at all.

WEBSITE RULES (a web property is a host, never a URL or path — docs/registry-growth-plan.md):
  - ABO `website` at a site root -> the authority's `primary` web property (self-reported).
  - ABO `website` with a path (a page on someone else's site) -> url_conventions[], no domain.
  - If the state agency listing names hosts for the authority, the curated listing primary wins;
    a differing ABO host goes to review. The listing's other hosts become `microsite`s.
  - Held, malformed, or shared-by-several-authorities hosts go to review, never applied.
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build_registry as br  # noqa: E402
import statewide_common as sc  # noqa: E402

CACHE = sc.CACHE
SOURCE_JSON = CACHE / "abo_4vym-q77x.json"
SOURCE_CSV = CACHE / "abo_4vym-q77x.csv"
OUT = CACHE / "records_abo.json"

MINT_TYPES = ("State", "Local")
_CLASS = {"State": "state-authority", "Local": "local-authority"}
_ABO = "ABO Directory of Public Authorities (data.ny.gov 4vym-q77x), snapshot " + sc.SNAPSHOT_DATE
_LISTING = "a state agency listing (" + sc.SNAPSHOT_DATE + ")"


def website_url(row: dict) -> str:
    w = row.get("website")
    if isinstance(w, dict):
        return (w.get("url") or "").strip()
    return (w or "").strip()


def _address(row: dict) -> str | None:
    street = ", ".join(p for p in ((row.get("address_line_1") or "").strip(),
                                   (row.get("address_line_2") or "").strip()) if p)
    tail = " ".join(p for p in ((row.get("state") or "").strip(), (row.get("zip") or "").strip()) if p)
    parts = [p for p in (street, (row.get("city") or "").strip(), tail) if p]
    return ", ".join(parts) if street else None


def _wp(domain: str, role: str, notes: str) -> dict:
    return {"domain": domain, "role": role, "valid_from": None, "valid_to": None, "notes": notes}


def build_records(rows: list[dict], listing: list[dict], curation: dict,
                  base: list[dict], tlds: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Pure: ABO rows (+ listing + curation + base registry) -> (records, review items).

    `tlds` (Public Suffix List top-level rules) tightens the host sanity check; None skips it."""
    rows = [r for r in rows if (r.get("public_authority_type") or "").strip() in MINT_TYPES]
    by_id = {e["id"]: e for e in base}
    by_name = {br._norm(e["name"]): e for e in base}
    matches = {m["abo_name"]: m for m in curation.get("abo_matches", [])}
    holds = {h["abo_name"]: h for h in curation.get("abo_holds", [])}
    web_holds = {h["abo_name"]: h for h in curation.get("abo_website_holds", [])}
    listing_targets = sc.listing_hosts_by_target(listing, curation)
    codes = curation.get("state_listing_codes", {})
    listing_primary = {}
    for code, entry in codes.items():
        if entry.get("target", "").startswith("abo:") and entry.get("primary"):
            listing_primary[entry["target"][4:]] = (entry["primary"], f"code {code!r}")
    for host, entry in curation.get("state_listing_host_overrides", {}).items():
        if entry.get("target", "").startswith("abo:") and entry.get("primary"):
            listing_primary[entry["target"][4:]] = (
                host, "filed there under another code; curated override")

    names = [(r.get("public_authority_name") or "").strip() for r in rows]
    for key in list(matches) + list(holds) + list(web_holds):
        if key not in names:
            raise ValueError(f"curation names ABO row {key!r}, which is not a State/Local row")
    for kind, value in listing_targets:
        if kind == "abo" and value not in names:
            raise ValueError(f"state listing target abo:{value!r} is not a State/Local ABO row")

    # Root hosts claimed by more than one unmatched row cannot be anyone's primary.
    root_claims: dict[str, list[str]] = {}
    for r, name in zip(rows, names, strict=True):
        host, path = sc.split_url(website_url(r))
        if host and not path and name not in matches and name not in holds:
            root_claims.setdefault(sc.norm_host(host), []).append(name)

    records: list[dict] = []
    review: list[dict] = []
    for r, name in zip(rows, names, strict=True):
        atype = r["public_authority_type"].strip()
        url = website_url(r)
        host, path = sc.split_url(url)
        if name in holds:
            review.append({"abo_name": name, "abo_type": atype, "reason": holds[name]["reason"],
                           "candidate_entity_id": holds[name].get("candidate_entity_id"),
                           "abo_website": url or None, "note": holds[name].get("note")})
            continue

        target = None
        match_basis = None
        if name in matches:
            target = by_id.get(matches[name]["entity_id"])
            if target is None:
                raise ValueError(f"abo_matches: {name!r} -> unknown entity {matches[name]['entity_id']!r}")
            match_basis = "curation abo_matches"
        elif br._norm(name) in by_name:
            target = by_name[br._norm(name)]
            match_basis = "exact name"

        extra_hosts = listing_targets.get(("abo", name), [])
        primary_code = listing_primary.get(name)

        if target is not None:
            owned = sc.owned_hosts(target)
            add_wps = []
            for h in extra_hosts:
                if sc.norm_host(h) not in owned:
                    add_wps.append(_wp(h, "microsite", f"Additional host attributed to this body in {_LISTING}; "
                                                       "sub-site or application host, not verified."))
                    owned.add(sc.norm_host(h))
            if primary_code and sc.norm_host(primary_code[0]) not in sc.owned_hosts(target):
                review.append({"abo_name": name, "entity_id": target["id"],
                               "reason": "state_listing_primary_differs_from_existing_primary",
                               "host": primary_code[0],
                               "note": "Added as a microsite only; the existing primary was kept."})
            known_hosts = sc.owned_hosts(target) | {sc.norm_host(h) for h in extra_hosts}
            if host and not path and sc.norm_host(host) not in known_hosts:
                review.append({"abo_name": name, "entity_id": target["id"],
                               "reason": "abo_host_differs_from_existing_entity", "host": host,
                               "abo_website": url,
                               "existing_hosts": sorted(w["domain"] for w in target.get("web_properties", []))})
            known = {br._norm(target["name"])} | {br._norm(o["name"]) for o in target.get("other_names", [])}
            records.append({
                "name": name,
                "_match_entity_id": target["id"],
                "_on_match": {
                    "add_provenance_source": "abo",
                    # None when the entity already carries this name (keeps its existing note).
                    "other_name_note": None if br._norm(name) in known else
                    f"Name in the {_ABO} ({atype} authority); matched by {match_basis}.",
                    "add_web_properties": add_wps,
                },
            })
            continue

        # --- unmatched: mint a new authority -------------------------------------------------
        web_properties: list[dict] = []
        url_conventions: list[dict] = []
        if primary_code:
            p_host, code = primary_code
            note = f"Main host per {_LISTING} ({code})"
            if host and not path and sc.norm_host(host) == sc.norm_host(p_host):
                note += "; agrees with the ABO `website` (self-reported)."
            else:
                note += "."
                if host and not path:
                    review.append({"abo_name": name, "reason": "abo_host_differs_from_state_listing",
                                   "host": host, "abo_website": url, "state_listing_primary": p_host,
                                   "note": "The listing host was used as primary; the ABO host was not "
                                           "applied (possibly a legacy host, unverified)."})
            web_properties.append(_wp(p_host, "primary", note))
        elif host and not path:
            if not sc.host_is_valid(host, tlds):
                review.append({"abo_name": name, "reason": "abo_website_malformed", "abo_website": url})
            elif name in web_holds:
                review.append({"abo_name": name, "reason": web_holds[name]["reason"], "host": host,
                               "abo_website": url, "note": web_holds[name].get("note")})
            elif len(root_claims.get(sc.norm_host(host), [])) > 1:
                review.append({"abo_name": name, "reason": "abo_host_shared_by_several_authorities",
                               "host": host, "abo_website": url,
                               "shared_with": sorted(n for n in root_claims[sc.norm_host(host)] if n != name)})
            else:
                web_properties.append(_wp(host, "primary", f"From the `website` field of the {_ABO} "
                                                           "(self-reported by the authority)."))
        elif host and path:
            url_conventions.append({"era": None, "pattern": f"{host}{path}",
                                    "notes": f"Page-only presence: the {_ABO} `website` is a page on a "
                                             "host this authority does not own, so no web property."})
        owned = {sc.norm_host(w["domain"]) for w in web_properties}
        for h in extra_hosts:
            if sc.norm_host(h) not in owned:
                web_properties.append(_wp(h, "microsite", f"Additional host attributed to this body in "
                                                          f"{_LISTING}; sub-site or application host, not verified."))
                owned.add(sc.norm_host(h))

        rec = {
            "name": name,
            "short_name": None,
            "other_names": [],
            "government_level": "authority",
            "classification": _CLASS[atype],
            "identifiers": [],
            "web_properties": web_properties,
            "url_conventions": url_conventions,
            "areas": [],
            "area_note": None,
            "status": "active",
            "provenance": {"sources": ["abo"] + (["state_listing"] if extra_hosts else []),
                           "last_verified": None},
            "_expect_new": True,
        }
        addr = _address(r)
        if addr:
            rec["contact_details"] = [{"type": "address", "value": addr, "note": f"Office address in the {_ABO}."}]
        records.append(rec)
    return records, review


def read_rows() -> list[dict]:
    if SOURCE_JSON.exists():
        return json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    with SOURCE_CSV.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    if not SOURCE_JSON.exists() and not SOURCE_CSV.exists():
        print(f"[gated] no source export at {SOURCE_JSON} (or {SOURCE_CSV.name})\n"
              f"        copy the dated snapshot data/ai-readability/sources/<date>/abo-directory-4vym-q77x.json "
              f"there and retry.", file=sys.stderr)
        return 2
    if not sc.LISTING.exists():
        print(f"[gated] no state agency listing at {sc.LISTING}", file=sys.stderr)
        return 2
    rows = read_rows()
    records, review = build_records(rows, sc.read_listing(), sc.load_curation(), sc.base_entities(),
                                    sc.psl_tlds())
    OUT.write_text(json.dumps({"records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    sc.write_review_section(
        "authorities", review,
        "ABO State + Local rows (sync_abo.py): possible duplicates held back, and hosts not applied.")
    minted = sum(1 for r in records if r.get("_expect_new"))
    print(f"read {len(rows)} ABO rows; {len(records)} State/Local records "
          f"({minted} new, {len(records) - minted} enrich existing); {len(review)} review item(s) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
