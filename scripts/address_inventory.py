"""address_inventory.py — one row per address to scan (issue #41, epic #47).

WHY THIS EXISTS
  websites.json is one row per host. A scan also needs the addresses below a host (sections,
  e.g. a community board's page on www.nyc.gov) and the ones a host list never names (a data
  portal, a sub-site, a Council API). This script joins every source into
  data/ai-readability/addresses.csv. Nothing is guessed: no `data.`, `gis.` or `api.` prefix is
  ever tried on a domain. Every address comes from a list, a file already fetched, or a public
  certificate log (and then only after it answered).

SOURCES, in order (the first source to name an address is its `source`; later ones are added)
  websites.json          every host row without a host issue, and every page in `ny_page_urls`
                         (page-corrections.csv applied: a gone page is dropped, a moved page is
                         listed at its new address)
  deep-page list         deep-pages-extra.csv (community-board pages on www.nyc.gov)
  data portal registry   the portal rows websites.json carries (`ny_portal_urls`)
  linked from fetched files
                         sources/2026-11-10/linked-from-fetched-files.csv, written by
                         `--fetched RUN_DIR` from a private run's results.jsonl. Only the
                         address and the listed host it belongs to are kept
  BetaNYC contact files  sources/2026-11-10/contact-websites.csv, written by `--contacts DIR`
                         from BetaNYC's private contact files. Only the "Website:" address and
                         the registry id are kept; nothing else is copied
  certificate logs       cert-log-addresses.csv, written by cert_log_names.py --export

KIND  main site | sub-site | section | data portal | API (see docs/ai-readability/addresses.md)

Run:
  python scripts/address_inventory.py
  python scripts/address_inventory.py --contacts ~/Code/BetaNYC_AI_Workspace/team/contacts
  python scripts/address_inventory.py --fetched <private run dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ai_readability_export as ex  # noqa: E402
import ai_readability_websites as ws  # noqa: E402
import cert_log_names as cl  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
AIR = ROOT / "data" / "ai-readability"
SRC = AIR / "sources" / "2026-11-10"
PATHS = {
    "websites": AIR / "websites.json",
    "registry": ROOT / "data" / "registry.json",
    "deep_extra": AIR / "deep-pages-extra.csv",
    "page_corrections": AIR / "page-corrections.csv",
    "fetched": SRC / "linked-from-fetched-files.csv",
    "contacts": SRC / "contact-websites.csv",
    "cert_logs": AIR / "cert-log-addresses.csv",
}
OUT = AIR / "addresses.csv"
COLUMNS = ("address", "owning_body_id", "owning_body", "kind", "level_or_entity_type", "source",
           "date_found", "notes", "platform")
SOURCE_LABELS = ("websites.json", "deep-page list", "data portal registry",
                 "linked from fetched files", "BetaNYC contact files", "certificate logs")
KINDS = ws.KINDS
WEBSITES_DATE = "2026-11-06"     # the date of the source snapshots websites.json is built from
NEW_SOURCES_DATE = "2026-11-10"  # portal registry snapshot, contact files read
SCAN_DATE = "2026-11-06"         # the run whose fetched files were read
BOROUGHS = {"MN": "manhattan", "BX": "bronx", "BK": "brooklyn", "QN": "queens", "SI": "staten-island"}


def addr_key(url: str):
    """Same address: `www.` ignored, trailing `/` ignored, fragment ignored, scheme ignored."""
    p = urllib.parse.urlparse(url if "//" in url else "https://" + url)
    return ws._key((p.hostname or "").lower().rstrip(".")), (p.path or "").rstrip("/"), p.query


def board_registry_id(code: str):
    """`BK08` -> `brooklyn-community-board-8`; None when it is not a board code."""
    m = re.fullmatch(r"(MN|BX|BK|QN|SI)0?(\d{1,2})", (code or "").strip().upper())
    return "{}-community-board-{}".format(BOROUGHS[m.group(1)], int(m.group(2))) if m else None


# ------------------------------------------------------------------------------ build
class Inventory:
    def __init__(self, registry):
        self.rows, self.by_key = [], {}
        self.names = {e["id"]: e["name"] for e in registry["entities"]}
        self.levels = {e["id"]: e.get("government_level") for e in registry["entities"]}

    def add(self, address, kind, source, date_found, ids=(), names=(), level=None, notes="",
            platform=""):
        assert kind in KINDS, kind
        assert source in SOURCE_LABELS, source
        k = addr_key(address)
        if k in self.by_key:
            r = self.by_key[k]
            if source not in r["source"].split("; "):
                r["source"] += "; " + source
            if source == "data portal registry" and kind in ("data portal", "API"):
                r["kind"] = kind                      # the portal registry says what it is
                if level and level not in r["level_or_entity_type"].split("; "):
                    r["level_or_entity_type"] = "; ".join(x for x in (r["level_or_entity_type"], level) if x)
                if notes:
                    r["notes"] = "; ".join(x for x in (r["notes"], notes) if x)
                if platform and not r["platform"]:
                    r["platform"] = platform
            return False
        ids = [i for i in ids if i]
        names = list(names) or [self.names[i] for i in ids if i in self.names]
        if not level:
            level = "; ".join(sorted({self.levels[i] for i in ids if self.levels.get(i)}))
        r = {"address": address, "owning_body_id": "; ".join(ids), "owning_body": "; ".join(names),
             "kind": kind, "level_or_entity_type": level or "", "source": source,
             "date_found": date_found, "notes": notes, "platform": platform or ""}
        self.by_key[k] = r
        self.rows.append(r)
        return True


def _host_kind(row, by_host):
    """`sub-site` when a listed host with an owner in common is this host's parent
    (dot.ny.gov owns able.dot.ny.gov); otherwise the row's own kind."""
    if row["ny_kind"] != "main site":
        return row["ny_kind"]
    host = ws._key(row["ny_initial_domain"].lower())
    ids = set(row.get("ny_entity_ids") or [])
    labels = host.split(".")
    for i in range(1, len(labels) - 1):
        parent = by_host.get(".".join(labels[i:]))
        if parent and not parent.get("ny_host_issue") and ids & set(parent.get("ny_entity_ids") or []):
            return "sub-site"
    return "main site"


