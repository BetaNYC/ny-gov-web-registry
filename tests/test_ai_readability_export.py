"""Offline tests for scripts/ai_readability_export.py (no network)."""
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import ai_readability_export as ex  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PSL = ({"gov", "us", "ny.us", "org", "uk", "co.uk"}, {"ck"}, {"www.ck"})


def test_base_domain_follows_public_suffix_rules():
    assert ex.base_domain("dos.ny.gov", PSL) == "ny.gov"
    assert ex.base_domain("www.otda.state.ny.us", PSL) == "state.ny.us"   # ny.us is a suffix
    assert ex.base_domain("a.b.example.co.uk", PSL) == "example.co.uk"
    assert ex.base_domain("x.foo.ck", PSL) == "x.foo.ck"                  # wildcard *.ck
    assert ex.base_domain("www.ck", PSL) == "www.ck"                      # exception !www.ck
    assert ex.base_domain("gov", PSL) is None


def test_columns_are_gsa_order_then_ours():
    gsa = ex.gsa_dictionary()
    cols = ex.columns(gsa)
    assert cols[:len(gsa)] == [r["field id"] for r in gsa]
    assert all(c.startswith(("ny_", "ai_")) for c in cols[len(gsa):])
    assert len(cols) == len(set(cols))
    assert set(ex.GSA_FILLED) <= {r["field id"] for r in gsa}


def test_committed_dictionary_matches_code(tmp_path):
    out = ex.write_dictionary(tmp_path / "d.csv")
    assert out.read_text() == (ROOT / "data" / "ai-readability" / "data-dictionary.csv").read_text(), \
        "run: python scripts/ai_readability_export.py --write-dictionary"
    rows = list(csv.DictReader(out.open()))
    assert tuple(rows[0].keys()) == ex.DICT_COLUMNS
    gsa_types = {r["data type"] for r in ex.gsa_dictionary()}
    assert {r["data type"] for r in rows} <= gsa_types


def _rows(host="www.example.gov"):
    base = {"run_id": "r", "catalog_version": "0.1", "vantage": "office", "ts": "2026-11-06T00:00:00+00:00",
            "host": host, "identity": "research_betanyc"}
    def r(test, verdict, ident="research_betanyc", **ev):
        return dict(base, test=test, verdict=verdict, identity=ident, evidence=ev)
    return [
        r("W1", "pass", status=200, content_type="text/plain; charset=utf-8",
          final_url="https://{}/robots.txt".format(host)),
        r("W3", "reported", tokens={"GPTBot": {"/": "disallow"}, "ClaudeBot": {"/": {"decision": "allow"}}}),
        r("W4", "pass", status=200, final_url="https://{}/home".format(host), server="nginx"),
        r("W4", "blocked", "gptbot", status=403),
        r("M2", "pass", url="https://{}/sitemap.xml".format(host), status=200, kind="urlset", url_count=7,
          content_type="application/xml"),
        r("F4", "pass", status=404),
        r("I5", "reported", http_status=301, http_redirects_to_https=True),
        r("R3", "reported", lang="en", landmarks=["main"], title="Example"),
    ]


def test_build_rows_fills_gsa_fields_and_ours():
    t = [{"url": "https://www.example.gov/", "entity_id": "ex", "entity_name": "Example Agency",
          "pool": "county", "lead": "cisa"}]
    v1 = [{"url": t[0]["url"], "variant": "headless_default", "verdict": "blocked", "status": 403},
          {"url": t[0]["url"], "variant": "headless_as_chrome", "verdict": "pass", "status": 200, "words": 9}]
    row = ex.build_rows(t, _rows(), v1, [], PSL, tenants={"www.example.gov": {"ex"}})[0]
    assert row["name"] == "example.gov" and row["initial_base_domain"] == "example.gov"
    assert row["live"] is True and row["redirect"] is True and row["status_code"] == 200
    assert row["primary_scan_status"] == "completed" and row["404_test"] is True
    assert row["robots_txt_detected"] is True and row["robots_txt_media_type"] == "text/plain"
    assert row["sitemap_xml_detected"] is True and row["sitemap_xml_count"] == 7
    assert row["https_enforced"] is True and row["main_element_present"] is True
    assert row["agency"] == "Example Agency" and row["source_list"] == ["cisa"]
    assert row["ai_w3_decisions"] == ["ClaudeBot=allow", "GPTBot=disallow"]
    assert row["ai_w4_gptbot_admitted"] is False and row["ai_v1_name_decides"] is True
    assert row["accessibility_violations"] is None                      # not measured -> blank


def test_shared_host_has_no_agency_and_quarantine_maps_to_gsa_status():
    t = [{"url": "https://www.example.gov/", "entity_id": "a", "entity_name": "A", "pool": "nyc-registry"}]
    row = ex.build_rows(t, _rows(), [], [], PSL, tenants={"www.example.gov": {"a", "b"}})[0]
    assert row["agency"] is None and row["ny_entity_ids"] == ["a", "b"]
    q = [{"host": "down.example.gov", "action": "quarantined", "what": "transport dns: gaierror"}]
    t2 = [{"url": "https://down.example.gov/", "entity_id": None, "entity_name": "D", "pool": "city"}]
    row2 = ex.build_rows(t2, [], [], q, PSL)[0]
    assert row2["primary_scan_status"] == "dns_resolution_error" and row2["live"] is None


def test_csv_conventions(tmp_path):
    assert ex.csv_value(True) == "TRUE" and ex.csv_value(None) == "" and ex.csv_value(["a", "b"]) == "a,b"
    ex.write_outputs([{"name": "x", "live": False}], tmp_path, ["name", "live"])
    assert (tmp_path / "sites.csv").read_text().splitlines() == ["name,live", "x,FALSE"]
    assert json.loads((tmp_path / "sites.json").read_text()) == [{"name": "x", "live": False}]
