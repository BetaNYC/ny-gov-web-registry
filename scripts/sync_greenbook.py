"""sync_greenbook.py — attach Greenbook contact SCAFFOLDING to matched registry entities.

Source: NYC Greenbook (Official Directory of the City of New York), NYC Open Data `mdcw-n682`.
        Per-OFFICER rows: one row per person, carrying the agency's name/acronym/website plus an
        office address + phones. STALE: last refreshed 2023-12 — used as a STRUCTURE DONOR only
        (agency-level website/address/phone scaffolding), NEVER as current-officer data.

WHAT THIS DOES (issue #1 user story 10; PR #5 tier gating):
  1. Aggregates the ~2,555 per-officer rows into ~123 distinct agencies (Agency Name + acronym).
     Officer identities (First/Last Name, Office Title) are DELIBERATELY DROPPED — the registry
     has no person structure and we do not import current officers from a 2023 export.
  2. Reconciles each aggregated agency name against the registry via scripts/reconcile.py
     (nycresolver, tier-gated). Only EXACT, same-government_level matches ("attach") receive
     enrichment; fuzzy / cross-level / no-match go to the review report, never applied, and
     unmatched agencies are NEVER minted as new entities (Greenbook divisions are not entities).
  3. Emits, for each attached agency:
       - agency website  -> a web_properties CANDIDATE, but only when its registrable host
         DIFFERS from every domain the entity already owns (www/www1 normalized) — that is a
         "legacy-domain lead" (role "legacy", flagged unverified). The current primary is NEVER
         overwritten; a website equal to a known domain adds nothing.
       - office address + agency phone -> contact_details[] entries, each with a staleness note.
  4. Writes:
       - data/greenbook_enrichment.json     (committed) — entity-id-keyed enrichment that
         build_registry.py applies (additive, idempotent, deduped).
       - data/greenbook_reconciliation.json (committed) — the human-review report:
         attached / review-fuzzy / review-cross-level / unmatched, with scores.

ACCESS GATE: this script does NOT fetch. Place the gated export at
data/cache/greenbook_mdcw-n682.csv and run sync_moda.py first (the match target is built
in-memory from seed + records_moda.json + curation — deterministically UN-enriched, so the
web-lead comparison never sees its own prior output). Run: python scripts/sync_greenbook.py
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import sys
from collections import Counter

from build_registry import build_unenriched
from reconcile import NYCRESOLVER_AVAILABLE, build_registry_matcher, reconcile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
SOURCE = CACHE / "greenbook_mdcw-n682.csv"
ENRICHMENT_OUT = ROOT / "data" / "greenbook_enrichment.json"
REPORT_OUT = ROOT / "data" / "greenbook_reconciliation.json"

# The export's own currency. Everything Greenbook-derived is stamped with this so the registry
# never impersonates currency it does not have (user story 10).
GREENBOOK_ASOF = "2023-12"
STALE_NOTE = f"Greenbook {GREENBOOK_ASOF} (stale; agency scaffolding, not current officer data)"

# Greenbook Section -> source government_level for the tier gate. City agencies are "nyc";
# County / Courts have no clean registry level, so they never satisfy the same-level gate and
# their matches (if any) route to review, never attach.
_SECTION_LEVEL = {"City": "nyc"}


def host_of(url: str) -> str:
    """Registrable host of a Greenbook website value (handles bare 'NYC.gov/html/x' and full URLs)."""
    h = re.sub(r"^https?://", "", (url or "").strip(), flags=re.I)
    h = h.split("/")[0].strip().lower()
    return h


def registrable(host: str) -> str:
    """Strip a leading www / www1 / www2 label so 'www.nyc.gov' and 'nyc.gov' compare equal."""
    return re.sub(r"^www\d*\.", "", (host or "").strip().lower())


def _most_common(values: list[str]) -> str:
    vals = [v.strip() for v in values if v and v.strip()]
    return Counter(vals).most_common(1)[0][0] if vals else ""


def aggregate_agencies(rows: list[dict]) -> list[dict]:
    """Collapse per-officer rows into one record per agency. Officer identity is dropped."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        key = ((r.get("Agency Name") or "").strip(), (r.get("Agency Acronym") or "").strip())
        if key[0]:
            groups.setdefault(key, []).append(r)

    agencies: list[dict] = []
    for (name, acronym), grp in groups.items():
        section = _most_common([r.get("Section", "") for r in grp])
        website = _most_common([r.get("Agency Website", "") for r in grp])
        phone = _most_common([r.get("Agency Primary Phone", "") for r in grp]) \
            or _most_common([r.get("Phone 1", "") for r in grp])
        # Representative office address: the most common full-address tuple across the agency's rows.
        addr_tuples = [
            (r.get("Address", "").strip(), r.get("City", "").strip(),
             r.get("State", "").strip(), r.get("Zip Code", "").strip())
            for r in grp
            if r.get("Address", "").strip()
        ]
        address = ""
        if addr_tuples:
            street, city, state, zipc = Counter(addr_tuples).most_common(1)[0][0]
            address = re.sub(r"\s+", " ", f"{street}, {city}, {state} {zipc}").strip().strip(",").strip()
        agencies.append({
            "agency_name": name,
            "acronym": acronym,
            "section": section,
            "website": website,
            "address": address,
            "phone": phone,
            "row_count": len(grp),
        })
    agencies.sort(key=lambda a: a["agency_name"].lower())
    return agencies


