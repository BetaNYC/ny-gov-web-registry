"""sync_nygov.py — NYS executive agencies as v2 registry records (growth plan step 1).

There is no single machine-readable list of New York State executive agencies with their
websites, so this sync joins three dated snapshots through a hand-curated table:

  spine (who the agencies are, and their names)
    - the ny.gov agency directory, https://www.ny.gov/agencies (redirects to
      /state-agencies-and-departments; rendered client-side, no export, no stated license).
      Snapshot: data/ai-readability/sources/2026-11-06/nygov-state-agencies-and-departments.json
    - Agency Contacts, data.ny.gov `kjw8-pupa` (Office of the Governor; columns verified
      2026-11-06 from /api/views/kjw8-pupa.json: contact, primary_contact, title, phone_number,
      agency, agency_contact_url). Only `agency` and `agency_contact_url` were fetched; contact
      names, titles and phones are deliberately never read.
      Snapshot: data/ai-readability/sources/2026-11-06/kjw8-pupa-agency-contacts-agencies.json
  websites
    - a state agency listing (shared with BetaNYC privately; link private): domain, url, agency
      code. Snapshot: data/ai-readability/sources/2026-11-06/state-agency-listing-websites.csv

  curation (data/curation.json)
    - state_agencies: one entry per agency minted — official name, which directory names it,
      short name, primary host.
    - state_listing_codes: every agency code in the listing -> a target
        agency:<key>   a state agency minted here
        abo:<name>     an ABO public authority (handled by sync_abo.py)
        entity:<id>    an existing registry entity (hosts added if missing)
        review:<why>   not minted; held in data/statewide_web_review.json
    - state_listing_host_overrides: a host the listing files under the wrong code.

INPUT (this script never fetches): data/cache/state_agency_listing_websites.csv,
data/cache/nygov_state_agencies.json, data/cache/kjw8-pupa_agencies.json,
data/cache/records_moda.json (+ records_abo.json if present), data/curation.json.
OUTPUT: data/cache/records_nygov.json; data/statewide_web_review.json § state_agencies.

Every agency record is `_expect_new`: if its name ever collides with an existing entity the build
fails rather than merging a state agency into a city one (NYC also has a Department of
Transportation). Names carry a "New York State" prefix where the directory name alone would be
ambiguous; the directory's own wording is kept in other_names[] (see docs/sources.md).
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import statewide_common as sc  # noqa: E402

CACHE = sc.CACHE
NYGOV = CACHE / "nygov_state_agencies.json"
KJW8 = CACHE / "kjw8-pupa_agencies.json"
RECORDS_ABO = CACHE / "records_abo.json"
OUT = CACHE / "records_nygov.json"

_LISTING = "a state agency listing (" + sc.SNAPSHOT_DATE + ")"
_NYGOV = "the ny.gov agency directory (" + sc.SNAPSHOT_DATE + ")"
_KJW8 = "Agency Contacts, data.ny.gov kjw8-pupa (" + sc.SNAPSHOT_DATE + ")"


def _wp(domain: str, role: str, notes: str) -> dict:
    return {"domain": domain, "role": role, "valid_from": None, "valid_to": None, "notes": notes}


def build_records(listing: list[dict], nygov: list[dict], kjw8: list[dict], curation: dict,
                  base: list[dict], abo_records: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Pure: snapshots + curation + base registry -> (records, review items)."""
    agencies = curation.get("state_agencies", [])
    codes = curation.get("state_listing_codes", {})
    overrides = curation.get("state_listing_host_overrides", {})
    nygov_by_name = {a["name"]: a for a in nygov}
    kjw8_by_name = {a["agency"]: a for a in kjw8}
    targets = sc.listing_hosts_by_target(listing, curation)

    # Every listing code must be curated (listing_hosts_by_target raises otherwise), and every
    # agency target must exist in state_agencies.
    keys = {a["key"] for a in agencies}
    for kind, value in targets:
        if kind == "agency" and value not in keys:
            raise ValueError(f"listing target agency:{value} has no state_agencies entry")

    # Hosts already owned elsewhere: the base registry plus any minted ABO record.
    owner: dict[str, str] = {}
    for e in base:
        for w in e.get("web_properties", []):
            owner.setdefault(sc.norm_host(w["domain"]), e["id"])
    for r in abo_records or []:
        for w in r.get("web_properties", []):
            owner.setdefault(sc.norm_host(w["domain"]), "abo:" + r["name"])

    review: list[dict] = []
    records: list[dict] = []
    codes_by_agency: dict[str, list[str]] = {}
    for code, entry in codes.items():
        if entry["target"].startswith("agency:"):
            codes_by_agency.setdefault(entry["target"][7:], []).append(code)

    for a in agencies:
        ng = nygov_by_name.get(a["nygov_name"]) if a.get("nygov_name") else None
        if a.get("nygov_name") and ng is None:
            raise ValueError(f"state_agencies {a['key']}: nygov_name {a['nygov_name']!r} not in the snapshot")
        kj = kjw8_by_name.get(a["kjw8_agency"]) if a.get("kjw8_agency") else None
        if a.get("kjw8_agency") and kj is None:
            raise ValueError(f"state_agencies {a['key']}: kjw8_agency {a['kjw8_agency']!r} not in the snapshot")
        if ng is None and kj is None:
            raise ValueError(f"state_agencies {a['key']}: no directory names this agency; it cannot be minted")
        hosts = targets.get(("agency", a["key"]), [])
        if a["primary"] not in hosts:
            raise ValueError(f"state_agencies {a['key']}: primary {a['primary']!r} is not among "
                             f"its listing hosts {hosts}")
        kj_host = sc.split_url(kj.get("agency_contact_url"))[0] if kj else None
        code_txt = ", ".join(repr(c) for c in sorted(codes_by_agency.get(a["key"], [])))

        wps: list[dict] = []
        for h in [a["primary"]] + [h for h in hosts if h != a["primary"]]:
            other = owner.get(sc.norm_host(h))
            if other is not None:
                # Applied anyway (the listing names this agency as the host's owner), but flagged:
                # the other claim is often a MODA `url` that is really a page on a state host.
                review.append({"agency_key": a["key"], "reason": "host_also_owned_by_other_entity",
                               "host": h, "also_owned_by": other,
                               "note": "Applied to this agency; check whether the other entity's claim "
                                       "is a page-only presence that belongs in url_conventions[]."})
            if h == a["primary"]:
                note = f"Main host for listing code {code_txt} in {_LISTING}"
                if kj_host and sc.norm_host(kj_host) == sc.norm_host(h):
                    note += f"; agrees with the agency_contact_url host in {_KJW8}"
                note += "."
                wps.append(_wp(h, "primary", note))
            else:
                note = (f"Additional host attributed to this agency in {_LISTING}; sub-site or "
                        "application host, not verified.")
                extra = (overrides.get(h) or {}).get("note") or a.get("host_notes", {}).get(h)
                if extra:
                    note += " " + extra
                wps.append(_wp(h, "microsite", note))

        other_names = []
        if ng and sc.br._norm(ng["name"]) != sc.br._norm(a["name"]):
            other_names.append({"name": ng["name"], "note": f"Name in {_NYGOV}."})
        seen = {sc.br._norm(a["name"])} | {sc.br._norm(o["name"]) for o in other_names}
        if kj and sc.br._norm(kj["agency"]) not in seen:
            other_names.append({"name": kj["agency"], "note": f"Name in {_KJW8}."})
        sources = ["state_listing"] + (["nygov"] if ng else []) + (["nygov_contacts"] if kj else [])
        rec = {
            "name": a["name"],
            "short_name": a.get("short_name"),
            "other_names": other_names,
            "government_level": "nys",
            "classification": a.get("classification", "state-agency"),
            "identifiers": [],
            "web_properties": wps,
            "areas": [],
            "area_note": None,
            "status": "active",
            "provenance": {"sources": sources, "last_verified": None},
            "_expect_new": True,
        }
        if ng:
            rec["links"] = [{"url": ng["url"], "note": f"Entry in {_NYGOV}."}]
        records.append(rec)

    # Existing entities named by a listing code: add the hosts they lack.
    for (kind, value), hosts in sorted(targets.items()):
        if kind != "entity":
            continue
        ent = next((e for e in base if e["id"] == value), None)
        if ent is None:
            raise ValueError(f"listing target entity:{value} is not in the registry")
        add = [_wp(h, "microsite", f"Additional host attributed to this body in {_LISTING}; not verified.")
               for h in hosts if sc.norm_host(h) not in sc.owned_hosts(ent)]
        records.append({"name": ent["name"], "_match_entity_id": value,
                        "_on_match": {"add_provenance_source": "state_listing" if add else None,
                                      "add_web_properties": add}})

    # Codes and hosts held for review.
    for (kind, value), hosts in sorted(targets.items()):
        if kind != "review":
            continue
        held_codes = sorted(c for c, e in codes.items() if e["target"] == f"review:{value}")
        held_hosts = sorted(h for h in hosts)
        notes = {c: codes[c].get("note") for c in held_codes if codes[c].get("note")}
        host_notes = {h: overrides[h]["note"] for h in held_hosts if h in overrides and overrides[h].get("note")}
        review.append({"reason": value, "listing_codes": held_codes, "hosts": held_hosts,
                       **({"code_notes": notes} if notes else {}),
                       **({"host_notes": host_notes} if host_notes else {})})

    # Directory agencies with no host in the listing: visible, not minted.
    used_ng = {a.get("nygov_name") for a in agencies}
    used_kj = {a.get("kjw8_agency") for a in agencies}
    covered_ng = set(curation.get("state_directory_covered_elsewhere", {}).get("nygov", {}))
    covered_kj = set(curation.get("state_directory_covered_elsewhere", {}).get("kjw8", {}))
    missing_ng = sorted(n for n in nygov_by_name if n not in used_ng and n not in covered_ng)
    missing_kj = sorted(n for n in kjw8_by_name if n not in used_kj and n not in covered_kj)
    if missing_ng or missing_kj:
        review.append({"reason": "in_directory_not_minted_no_listing_host",
                       "nygov_names": missing_ng, "kjw8_agencies": missing_kj,
                       "note": "Named by ny.gov or kjw8-pupa but not minted as its own entity in "
                               "this batch: no host of its own in the state agency listing. Some are "
                               "sub-units whose hosts the listing files under a parent agency (kept "
                               "there as microsites, with a note)."})
    return records, review


def _load_list(path: pathlib.Path, key: str) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))[key]


def main() -> int:
    for p in (sc.LISTING, NYGOV, KJW8):
        if not p.exists():
            print(f"[gated] missing {p}; copy it from data/ai-readability/sources/<date>/ and retry.",
                  file=sys.stderr)
            return 2
    abo = json.loads(RECORDS_ABO.read_text(encoding="utf-8"))["records"] if RECORDS_ABO.exists() else []
    records, review = build_records(sc.read_listing(), _load_list(NYGOV, "agencies"),
                                    _load_list(KJW8, "agencies"), sc.load_curation(),
                                    sc.base_entities(), [r for r in abo if r.get("_expect_new")])
    OUT.write_text(json.dumps({"records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    sc.write_review_section(
        "state_agencies", review,
        "State agency listing codes and hosts (sync_nygov.py) not minted or not applied.")
    minted = sum(1 for r in records if r.get("_expect_new"))
    print(f"{minted} state agency records, {len(records) - minted} existing-entity enrichments, "
          f"{len(review)} review item(s) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
