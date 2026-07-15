"""sync_wikidata.py — attach Wikidata QIDs to registry entities via DOMAIN-ANCHORED matching.

Source: Wikidata Query Service (WDQS) SPARQL endpoint https://query.wikidata.org/sparql
        A SPARQL result set of NYC government organizations, captured 2026-07-15 (operator-
        authorized fetch) into data/cache/wikidata_nyc_gov_orgs.json. Wikidata content is CC0.
        The exact query is recorded in docs/freshness.md § Wikidata for reproducibility.

RESULT SHAPE (SPARQL JSON, verified against the cache — head.vars = item, itemLabel, website,
altLabel): one binding PER (item × altLabel × website) combination, so an item appears in many
rows. Fields read (all others ignored):
  - item.value    -> QID URI 'http://www.wikidata.org/entity/Q<n>'   (the identifier)
  - itemLabel     -> English preferred label                          (the name signal)
  - altLabel      -> one English alias (repeated across rows)         (extra name signals)
  - website       -> P856 official website URL                        (the DOMAIN signal)

WHY DOMAIN-ANCHORED (issue #1 phase-3 scope; PR #5 tier-gating carried forward):
  A QID is only auto-attached on a **distinctive domain** match — the QID's P856 host equals a
  domain some registry entity owns (host-only, www-normalized), AND that domain is owned by
  exactly ONE entity. This is the trap the whole phase turns on: sync_moda stores host-only web
  properties, so 154 registry entities all carry `nyc.gov`. A QID whose P856 is
  `https://www.nyc.gov/site/mome/index.page` reduces to host `nyc.gov`, which matches 154
  entities — that is NOT an identifying signal and must NEVER auto-attach. Apex-shared hosts fall
  through to NAME matching, which only ever PROPOSES (review), never auto-attaches.

TIER GATING (mirrors reconcile.py's philosophy; domain replaces nycresolver as the strong signal):
  1. Distinctive domain (P856 host owned by exactly one entity)  -> "auto": attach
     identifiers[]{scheme:"wikidata", identifier:QID} + provenance source "wikidata".
     Applied in the build via build_registry.apply_wikidata_enrichment (additive, idempotent).
  2. Distinctive domains that resolve to DIFFERENT entities       -> "review" (domain_conflict).
  3. No distinctive domain, exact name/alias match to ONE entity  -> "proposal": report only,
     never auto-attached (spec: never auto-attach on name alone).
  4. Exact name/alias match to MULTIPLE entities                  -> "review" (ambiguous_name).
  5. Only an apex-shared domain hit, no name match                -> "review" (apex_shared_only) —
     the QID is a NYC org but not distinguishable; likely a sub-office not in the registry.
  6. Nothing matched                                             -> "unmatched" (not in registry).

  LEGACY-DOMAIN LEADS (spec requirement — "P856 vs web_properties disagreements"): for any matched
  entity (auto or proposal), a QID P856 host that is NOT one of the entity's owned domains and is
  NOT itself an apex-shared registry host is emitted as an UNVERIFIED legacy-domain lead in the
  report (never written to web_properties by this script).

  NAME-DIVERGENCE CAVEAT: a distinctive-domain auto-attach whose Wikidata label/aliases do not
  normalize-match the entity's name/aliases is auto-attached (domain is the strong signal) but
  ALSO flagged `name_divergent` in the report — this surfaces Wikidata's systematic conflation of
  a PLACE item with an OFFICE's website (the four borough items carry their borough-president
  office site as P856), so the operator can review those specific attachments.

OUTPUTS (committed; parallel to the Greenbook seam):
  - data/wikidata_enrichment.json     — entity-id-keyed auto attachments the build applies.
  - data/wikidata_reconciliation.json — the human-review report (proposals, conflicts, leads,
                                        divergence caveats, unmatched), with evidence.

ACCESS GATE: this script does NOT fetch. It reads the committed cache. Re-fetching WDQS requires
explicit operator authorization; document any new query + fetch date in docs/freshness.md and save
the raw result into data/cache/ so the sync layer stays fetch-free and reproducible.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict

from build_registry import build_unenriched

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
SOURCE = CACHE / "wikidata_nyc_gov_orgs.json"
ENRICHMENT_OUT = ROOT / "data" / "wikidata_enrichment.json"
REPORT_OUT = ROOT / "data" / "wikidata_reconciliation.json"

# The fetch date of the cached SPARQL result (operator-authorized). Everything wikidata-derived is
# stamped with it; the query itself lives in docs/freshness.md.
WIKIDATA_ASOF = "2026-07-15"
QID_RE = re.compile(r"^Q\d+$")


def _norm_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def norm_host(url: str) -> str:
    """Host-only, scheme-stripped, www/www\\d*-stripped, lower-cased registrable host of a URL.

    'https://www.nyc.gov/site/mome/index.page' -> 'nyc.gov'; 'http://MTA.info/' -> 'mta.info'.
    Paths are deliberately discarded: nyc.gov/site/<agency> paths all share the apex, so path
    retention would fabricate distinctiveness the domain does not have (the phase-3 trap)."""
    h = re.sub(r"^https?://", "", (url or "").strip(), flags=re.I)
    h = h.split("/")[0].split("?")[0].strip().lower()
    return re.sub(r"^www\d*\.", "", h)


def qid_of(item_uri: str) -> str:
    """The bare QID from a Wikidata entity URI ('http://www.wikidata.org/entity/Q60' -> 'Q60')."""
    return (item_uri or "").rsplit("/", 1)[-1].strip()


def parse_candidates(bindings: list[dict]) -> list[dict]:
    """Collapse the per-(item×altLabel×website) SPARQL rows into one candidate per QID.

    Pure. Returns a deterministically-sorted list of
    {qid, label, aliases:[...sorted], hosts:[...sorted]}. Bindings whose item URI does not yield a
    well-formed QID (^Q\\d+$) are skipped (defensive; the cache is clean)."""
    labels: dict[str, str] = {}
    aliases: dict[str, set[str]] = defaultdict(set)
    hosts: dict[str, set[str]] = defaultdict(set)
    order: list[str] = []
    for b in bindings:
        qid = qid_of(b.get("item", {}).get("value", ""))
        if not QID_RE.match(qid):
            continue
        if qid not in aliases and qid not in labels and qid not in hosts:
            order.append(qid)
        lbl = b.get("itemLabel", {}).get("value", "").strip()
        if lbl and qid not in labels:
            labels[qid] = lbl
        alt = b.get("altLabel", {}).get("value", "").strip()
        if alt:
            aliases[qid].add(alt)
        host = norm_host(b.get("website", {}).get("value", ""))
        if host:
            hosts[qid].add(host)
    seen: list[str] = []
    for qid in order:
        if qid not in seen:
            seen.append(qid)
    return [{
        "qid": qid,
        "label": labels.get(qid, ""),
        "aliases": sorted(aliases.get(qid, set())),
        "hosts": sorted(hosts.get(qid, set())),
    } for qid in seen]


def build_indexes(entities: list[dict]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return (domain_index, name_index).

    domain_index: normalized host -> sorted list of entity ids that own that host. A host mapping
    to exactly one id is DISTINCTIVE; a host mapping to many is APEX-SHARED (nyc.gov et al.).
    name_index:   normalized name/short_name/other_name -> sorted list of entity ids carrying it.
    Both built from the UN-enriched entities so classification is deterministic across rebuilds."""
    dom: dict[str, set[str]] = defaultdict(set)
    nam: dict[str, set[str]] = defaultdict(set)
    for e in entities:
        for w in e.get("web_properties", []):
            d = norm_host(w.get("domain", ""))
            if d:
                dom[d].add(e["id"])
        for nm in [e.get("name"), e.get("short_name")] + [o.get("name") for o in e.get("other_names", [])]:
            key = _norm_name(nm)
            if key:
                nam[key].add(e["id"])
    return ({d: sorted(v) for d, v in dom.items()}, {n: sorted(v) for n, v in nam.items()})


