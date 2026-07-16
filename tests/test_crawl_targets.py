"""Offline tests for crawl_targets.derive_targets — the crawl-set derivation seam.

Verifies the priority order (MODA full URL > primary web_property > any web_property > no_url),
that the deep nyc.gov/site path is recovered verbatim from the CSV (the whole reason this step
exists), and that URL-less entities are recorded as no_url, never guessed. No fetch."""
from crawl_targets import _url_from_domain, derive_targets


def _entity(id_, name, identifiers=None, web_properties=None):
    return {"id": id_, "name": name,
            "identifiers": identifiers or [], "web_properties": web_properties or []}


def test_moda_csv_url_recovers_the_deep_path_verbatim():
    # The registry web_property is host-only ('www.nyc.gov'); the deep path lives only in the CSV.
    ents = [_entity("dsny", "Department of Sanitation",
                    identifiers=[{"scheme": "nyc_goid", "identifier": "NYC_GOID_000152"}],
                    web_properties=[{"domain": "www.nyc.gov", "role": "primary"}])]
    url_by_goid = {"NYC_GOID_000152": "https://www.nyc.gov/site/dsny/index.page"}
    targets, no_url = derive_targets(ents, url_by_goid)
    assert no_url == []
    assert targets[0]["start_url"] == "https://www.nyc.gov/site/dsny/index.page"
    assert targets[0]["derivation"] == "moda_csv_url"


def test_moda_url_wins_over_web_property():
    ents = [_entity("x", "X",
                    identifiers=[{"scheme": "nyc_goid", "identifier": "G1"}],
                    web_properties=[{"domain": "old.example.gov", "role": "primary"}])]
    targets, _ = derive_targets(ents, {"G1": "https://new.example.gov/home"})
    assert targets[0]["start_url"] == "https://new.example.gov/home"
    assert targets[0]["derivation"] == "moda_csv_url"


def test_falls_back_to_primary_web_property_when_no_goid_url():
    # Seed entities (MTA) have no goid; their web_property is the only URL source.
    ents = [_entity("mta", "MTA", web_properties=[{"domain": "mta.info", "role": "primary"}])]
    targets, no_url = derive_targets(ents, {})
    assert no_url == []
    assert targets[0]["start_url"] == "https://mta.info/"
    assert targets[0]["derivation"] == "web_property_primary"


def test_goid_present_but_no_csv_url_falls_back_to_web_property():
    ents = [_entity("y", "Y",
                    identifiers=[{"scheme": "nyc_goid", "identifier": "G2"}],
                    web_properties=[{"domain": "y.example.gov", "role": "primary"}])]
    targets, _ = derive_targets(ents, {})  # G2 not in the map (blank url cell)
    assert targets[0]["derivation"] == "web_property_primary"
    assert targets[0]["start_url"] == "https://y.example.gov/"


def test_non_primary_web_property_used_as_last_resort():
    ents = [_entity("z", "Z", web_properties=[{"domain": "legacy.example.gov", "role": "legacy"}])]
    targets, _ = derive_targets(ents, {})
    assert targets[0]["derivation"] == "web_property_other"
    assert targets[0]["start_url"] == "https://legacy.example.gov/"


def test_no_url_recorded_never_guessed():
    ents = [_entity("lirr", "Long Island Rail Road")]  # no identifiers, no web_properties
    targets, no_url = derive_targets(ents, {})
    assert targets == []
    assert no_url == [{"id": "lirr", "name": "Long Island Rail Road"}]


def test_url_from_domain_shapes_bare_host():
    assert _url_from_domain("mta.info") == "https://mta.info/"
    assert _url_from_domain("www.example.gov/") == "https://www.example.gov/"
