"""reconcile.py — tier-gated entity resolution of messy source names against the registry.

Wraps MODA's **nycresolver** (github.com/MODA-NYC/nyc-entity-resolver, Apache-2.0) — the
City's own agency-name matcher — and applies the phase-2 tier-gating rules from issue #1 and
the PR #5 entity-resolution guidance comment (operator-reviewed 2026-07-15).

BUILT AGAINST THE REAL nycresolver INTERFACE (read from the repo source, not guessed):
  - `nycresolver.fetcher.CanonicalRecord.from_row(row)` builds a canonical record from a
    Socrata-shaped row dict; the fields it reads are: record_id, name, acronym,
    alternate_or_former_names, alternate_or_former_acronyms, name_alphabetized,
    organization_type, operational_status, reports_to, url. (fetcher.py)
  - `nycresolver.matcher.Matcher(records)` indexes those records; `.match(value)` returns a
    `MatchResult` exposing `.matched` (score >= 45), `.confidence_score`, `.confidence_tier`
    (exact|high|medium|low|none), `.matched_record_id`, `.matched_canonical_name`,
    `.match_type`, `.needs_review`. (matcher.py)
  - We deliberately do NOT call `build_matcher()` — it fetches the canonical set live from
    Socrata. Live pulls are access-gated, so we build the Matcher OFFLINE from registry
    entities converted to canonical rows. nycresolver runs stdlib-only, no network here.

MATCHING TARGET (the "canonical set"): the registry's own entities — every entity becomes a
canonical row (name -> name, short_name -> acronym, other_names[] -> alternate_or_former_names).
So esd's operator-curated variant names ("Empire State Development Corporation", ...) are in the
match space, and the government_level guard below is what actually rejects the EDC/ESD collision.
matched_record_id is the registry entity `id` (round-trips straight back to the entity).

TIER GATING (PR #5 guidance -> decisions):
  1. Shared {scheme, identifier}          -> auto-merge. NOT handled here (that path lives in
     build_registry.build_entities, which matches identifier-first). Name reconciliation never
     auto-merges entities.
  2. Exact match, SAME government_level    -> "attach" (exact / proposal-grade confidence).
     Greenbook enrichment (contact scaffolding, staleness-flagged) may be attached to the
     matched entity; the match is also written to the review report for audit. No entity merge.
  3. Fuzzy (high/medium/low), same level   -> "review" (reason "fuzzy") — report only, with the
     score; never applied.
  4. Any level mismatch                    -> "review" (reason "cross_government_level") — report
     only; cross-level matches are NEVER proposed/attached (this is what excludes ESD, a NYS
     authority, from any city-EDC candidate set even when the name scores high).
  5. No match (< 45)                        -> "unmatched" — report only; NEVER minted as a new
     entity (Greenbook divisions are not entities).

FALLBACK: if nycresolver cannot be imported, `build_registry_matcher` returns an
`ExactFallbackMatcher` that does exact normalized-name + acronym matching only (no fuzzy tiers),
and `NYCRESOLVER_AVAILABLE` is False so callers can report the degradation. Per the
build-against-docs rule we ship the real interface; the fallback only guards a missing dep.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

try:  # Real, documented interface (preferred).
    from nycresolver.fetcher import CanonicalRecord
    from nycresolver.matcher import Matcher

    NYCRESOLVER_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only where the dep is absent
    CanonicalRecord = None  # type: ignore[assignment]
    Matcher = None  # type: ignore[assignment]
    NYCRESOLVER_AVAILABLE = False


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def entity_to_canonical_row(entity: dict) -> dict:
    """Convert a registry entity into the Socrata-shaped row nycresolver's
    CanonicalRecord.from_row expects. The registry `id` becomes record_id so a match round-trips
    straight back to the entity. other_names[] (MODA acronyms/alt-names + operator curation)
    become the alternate-name variant space."""
    other = [o.get("name", "") for o in entity.get("other_names", []) if o.get("name")]
    status = entity.get("status", "")
    return {
        "record_id": entity["id"],
        "name": entity.get("name", ""),
        "acronym": entity.get("short_name") or "",
        "alternate_or_former_names": ";".join(other),
        "alternate_or_former_acronyms": "",
        "name_alphabetized": "",
        "organization_type": entity.get("classification") or "",
        "operational_status": "Active" if status == "active" else status,
        "reports_to": "",
        "url": "",
    }


@dataclass(frozen=True)
class _FallbackResult:
    """Mimics the subset of nycresolver.MatchResult that reconcile reads."""
    input_value: str
    matched: bool
    confidence_score: float
    confidence_tier: str
    matched_record_id: str
    matched_canonical_name: str
    match_type: str


class ExactFallbackMatcher:
    """Exact normalized-name + acronym matcher used only when nycresolver is unavailable.
    Produces exact (100) or no-match (0) decisions — no fuzzy tiers."""

    def __init__(self, rows: list[dict]) -> None:
        self._by_name: dict[str, dict] = {}
        self._by_acronym: dict[str, dict] = {}
        for r in rows:
            self._by_name.setdefault(_norm(r["name"]), r)
            for variant in (r.get("alternate_or_former_names") or "").split(";"):
                if variant.strip():
                    self._by_name.setdefault(_norm(variant), r)
            if r.get("acronym"):
                self._by_acronym.setdefault(_norm(r["acronym"]), r)

    def match(self, value: str) -> _FallbackResult:
        key = _norm(value)
        row = self._by_acronym.get(key) or self._by_name.get(key)
        if row is None:
            return _FallbackResult(value, False, 0.0, "none", "", "", "no_match")
        return _FallbackResult(value, True, 100.0, "exact", row["record_id"], row["name"], "exact_fallback")


def build_registry_matcher(entities: list[dict]):
    """Return (matcher, id_to_level). The matcher is nycresolver's real Matcher built OFFLINE
    from registry entities, or the exact-only fallback if nycresolver is not installed."""
    rows = [entity_to_canonical_row(e) for e in entities]
    id_to_level = {e["id"]: e.get("government_level", "") for e in entities}
    if NYCRESOLVER_AVAILABLE:
        matcher = Matcher([CanonicalRecord.from_row(r) for r in rows])
    else:  # pragma: no cover - exercised only where the dep is absent
        matcher = ExactFallbackMatcher(rows)
    return matcher, id_to_level


@dataclass(frozen=True)
class Decision:
    """One reconciliation outcome for a source name."""
    source_name: str
    source_level: str
    decision: str          # "attach" | "review" | "unmatched"
    reason: str            # "exact" | "fuzzy" | "cross_government_level" | "no_match"
    entity_id: str | None
    matched_name: str | None
    matched_level: str | None
    confidence_score: float
    confidence_tier: str
    match_type: str


def classify(result, id_to_level: dict[str, str], source_name: str, source_level: str) -> Decision:
    """Apply the PR #5 tier-gating rules to one nycresolver MatchResult."""
    if not getattr(result, "matched", False):
        return Decision(source_name, source_level, "unmatched", "no_match", None, None, None,
                        float(getattr(result, "confidence_score", 0.0)),
                        getattr(result, "confidence_tier", "none"),
                        getattr(result, "match_type", "no_match"))

    entity_id = result.matched_record_id
    matched_level = id_to_level.get(entity_id, "")
    score = float(result.confidence_score)
    tier = result.confidence_tier
    mtype = result.match_type

    # Rule 4: any government_level mismatch is never proposed/attached (excludes ESD).
    if matched_level != source_level:
        return Decision(source_name, source_level, "review", "cross_government_level",
                        entity_id, result.matched_canonical_name, matched_level, score, tier, mtype)

    # Rule 2: exact, same level -> attach (proposal-grade). Rule 3: fuzzy -> review only.
    if tier == "exact":
        return Decision(source_name, source_level, "attach", "exact",
                        entity_id, result.matched_canonical_name, matched_level, score, tier, mtype)
    return Decision(source_name, source_level, "review", "fuzzy",
                    entity_id, result.matched_canonical_name, matched_level, score, tier, mtype)


def reconcile(matcher, id_to_level: dict[str, str], named_sources: list[tuple[str, str]]) -> list[Decision]:
    """Reconcile a list of (source_name, source_level) pairs. Pure over the matcher."""
    out: list[Decision] = []
    for name, level in named_sources:
        out.append(classify(matcher.match(name), id_to_level, name, level))
    return out