def greenbook_contact_details(agency: dict) -> list[dict]:
    """Address + phone as Popolo contact_details, each staleness-flagged."""
    cd: list[dict] = []
    if agency.get("address"):
        cd.append({"type": "address", "value": agency["address"], "note": STALE_NOTE + "; office address"})
    if agency.get("phone"):
        cd.append({"type": "voice", "value": agency["phone"], "note": STALE_NOTE + "; agency primary phone"})
    return cd


def web_candidate(agency_website: str, entity_web_properties: list[dict]) -> dict | None:
    """A legacy-domain lead IFF the Greenbook host differs (www-normalized) from every domain the
    entity already owns. Never overwrites the primary; returns None when known or empty."""
    host = host_of(agency_website)
    if not host:
        return None
    owned = {registrable(w.get("domain", "")) for w in entity_web_properties}
    if registrable(host) in owned:
        return None
    primary = next((w.get("domain") for w in entity_web_properties if w.get("role") == "primary"), None)
    note = (f"{STALE_NOTE}; agency website field. Differs from current primary "
            f"'{primary}' — UNVERIFIED legacy-domain lead, verify before treating as canonical.")
    return {"domain": host, "role": "legacy", "valid_from": None, "valid_to": None, "notes": note}


def main() -> int:
    if not SOURCE.exists():
        print(f"[gated] no Greenbook export at {SOURCE}\n"
              f"        place the mdcw-n682 export there (manual, gated) and retry.", file=sys.stderr)
        return 2

    with SOURCE.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    agencies = aggregate_agencies(rows)

    # Un-enriched match target (seed + moda + curation), built in-memory so the reconciliation is
    # deterministic regardless of whether registry.json already carries Greenbook leads.
    entities, _ = build_unenriched()
    by_id = {e["id"]: e for e in entities}
    matcher, id_to_level = build_registry_matcher(entities)

    named = [(a["agency_name"], _SECTION_LEVEL.get(a["section"], "other")) for a in agencies]
    decisions = reconcile(matcher, id_to_level, named)

    enrichments: list[dict] = []
    report = {"attached": [], "review_fuzzy": [], "review_cross_level": [], "unmatched": []}
    by_name = {a["agency_name"]: a for a in agencies}

    for d in decisions:
        agency = by_name[d.source_name]
        base = {
            "greenbook_agency": d.source_name, "acronym": agency["acronym"],
            "section": agency["section"], "confidence_score": d.confidence_score,
            "confidence_tier": d.confidence_tier, "match_type": d.match_type,
            "entity_id": d.entity_id, "matched_name": d.matched_name,
            "matched_government_level": d.matched_level,
        }
        if d.decision == "attach":
            entity = by_id[d.entity_id]
            cd = greenbook_contact_details(agency)
            wc = web_candidate(agency["website"], entity.get("web_properties", []))
            enrichments.append({
                "entity_id": d.entity_id,
                "matched_from": {"source": "greenbook", "agency_name": d.source_name,
                                 "match_type": d.match_type, "confidence_score": d.confidence_score,
                                 "confidence_tier": d.confidence_tier, "as_of": GREENBOOK_ASOF},
                "add_provenance_source": "greenbook",
                "contact_details": cd,
                "web_candidates": [wc] if wc else [],
            })
            report["attached"].append({**base, "web_lead": wc["domain"] if wc else None,
                                       "contact_details_added": len(cd)})
        elif d.reason == "cross_government_level":
            report["review_cross_level"].append(base)
        elif d.reason == "fuzzy":
            report["review_fuzzy"].append(base)
        else:
            report["unmatched"].append(base)

    ENRICHMENT_OUT.write_text(json.dumps({
        "_generated_from": "sync_greenbook.py",
        "source": "NYC Greenbook mdcw-n682",
        "greenbook_as_of": GREENBOOK_ASOF,
        "nycresolver_available": NYCRESOLVER_AVAILABLE,
        "enrichments": enrichments,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    stats = {k: len(v) for k, v in report.items()}
    REPORT_OUT.write_text(json.dumps({
        "_generated_from": "sync_greenbook.py",
        "greenbook_as_of": GREENBOOK_ASOF,
        "nycresolver_available": NYCRESOLVER_AVAILABLE,
        "distinct_agencies": len(agencies),
        "greenbook_rows": len(rows),
        "stats": stats,
        "report": report,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"greenbook: {len(rows)} rows -> {len(agencies)} agencies; "
          f"nycresolver={'yes' if NYCRESOLVER_AVAILABLE else 'FALLBACK(exact-only)'}; "
          f"attached={stats['attached']} review_fuzzy={stats['review_fuzzy']} "
          f"review_cross_level={stats['review_cross_level']} unmatched={stats['unmatched']}")
    print(f"  -> {ENRICHMENT_OUT.name} ({len(enrichments)} enrichment(s)) + {REPORT_OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
