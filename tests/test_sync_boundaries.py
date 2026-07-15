"""Offline tests for the nyc-boundaries vocabulary parser (scripts/sync_boundaries.py).

No fetch — exercises the pure parse functions on in-memory fixtures mirroring the DOCUMENTED
index.ts shape (BoundaryId union + layers object; single- and double-quoted string literals), plus
outcome assertions on the committed data/nyc_boundaries_layers.json. Fixtures are not guessed: they
mirror data/cache/nyc-boundaries_layers_index.ts (staged 2026-07-15).
"""
import json
import pathlib

import pytest

from sync_boundaries import parse_boundary_ids, parse_layers

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Minimal fixture reproducing the two quote styles (single for cd, double for uhf) and pp's
# internal double-quotes inside a single-quoted literal, plus a description-less layer (hd).
FIXTURE_TS = """
export type BoundaryId =
  | 'cd'
  | 'pp'
  | 'uhf'
  | 'hd';

export const layers: ILayers = {
  cd: {
    name: 'Community District',
    name_plural: 'Community Districts',
    description:
      'Community Boards advise on land use.',
    description_url: 'https://communityprofiles.planning.nyc.gov/',
    apiUrl: 'https://bm-api.beta.nyc/bounds_new?id=cd',
    icon: '💬',
    formatContent: name => format_cd(name[0], name.substring(1, 3))
  },
  pp: {
    name: 'Police Precinct',
    name_plural: 'Police Precincts',
    description:
      'A police precinct is patrolled by the NYPD. The term "precinct" may also refer to a station.',
    description_url: 'https://www1.nyc.gov/site/nypd/index.page',
    apiUrl: 'https://bm-api.beta.nyc/bounds_new?id=pp',
    icon: '🚔',
    formatContent: name => format_default(name)
  },
  uhf: {
    name: "UHF 42 Neighborhood",
    name_plural: "UHF 42 Neighborhoods",
    description:
      "In the 1980s, NYC agencies and the United Hospital Fund defined schemes.",
    description_url: 'https://a816-dohbesp.nyc.gov/',
    apiUrl: 'https://bm-api.beta.nyc/bounds_new?id=uhf',
    icon: '🏥',
    formatContent: name => format_default(name)
  },
  hd: {
    name: 'Historic District',
    name_plural: 'Historic Districts',
    apiUrl: 'https://bm-api.beta.nyc/bounds_new?id=hd',
    icon: '🗝',
    formatContent: name => format_default(name)
  }
};
"""


def test_parse_boundary_ids_order():
    assert parse_boundary_ids(FIXTURE_TS) == ["cd", "pp", "uhf", "hd"]


def test_parse_layers_single_quoted_fields():
    rows = {r["id"]: r for r in parse_layers(FIXTURE_TS)}
    assert rows["cd"]["name"] == "Community District"
    assert rows["cd"]["name_plural"] == "Community Districts"
    assert rows["cd"]["description"] == "Community Boards advise on land use."
    assert rows["cd"]["description_url"] == "https://communityprofiles.planning.nyc.gov/"


def test_parse_layers_double_quoted_fields():
    # uhf uses double-quoted literals — must still parse (regression: single-quote-only missed it).
    uhf = next(r for r in parse_layers(FIXTURE_TS) if r["id"] == "uhf")
    assert uhf["name"] == "UHF 42 Neighborhood"
    assert uhf["description"].startswith("In the 1980s")


def test_parse_layers_single_quote_keeps_internal_double_quotes():
    pp = next(r for r in parse_layers(FIXTURE_TS) if r["id"] == "pp")
    assert '"precinct"' in pp["description"], "internal double-quotes truncated the literal"


def test_parse_layers_missing_description_is_null():
    hd = next(r for r in parse_layers(FIXTURE_TS) if r["id"] == "hd")
    assert hd["description"] is None
    assert hd["description_url"] is None


def test_name_not_confused_with_name_plural_or_description_url():
    cd = next(r for r in parse_layers(FIXTURE_TS) if r["id"] == "cd")
    # `name` must be the singular literal, not the plural, and description not description_url.
    assert cd["name"] == "Community District"
    assert cd["description"] == "Community Boards advise on land use."


def test_union_vs_object_divergence_raises():
    bad = FIXTURE_TS.replace("  | 'hd';", "  | 'hd'\n  | 'ghost';")
    with pytest.raises(ValueError, match="union"):
        parse_layers(bad)


def test_committed_vocab_has_all_22_layers():
    vocab = json.loads((ROOT / "data" / "nyc_boundaries_layers.json").read_text(encoding="utf-8"))
    ids = {row["id"] for row in vocab["layers"]}
    expected = {"bid", "cc", "cd", "dsny", "fb", "hc", "nta", "nycongress", "pp", "sa", "sd", "ss",
                "zipcode", "hd", "ibz", "uhf", "puma", "cdta", "ps", "nda", "ed", "mc"}
    assert ids == expected
    assert vocab["boundaries_as_of"] == "2026-07-15"