def _name_matches_entity(cand: dict, entity: dict) -> bool:
    """True if any of the candidate's label/aliases normalize-equals any of the entity's names."""
    cand_names = {_norm_name(n) for n in [cand["label"], *cand["aliases"]] if _norm_name(n)}
    ent_names = {_norm_name(n) for n in
                 [entity.get("name"), entity.get("short_name")]
                 + [o.get("name") for o in entity.get("other_names", [])] if _norm_name(n)}
    return bool(cand_names & ent_names)


def classify(cand: dict, domain_index: dict[str, list[str]], name_index: dict[str, list[str]],
             by_id: dict[str, dict]) -> dict:
    """Apply the domain-anchored tier gate to one QID candidate. Pure over the indexes.

    Returns a decision dict: {qid, label, decision, reason, entity_id, evidence, ...}. `decision`
    is one of auto|proposal|review|unmatched; `reason` names the specific rule that fired."""
    # Distinctive vs apex-shared domain hits.
    distinctive: dict[str, str] = {}   # host -> the single entity id
    apex_hosts: list[str] = []
    for h in cand["hosts"]:
        owners = domain_index.get(h)
        if not owners:
            continue
        if len(owners) == 1:
            distinctive[h] = owners[0]
        else:
            apex_hosts.append(h)

    distinct_entities = sorted(set(distinctive.values()))

    base = {"qid": cand["qid"], "label": cand["label"], "aliases": cand["aliases"],
            "hosts": cand["hosts"], "apex_shared_hosts": sorted(apex_hosts)}

    # Rule 1/2: distinctive domain.
    if distinct_entities:
        if len(distinct_entities) > 1:
            return {**base, "decision": "review", "reason": "domain_conflict",
                    "entity_id": None,
                    "conflict_entities": [{"entity_id": eid, "via_host":
                                           next(h for h, i in distinctive.items() if i == eid)}
                                          for eid in distinct_entities]}
        eid = distinct_entities[0]
        via = next(h for h, i in distinctive.items() if i == eid)
        divergent = not _name_matches_entity(cand, by_id[eid])
        return {**base, "decision": "auto", "reason": "distinctive_domain",
                "entity_id": eid, "matched_via_host": via,
                "matched_name": by_id[eid].get("name"), "name_divergent": divergent}

    # Rule 3/4: name/alias exact match (no distinctive domain).
    name_ids: set[str] = set()
    for nm in [cand["label"], *cand["aliases"]]:
        name_ids.update(name_index.get(_norm_name(nm), []))
    name_ids_sorted = sorted(name_ids)
    if len(name_ids_sorted) == 1:
        eid = name_ids_sorted[0]
        return {**base, "decision": "proposal", "reason": "exact_name",
                "entity_id": eid, "matched_name": by_id[eid].get("name")}
    if len(name_ids_sorted) > 1:
        return {**base, "decision": "review", "reason": "ambiguous_name", "entity_id": None,
                "candidate_entities": [{"entity_id": i, "name": by_id[i].get("name")}
                                       for i in name_ids_sorted]}

    # Rule 5/6.
    if apex_hosts:
        return {**base, "decision": "review", "reason": "apex_shared_only", "entity_id": None}
    return {**base, "decision": "unmatched", "reason": "no_match", "entity_id": None}


