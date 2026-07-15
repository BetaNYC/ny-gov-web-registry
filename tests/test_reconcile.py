"""Offline tests for the tier-gated reconciliation (scripts/reconcile.py).

Covers the PR #5 matching rules: exact same-level -> attach; fuzzy -> review; ANY level
mismatch -> review (never proposed); no-match -> unmatched. Includes the ESD false-positive
case as a regression test ("Economic Development Corporation" must resolve to the city EDC, never
to the NYS authority ESD) and one test that exercises the REAL nycresolver interface offline.
"""
import pytest

from reconcile import (
    NYCRESOLVER_AVAILABLE,
    build_registry_matcher,
    classify,
    entity_to_canonical_row,
    reconcile,
)


def _entity(id_, name, level, short_name=None, other_names=None):
    return {
        "id": id_, "name": name, "government_level": level,
        "short_name": short_name,
        "other_names": [{"name": n, "note": None} for n in (other_names or [])],
        "classification": None, "status": "active",
        "identifiers": [], "web_properties": [],
        "provenance": {"sources": ["manual"], "last_verified": None},
    }


# The collision-dense "Development Corporation" namespace from PR #5: the city EDC and the NYS
# authority ESD (carrying its operator-curated variant names) both in the match space.
EDC = _entity("nycedc", "New York City Economic Development Corporation", "authority",
              short_name="NYCEDC", other_names=["Economic Development Corporation"])
ESD = _entity("esd", "Empire State Development", "authority", short_name="ESD",
              other_names=["Empire State Development Corporation",
                           "New York State Urban Development Corporation"])
DSNY = _entity("dsny", "Department of Sanitation", "nyc", short_name="DSNY",
               other_names=["NYC Sanitation"])
ENTITIES = [EDC, ESD, DSNY]


def test_entity_to_canonical_row_shape():
    row = entity_to_canonical_row(DSNY)
    # These keys are exactly what nycresolver's CanonicalRecord.from_row reads (fetcher.py).
    assert row["record_id"] == "dsny"
    assert row["name"] == "Department of Sanitation"
    assert row["acronym"] == "DSNY"
    assert "NYC Sanitation" in row["alternate_or_former_names"]
    assert row["operational_status"] == "Active"


def test_real_nycresolver_is_usable_offline():
    # Guards that we build against the actual documented interface, offline (no network).
    if not NYCRESOLVER_AVAILABLE:
        pytest.skip("nycresolver not installed; exact-only fallback in use")
    matcher, _ = build_registry_matcher(ENTITIES)
    res = matcher.match("DSNY")  # exact acronym
    assert res.matched_record_id == "dsny"
    assert res.confidence_tier == "exact"
    res2 = matcher.match("NYC Sanitation")  # exact alternate name
    assert res2.matched_record_id == "dsny"


def test_exact_same_level_attaches():
    matcher, levels = build_registry_matcher(ENTITIES)
    [d] = reconcile(matcher, levels, [("Department of Sanitation", "nyc")])
    assert d.decision == "attach"
    assert d.reason == "exact"
    assert d.entity_id == "dsny"
    assert d.confidence_tier == "exact"


def test_edc_esd_false_positive_regression():
    # "Economic Development Corporation" must map to the city EDC, never to the NYS authority ESD,
    # even though ESD carries the near-identical variant "Empire State Development Corporation".
    matcher, levels = build_registry_matcher(ENTITIES)
    res = matcher.match("Economic Development Corporation")
    assert res.matched_record_id == "nycedc", "must resolve to the city EDC, not ESD"
    assert res.matched_record_id != "esd"


def test_cross_level_is_never_proposed():
    # A Greenbook city row (source_level 'nyc') exact-matching an 'authority' entity must route to
    # review as cross_government_level — never attach. This is the rule that excludes ESD.
    matcher, levels = build_registry_matcher(ENTITIES)
    [d] = reconcile(matcher, levels, [("New York City Economic Development Corporation", "nyc")])
    assert d.decision == "review"
    assert d.reason == "cross_government_level"
    assert d.matched_level == "authority"


def test_esd_variant_from_city_source_is_guarded():
    # Directly feeding an ESD variant name from a city-level source: even if it matches esd, the
    # level guard (authority != nyc) forces review, never attach/propose.
    matcher, levels = build_registry_matcher(ENTITIES)
    [d] = reconcile(matcher, levels, [("Empire State Development Corporation", "nyc")])
    assert d.decision == "review"
    assert d.reason == "cross_government_level"
    assert d.entity_id == "esd"


def test_no_match_is_unmatched_not_minted():
    matcher, levels = build_registry_matcher(ENTITIES)
    [d] = reconcile(matcher, levels, [("Schrodinger's Imaginary Bureau", "nyc")])
    assert d.decision == "unmatched"
    assert d.entity_id is None


def test_classify_fuzzy_same_level_is_review():
    # Unit-level: a synthetic high-but-not-exact result at the same level -> review/fuzzy.
    class _R:
        matched = True
        matched_record_id = "dsny"
        matched_canonical_name = "Department of Sanitation"
        confidence_score = 90.0
        confidence_tier = "high"
        match_type = "abbreviation_expansion"
    d = classify(_R(), {"dsny": "nyc"}, "Dept of Sanitation", "nyc")
    assert d.decision == "review" and d.reason == "fuzzy"
