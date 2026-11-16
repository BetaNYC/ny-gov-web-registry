"""Offline tests for sync_dotgov (CISA .gov leads for NY counties, cities, towns, villages) and for
extract_osc_entities (OSC entity tables -> JSON). In-memory fixtures only."""
import pytest

from extract_osc_entities import extract
from sync_dotgov import build_leads, classify


@pytest.mark.parametrize("org,want", [
    ("Town of Ira", ("town", "Ira")),
    ("TOWN OF PERU", ("town", "PERU")),
    ("Town Of Varick", ("town", "Varick")),
    ("Town of Clinton, NY", ("town", "Clinton")),
    ("Inc. Village of Freeport", ("village", "Freeport")),
    ("Incorporated Village of Lloyd Harbor", ("village", "Lloyd Harbor")),
    ("Village of the Branch", ("village", "Branch")),
    ("City of White Plains", ("city", "White Plains")),
    ("Broome County", ("county", "Broome")),
    ("Chenango County NY", ("county", "Chenango")),
    ("County of Erie, New York", ("county", "Erie")),
    ("St. Lawrence County", ("county", "St. Lawrence")),
    ("Brockport Police Department", None),
    ("Suffolk County Water Authority", None),
    ("Office of the Westchester County", None),
    ("", None),
])
def test_classify(org, want):
    assert classify(org) == want


def _row(domain, org, dtype="City", state="NY", sub=""):
    return {"Domain name": domain, "Domain type": dtype, "Organization name": org,
            "Suborganization name": sub, "City": "X", "State": state, "Security contact email": "s@x"}


def test_build_leads_filters_and_counts():
    rows = [_row("irany.gov", "Town of Ira"), _row("broomecountyny.gov", "Broome County", dtype="County"),
            _row("29palms.gov", "City of Twentynine Palms", state="CA"),
            _row("panynj.gov", "Port Authority", dtype="Interstate"),
            _row("ira-pd.gov", "Town of Ira", sub="Police Department"),
            _row("brockportpolice.gov", "Brockport Police Department")]
    leads, counts = build_leads(rows)
    assert [lead["domain"] for lead in leads] == ["broomecountyny.gov", "irany.gov"]
    assert counts == {"ny_rows": 5, "read": 4, "suborganization_skipped": 1,
                      "not_a_government_name": 1, "leads": 2}
    assert "Security contact email" not in str(leads) and "s@x" not in str(leads)


class _Sheet:
    def __init__(self, rows):
        self.rows, self.nrows = rows, len(rows)

    def row_values(self, i):
        return self.rows[i]


class _Book:
    def __init__(self, sheets):
        self.sheets = sheets

    def sheet_by_name(self, n):
        return _Sheet(self.sheets[n])


def _book(**over):
    sheets = {
        "Counties": [["Counties", "", ""], ["Municipal Code", "County Name", "Economic Region"],
                     ["010100000000", "County of Albany", "Capital District"], ["", "", ""]],
        "Cities": [["Cities", "", "", ""], ["Municipal Code", "City Name", "County ", "Economic Region"],
                   ["010201000000", "City of Albany", "Albany", "Capital District"]],
        "Towns": [["Towns", "", "", ""], ["Municipal Code", "Town Name", "County ", "Economic Region"],
                  ["330363650000", "Town of Palm Tree", "Orange", "Mid-Hudson Region"]],
        "Villages": [["Villages", "", "", ""], ["Municipal Code", "Village Name", "County ", "Economic Region"],
                     ["010418703040", "Village of Menands", "Albany", "Capital District"]],
        "Summary Table": [["Number of Local Governments", "", ""], ["Counts as of 2026", "", ""],
                          ["Government Type", "Specific Government Type", "Count"],
                          ["General Purpose Local Governments (Municipalities)", "Counties", 57.0],
                          ["Special Purpose Local Governments", "School Districts", 689.0]],
    }
    sheets.update(over)
    return _Book(sheets)


def test_extract_osc_keeps_codes_as_text_and_reads_summary():
    out = extract(_book())
    assert out["counties"] == [{"municipal_code": "010100000000", "name": "County of Albany",
                                "economic_region": "Capital District", "county": None}]
    assert out["towns"][0]["county"] == "Orange"
    assert out["summary"] == {"title": "Counts as of 2026",
                              "general_purpose": [{"type": "Counties", "count": 57}]}


def test_extract_osc_refuses_a_changed_layout_or_a_numeric_code():
    with pytest.raises(ValueError, match="headers"):
        extract(_book(Towns=[["Towns"], ["Code", "Town Name", "County", "Economic Region"]]))
    with pytest.raises(TypeError, match="leading zeros"):
        extract(_book(Villages=[["Villages", "", "", ""],
                                ["Municipal Code", "Village Name", "County", "Economic Region"],
                                [10418703040.0, "Village of Menands", "Albany", "Capital District"]]))