def legacy_domain_leads(decision: dict, by_id: dict[str, dict],
                        domain_index: dict[str, list[str]]) -> list[dict]:
    """P856 hosts on a MATCHED entity (auto/proposal) that the entity does not already own and that
    are not apex-shared registry hosts -> unverified legacy-domain leads (report only)."""
    eid = decision.get("entity_id")
    if eid is None or decision["decision"] not in ("auto", "proposal"):
        return []
    owned = {norm_host(w.get("domain", "")) for w in by_id[eid].get("web_properties", [])}
    leads = []
    for h in decision["hosts"]:
        if h in owned:
            continue
        owners = domain_index.get(h, [])
        if len(owners) > 1:  # apex-shared -> not a useful lead
            continue
        leads.append({"entity_id": eid, "qid": decision["qid"], "lead_domain": h,
                      "note": (f"Wikidata P856 host '{h}' for {decision['qid']} is not among "
                               f"{sorted(owned)} owned by '{eid}' — UNVERIFIED legacy-domain lead, "
                               f"verify before adding to web_properties.")})
    return leads


def main() -> int:
    if not SOURCE.exists():
        print(f"[gated] no Wikidata SPARQL cache at {SOURCE}\n"
              f"        place the WDQS result there (operator-authorized) and retry.", file=sys.stderr)
        return 2

    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    bindings = data.get("results", {}).get("bindings", [])
    candidates = parse_candidates(bindings)

    entities, _ = build_unenriched()
    by_id = {e["id"]: e for e in entities}
    domain_index, name_index = build_indexes(entities)

    enrichments: list[dict] = []
    report = {"auto_attached": [], "proposals": [], "review_domain_conflict": [],
              "review_ambiguous_name": [], "review_apex_shared": [], "unmatched": [],
              "legacy_domain_leads": [], "name_divergent_caveats": []}

    for cand in candidates:
        d = classify(cand, domain_index, name_index, by_id)
        report["legacy_domain_leads"].extend(legacy_domain_leads(d, by_id, domain_index))

        if d["decision"] == "auto":
            enrichments.append({
                "entity_id": d["entity_id"],
                "matched_from": {"source": "wikidata", "qid": d["qid"], "label": d["label"],
                                 "match_type": "distinctive_domain",
                                 "matched_via_host": d["matched_via_host"], "as_of": WIKIDATA_ASOF},
                "add_provenance_source": "wikidata",
                "identifiers": [{"scheme": "wikidata", "identifier": d["qid"]}],
            })
            report["auto_attached"].append({
                "qid": d["qid"], "label": d["label"], "entity_id": d["entity_id"],
                "matched_name": d["matched_name"], "matched_via_host": d["matched_via_host"],
                "name_divergent": d["name_divergent"]})
            if d["name_divergent"]:
                report["name_divergent_caveats"].append({
                    "qid": d["qid"], "label": d["label"], "entity_id": d["entity_id"],
                    "matched_name": d["matched_name"], "matched_via_host": d["matched_via_host"],
                    "note": ("distinctive-domain auto-attach whose Wikidata label does not match "
                             "the entity name — likely a place/office conflation; operator review "
                             "recommended before treating the QID as an identity assertion.")})
        elif d["decision"] == "proposal":
            report["proposals"].append({
                "qid": d["qid"], "label": d["label"], "entity_id": d["entity_id"],
                "matched_name": d["matched_name"], "reason": d["reason"],
                "note": "exact name/alias match, no distinctive domain — operator-confirm before attaching."})
        elif d["reason"] == "domain_conflict":
            report["review_domain_conflict"].append(
                {"qid": d["qid"], "label": d["label"], "conflict_entities": d["conflict_entities"]})
        elif d["reason"] == "ambiguous_name":
            report["review_ambiguous_name"].append(
                {"qid": d["qid"], "label": d["label"], "candidate_entities": d["candidate_entities"]})
        elif d["reason"] == "apex_shared_only":
            report["review_apex_shared"].append(
                {"qid": d["qid"], "label": d["label"], "apex_shared_hosts": d["apex_shared_hosts"]})
        else:
            report["unmatched"].append(
                {"qid": d["qid"], "label": d["label"], "hosts": d["hosts"]})

    ENRICHMENT_OUT.write_text(json.dumps({
        "_generated_from": "sync_wikidata.py",
        "source": "Wikidata Query Service (WDQS) — NYC gov orgs",
        "wikidata_as_of": WIKIDATA_ASOF,
        "enrichments": enrichments,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    stats = {k: len(v) for k, v in report.items()}
    REPORT_OUT.write_text(json.dumps({
        "_generated_from": "sync_wikidata.py",
        "wikidata_as_of": WIKIDATA_ASOF,
        "distinct_qids": len(candidates),
        "qids_with_website": sum(1 for c in candidates if c["hosts"]),
        "stats": stats,
        "report": report,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wikidata: {len(bindings)} bindings -> {len(candidates)} QIDs "
          f"({stats['auto_attached']} auto, {stats['proposals']} proposals, "
          f"{stats['review_domain_conflict']} domain-conflict, "
          f"{stats['review_ambiguous_name']} ambiguous-name, "
          f"{stats['review_apex_shared']} apex-only, {stats['unmatched']} unmatched; "
          f"{stats['legacy_domain_leads']} legacy leads, "
          f"{stats['name_divergent_caveats']} divergent caveats)")
    print(f"  -> {ENRICHMENT_OUT.name} ({len(enrichments)} enrichment(s)) + {REPORT_OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
