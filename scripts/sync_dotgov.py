"""sync_dotgov.py — New York county, city, town and village website LEADS from CISA's .gov list.

Source: CISA .gov domain data, `current-full.csv` (github.com/cisagov/dotgov-data).
        Columns (verified on the 2026-11-06 snapshot,
        data/ai-readability/sources/2026-11-06/cisa-dotgov-current-full.csv):
            Domain name, Domain type, Organization name, Suborganization name, City, State,
            Security contact email
        The security contact email is never read.

What this produces is a list of LEADS, not web properties (growth plan § Finding and verifying
websites). A .gov registration says an organization registered a name; it does not say the name
is that government's main site, and one government can hold several (fire, police, courts).
sync_nys_localities.py turns a lead into a `primary` web property only when a second,
independent lead names the same host.

Classification is by the organization name, a heuristic that sources-statewide.md § 7 documents
(CISA files every municipality under Domain type "City", and mis-files a few under "County"):
  - "Town of X" / "Village of X" / "City of X" (also "Inc."/"Incorporated Village of X", and a
    trailing ", NY" / ", New York") -> tier town / village / city, name X
  - "X County" / "County of X" -> tier county, name X
  - anything else (police departments, museums, NYC agencies, ...) -> not a lead
Only rows with State "NY", Domain type "City" or "County", and an empty Suborganization name are
read: a suborganization row is a department's domain, not the government's own.

INPUT (never fetches): data/cache/cisa_dotgov_current-full.csv (a copy of the dated snapshot)
OUTPUT: data/cache/leads_dotgov.json (read by sync_nys_localities.py)
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import statewide_common as sc  # noqa: E402

SOURCE = sc.CACHE / "cisa_dotgov_current-full.csv"
OUT = sc.CACHE / "leads_dotgov.json"
SNAPSHOT = "data/ai-readability/sources/2026-11-06/cisa-dotgov-current-full.csv"
DOMAIN_TYPES = ("City", "County")

_TIER_RE = re.compile(r"^(?:(?:inc\.?|incorporated)\s+)?(town|village|city)\s+of\s+(?:the\s+)?(.+)$", re.IGNORECASE)
_COUNTY_OF_RE = re.compile(r"^county\s+of\s+(.+)$", re.IGNORECASE)
_X_COUNTY_RE = re.compile(r"^(.+?)\s+county$", re.IGNORECASE)
_STATE_TAIL_RE = re.compile(r",?\s*(?:ny|n\.y\.|new york(?: state)?)\.?$", re.IGNORECASE)


def classify(org: str) -> tuple[str, str] | None:
    """Organization name -> (tier, place name) or None when it does not name a government."""
    s = re.sub(r"\s+", " ", (org or "").strip())
    s = _STATE_TAIL_RE.sub("", s).strip().rstrip(",").strip()
    if not s:
        return None
    m = _TIER_RE.match(s)
    if m:
        return m.group(1).lower(), m.group(2).strip()
    m = _COUNTY_OF_RE.match(s)
    if m:
        return "county", m.group(1).strip()
    m = _X_COUNTY_RE.match(s)
    if m and not re.search(r"\b(of|department|office|police|sheriff|board|district)\b", m.group(1), re.IGNORECASE):
        return "county", m.group(1).strip()
    return None


def build_leads(rows: list[dict]) -> tuple[list[dict], dict]:
    """Pure: CISA rows -> (leads, counts). Deterministic order (by domain)."""
    leads: list[dict] = []
    counts = {"ny_rows": 0, "read": 0, "suborganization_skipped": 0, "not_a_government_name": 0}
    for r in rows:
        if (r.get("State") or "").strip() != "NY":
            continue
        counts["ny_rows"] += 1
        if (r.get("Domain type") or "").strip() not in DOMAIN_TYPES:
            continue
        counts["read"] += 1
        if (r.get("Suborganization name") or "").strip():
            counts["suborganization_skipped"] += 1
            continue
        org = (r.get("Organization name") or "").strip()
        tier_name = classify(org)
        if tier_name is None:
            counts["not_a_government_name"] += 1
            continue
        leads.append({"domain": (r.get("Domain name") or "").strip().lower(),
                      "tier": tier_name[0], "name": tier_name[1], "organization": org,
                      "domain_type": r["Domain type"].strip(),
                      "city": (r.get("City") or "").strip() or None})
    leads.sort(key=lambda x: (x["domain"], x["organization"]))
    counts["leads"] = len(leads)
    return leads, counts


def main() -> int:
    if not SOURCE.exists():
        print(f"[gated] no CISA list at {SOURCE}\n        copy {SNAPSHOT} there and retry.", file=sys.stderr)
        return 2
    with SOURCE.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = {"Domain name", "Domain type", "Organization name", "Suborganization name",
                   "City", "State"} - set(reader.fieldnames or [])
        if missing:
            print(f"error: {SOURCE} lacks column(s) {sorted(missing)}", file=sys.stderr)
            return 2
        rows = list(reader)
    leads, counts = build_leads(rows)
    OUT.write_text(json.dumps({"_source": SNAPSHOT, "counts": counts, "leads": leads},
                              indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{counts['ny_rows']} NY rows; {counts['read']} City/County rows read; "
          f"{counts['leads']} leads ({counts['suborganization_skipped']} suborganization rows and "
          f"{counts['not_a_government_name']} non-government names skipped) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
