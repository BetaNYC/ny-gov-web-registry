"""Offline tests for the nyc.gov directory validation (scripts/validate_nycgov_directory.py).

The directory JSON is the SAME upstream as the canonical dataset (t3jq record_ids), so the check
is a strict-subset consistency assertion, not entity minting.
"""
from validate_nycgov_directory import validate


def _entity(goid):
    return {"id": goid.lower(), "name": goid, "government_level": "nyc",
            "web_properties": [], "status": "active",
            "identifiers": [{"scheme": "nyc_goid", "identifier": goid}]}


def test_subset_holds_reports_clean():
    directory = [
        {"record_id": "NYC_GOID_000001", "listed_in_nyc_gov_agency": True},
        {"record_id": "NYC_GOID_000002", "listed_in_nyc_gov_agency": False},
    ]
    entities = [_entity("NYC_GOID_000001"), _entity("NYC_GOID_000002"), _entity("NYC_GOID_000003")]
    rep = validate(directory, entities)
    assert rep["subset_holds"] is True
    assert rep["directory_ids_missing_from_registry"] == []
    assert rep["listed_in_nyc_gov_agency_true"] == 1
    # A registry goid not in this directory export is a note, not drift.
    assert rep["registry_goids_absent_from_directory"] == ["NYC_GOID_000003"]


def test_directory_id_missing_from_registry_is_drift():
    directory = [{"record_id": "NYC_GOID_999999", "listed_in_nyc_gov_agency": True}]
    entities = [_entity("NYC_GOID_000001")]
    rep = validate(directory, entities)
    assert rep["subset_holds"] is False
    assert rep["directory_ids_missing_from_registry"] == ["NYC_GOID_999999"]