def build(paths=PATHS) -> list:
    registry = json.loads(pathlib.Path(paths["registry"]).read_text(encoding="utf-8"))
    websites = json.loads(pathlib.Path(paths["websites"]).read_text(encoding="utf-8"))
    inv = Inventory(registry)
    by_host = {ws._key(r["ny_initial_domain"].lower()): r for r in websites}
    live = [r for r in websites if not r.get("ny_host_issue")]

    # 1. websites.json: hosts, then their pages
    for r in live:
        portal_only = r["ny_kind"] != "main site"
        if portal_only:
            continue                                   # step 3 lists the portal's own URLs
        ids = r.get("ny_entity_ids") or []
        names = [r["agency"]] if r.get("agency") else (r.get("ny_names") or [])
        inv.add(r["initial_url"], _host_kind(r, by_host), "websites.json", WEBSITES_DATE,
                ids=ids, names=names, level="; ".join(r.get("ny_levels") or []))
    fixes = ws.read_page_corrections(paths["page_corrections"])
    owners = ws.page_owners()
    for r in live:
        for url in r.get("ny_page_urls") or []:
            fix = fixes.get(ws._page_key(url))
            if fix and fix["issue"] == "gone":
                continue
            o = owners.get(url) or {}
            note = ""
            if fix:
                note, url = "listed at {}; moved (page-corrections.csv)".format(url), fix["moved_to"].strip()
            inv.add(url, "section", "websites.json", WEBSITES_DATE, ids=o.get("entity_ids") or [],
                    names=o.get("names") or [], notes=note)

    # 2. deep-page list
    with open(paths["deep_extra"], newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            m = re.search(r"\(([A-Z]{2}\d{2})\)", r.get("name") or "")
            bid = board_registry_id(m.group(1)) if m else None
            inv.add(r["url"].strip(), "section", "deep-page list", r.get("checked") or WEBSITES_DATE,
                    ids=[bid] if bid else [], names=[r["name"].strip()] if r.get("name") else [])

    # 3. data portal registry (snapshot read by ai_readability_websites.py)
    for rec in ws.read_portal_registry(ws.PATHS["portal_registry"]):
        url = rec["url"].split("#", 1)[0]
        kind = "API" if rec["api"] else "data portal"
        rid = rec.get("registry_id")
        notes = []
        if rec.get("status") == "UNCONFIRMED":
            notes.append("portal registry status UNCONFIRMED")
        host_row = by_host.get(ws._key(rec["host"]))
        if host_row and host_row.get("ny_hosting") == "vendor":
            notes.append("vendor-hosted")
        inv.add(url, kind, "data portal registry", NEW_SOURCES_DATE,
                ids=[rid] if rid in inv.names else [], names=[rec["name"]],
                level=rec.get("entity_type"), notes="; ".join(notes),
                platform=(rec.get("platform") or "").strip())

    # 4. linked from fetched files (same registered domain as the listed host)
    if paths.get("fetched") and pathlib.Path(paths["fetched"]).exists():
        with open(paths["fetched"], newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                parent = by_host.get(ws._key(r["listed_host"].lower())) or {}
                inv.add(r["address"], "sub-site", "linked from fetched files", SCAN_DATE,
                        ids=parent.get("ny_entity_ids") or [],
                        names=[parent["agency"]] if parent.get("agency") else (parent.get("ny_names") or []),
                        level="; ".join(parent.get("ny_levels") or []),
                        notes="same registered domain as {}".format(r["listed_host"]))

    # 5. BetaNYC contact files
    if paths.get("contacts") and pathlib.Path(paths["contacts"]).exists():
        with open(paths["contacts"], newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                kind = "section" if urllib.parse.urlparse(r["address"]).path.strip("/") else "main site"
                inv.add(r["address"], kind, "BetaNYC contact files", NEW_SOURCES_DATE,
                        ids=[r["owning_body_id"]], notes=r.get("notes") or "")

    # 6. certificate logs
    if paths.get("cert_logs") and pathlib.Path(paths["cert_logs"]).exists():
        owners_by_domain = {}
        for r in live:
            if r.get("ny_government") is False:
                continue
            owners_by_domain.setdefault(r["base_domain"], []).append(r)
        with open(paths["cert_logs"], newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows = owners_by_domain.get(r["registered_domain"], [])
                apex = [x for x in rows if ws._key(x["ny_initial_domain"].lower()) == r["registered_domain"]]
                pick = apex or (rows if len({tuple(x.get("ny_entity_ids") or []) + (x.get("agency"),)
                                             for x in rows}) == 1 else [])
                o = pick[0] if pick else {}
                inv.add(r["address"], "sub-site", "certificate logs", r["found"],
                        ids=o.get("ny_entity_ids") or [],
                        names=[o["agency"]] if o.get("agency") else (o.get("ny_names") or []),
                        level="; ".join(o.get("ny_levels") or []),
                        notes="found in certificate logs under {}".format(r["registered_domain"])
                        + ("" if o else "; owner not stated (several bodies use this domain)"))
    return inv.rows


def write(rows, path=OUT) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def counts(rows) -> dict:
    """Addresses per first source, and per kind."""
    by_src = {s: 0 for s in SOURCE_LABELS}
    by_kind = {k: 0 for k in KINDS}
    for r in rows:
        by_src[r["source"].split("; ")[0]] += 1
        by_kind[r["kind"]] += 1
    return {"by_source": by_src, "by_kind": by_kind, "total": len(rows)}


# ------------------------------------------------------------------------------ private inputs
_WEBSITE = re.compile(r"^\s*-\s*\*\*Website:\*\*\s*(https?://\S+)", re.M)


def contact_websites(contacts_dir, known_keys) -> list:
    """Website addresses from BetaNYC's contact files that the inventory does not already hold.
    Reads only the "Website:" line and the file's board id or district number; nothing else.
    Community boards map to their registry id; a Council district page to the Council."""
    out = []
    root = pathlib.Path(contacts_dir).expanduser()
    for sub in ("agencies", "electeds", "community-boards"):
        for f in sorted((root / sub).rglob("*.md")):
            text = f.read_text(encoding="utf-8")
            m = _WEBSITE.search(text)
            if not m:
                continue
            url = m.group(1).rstrip(").,;")
            if addr_key(url) in known_keys:
                continue
            bid = re.search(r"^board_id:\s*(\S+)", text, re.M)
            if sub == "community-boards" and bid:
                owner, note = board_registry_id(bid.group(1)), ""
            elif sub == "electeds" and re.match(r"d(\d+)-", f.name):
                owner = "new-york-city-council"
                note = "Council District {} page".format(int(re.match(r"d(\d+)-", f.name).group(1)))
            else:
                continue                                 # an owner we cannot state: left out
            out.append({"address": url, "owning_body_id": owner, "notes": note})
    return out


def fetched_links(run_dir, websites, psl) -> list:
    """Sub-site addresses the earlier scan already fetched: a robots.txt `Sitemap:` line or a
    homepage redirect pointing at another host on the same registered domain. Vendor platforms
    and infrastructure names (cert_log_names.is_infra) are left out. Only the address and the
    listed host are returned; no result is."""
    known = {ws._key(r["ny_initial_domain"].lower()) for r in websites}
    found = {}
    with open(pathlib.Path(run_dir) / "results.jsonl", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            ev, host = r.get("evidence") or {}, r["host"].lower()
            cands = []
            if r["test"] == "W1" and ev.get("first_8kb"):
                cands += re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)", ev["first_8kb"])
            if r["test"] in ("W1", "F4") and ev.get("final_url"):
                cands.append(ev["final_url"])
            for u in cands:
                h = (urllib.parse.urlparse(u).hostname or "").lower().rstrip(".")
                bd = ex.base_domain(host, psl)
                if (not h or ws._key(h) == ws._key(host) or ws._key(h) in known
                        or ex.base_domain(h, psl) != bd or cl._on_vendor(bd) or cl.is_infra(h, bd)):
                    continue
                found.setdefault(h, host)
    return [{"address": "https://{}/".format(h), "listed_host": found[h]} for h in sorted(found)]


def _write_csv(rows, path, cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote {} rows to {}".format(len(rows), path))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contacts", metavar="DIR", help="BetaNYC team/contacts folder (private)")
    ap.add_argument("--fetched", metavar="RUN_DIR", help="a private run folder with results.jsonl")
    a = ap.parse_args(argv)
    if a.fetched:
        websites = json.loads(PATHS["websites"].read_text(encoding="utf-8"))
        _write_csv(fetched_links(a.fetched, websites, ex.load_psl()), PATHS["fetched"],
                   ("address", "listed_host"))
    if a.contacts:
        known = {addr_key(r["address"]) for r in build({**PATHS, "contacts": None})}
        _write_csv(contact_websites(a.contacts, known), PATHS["contacts"],
                   ("address", "owning_body_id", "notes"))
    rows = build()
    write(rows)
    print("wrote {} addresses to {}".format(len(rows), OUT))
    print(json.dumps(counts(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
