"""Offline tests for Greenbook aggregation + enrichment shaping (scripts/sync_greenbook.py).

No fetch, no registry read — exercises the pure functions on in-memory rows/entities.
"""
from sync_greenbook import (
    GREENBOOK_ASOF,
    aggregate_agencies,
    greenbook_contact_details,
    host_of,
    registrable,
    web_candidate,
)


def _row(agency, acronym, **kw):
    base = {
        "Agency Name": agency, "Agency Acronym": acronym, "Agency Website": "",
        "First Name": "", "Last Name": "", "Office Title": "",
        "Address": "", "City": "", "State": "", "Zip Code": "",
        "Phone 1": "", "Agency Primary Phone": "", "Section": "City",
    }
    base.update(kw)
    return base


def test_aggregation_collapses_officers_and_drops_identity():
    rows = [
        _row("Department of Sanitation", "DSNY", **{
            "First Name": "Jane", "Last Name": "Doe", "Office Title": "Commissioner",
            "Agency Website": "www.nyc.gov/dsny", "Agency Primary Phone": "(212) 555-0000",
            "Address": "125 Worth St.", "City": "New York", "State": "NY", "Zip Code": "10013"}),
        _row("Department of Sanitation", "DSNY", **{
            "First Name": "John", "Last Name": "Roe", "Office Title": "Deputy",
            "Agency Website": "www.nyc.gov/dsny", "Agency Primary Phone": "(212) 555-0000",
            "Address": "125 Worth St.", "City": "New York", "State": "NY", "Zip Code": "10013"}),
    ]
    agencies = aggregate_agencies(rows)
    assert len(agencies) == 1, "two officer rows -> one agency"
    a = agencies[0]
    assert a["agency_name"] == "Department of Sanitation"
    assert a["row_count"] == 2
    assert a["phone"] == "(212) 555-0000"
    assert a["address"] == "125 Worth St., New York, NY 10013"
    # Officer identity must never survive aggregation.
    blob = repr(a)
    assert "Jane" not in blob and "Doe" not in blob and "Commissioner" not in blob


def test_host_and_registrable_normalization():
    assert host_of("http://www.nyc.gov/rgb") == "www.nyc.gov"
    assert host_of("NYC.gov/html/nycers") == "nyc.gov"
    assert host_of("https://www1.nyc.gov/site/dcas/index.page") == "www1.nyc.gov"
    # www / www1 collapse so a Greenbook 'www.nyc.gov' equals a MODA 'nyc.gov'.
    assert registrable("www.nyc.gov") == registrable("nyc.gov") == "nyc.gov"
    assert registrable("www1.nyc.gov") == "nyc.gov"


def test_web_candidate_skips_known_domain_www_normalized():
    # Greenbook 'www.nyc.gov/x' must NOT be added when the entity already owns 'nyc.gov'.
    existing = [{"domain": "nyc.gov", "role": "primary"}]
    assert web_candidate("www.nyc.gov/x", existing) is None
    # Empty website -> nothing.
    assert web_candidate("", existing) is None


def test_web_candidate_flags_genuinely_different_domain_as_legacy_lead():
    existing = [{"domain": "rentguidelinesboard.cityofnewyork.us", "role": "primary"}]
    wc = web_candidate("http://www.nyc.gov/rgb", existing)
    assert wc is not None
    assert wc["domain"] == "www.nyc.gov"
    assert wc["role"] == "legacy"  # never overwrites the primary
    assert "UNVERIFIED" in wc["notes"] and "rentguidelinesboard.cityofnewyork.us" in wc["notes"]


def test_contact_details_are_staleness_flagged():
    agency = {"address": "125 Worth St., New York, NY 10013", "phone": "(212) 555-0000"}
    cds = greenbook_contact_details(agency)
    types = {c["type"] for c in cds}
    assert types == {"address", "voice"}
    assert all(GREENBOOK_ASOF in c["note"] for c in cds), "every Greenbook contact must be staleness-flagged"


def test_agency_primary_phone_falls_back_to_phone_1():
    rows = [_row("Some Board", "SB", **{"Phone 1": "(718) 555-1212"})]  # no Agency Primary Phone
    assert aggregate_agencies(rows)[0]["phone"] == "(718) 555-1212"
