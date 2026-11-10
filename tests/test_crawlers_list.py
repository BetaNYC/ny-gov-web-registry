"""Offline checks for data/ai-readability/crawlers.csv, the checked list of crawler names
(issue #42). These test the file's shape and its own rules, not the vendor pages: the vendor
pages were checked by hand and by a second reviewer (docs/ai-readability/crawlers.md)."""

import csv
import datetime
import pathlib
import re

CRAWLERS = pathlib.Path(__file__).resolve().parent.parent / "data" / "ai-readability" / "crawlers.csv"

HEADER = ["company", "name", "purpose", "ua_exact", "send", "follows_robots_per_vendor",
          "publishes_ips", "ip_list_url", "doc_url", "checked", "proof_quote", "reason_not_sent"]
SEND = {"yes", "rules-only"}
ROBOTS = {"yes", "no", "partly", "not stated"}
IPS = {"yes", "no"}
# Names that exist only in robots.txt: no crawler ever sends them, so they are never sent.
ROBOTS_ONLY_TOKENS = {"Google-Extended", "Applebot-Extended"}


def rows():
    with CRAWLERS.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == HEADER
        return list(reader)


def test_header_and_values():
    for r in rows():
        assert r["send"] in SEND, r["name"]
        assert r["follows_robots_per_vendor"] in ROBOTS, r["name"]
        assert r["publishes_ips"] in IPS, r["name"]
        datetime.date.fromisoformat(r["checked"])
        assert r["company"] and r["name"] and r["purpose"], r["name"]


def test_names_are_unique():
    names = [r["name"].lower() for r in rows()]
    assert len(names) == len(set(names))


def test_no_unfinished_rows():
    for r in rows():
        for v in r.values():
            assert "NEEDS BROWSER" not in v and "TODO" not in v, r["name"]


def test_sent_names_have_a_label_and_a_vendor_page():
    for r in rows():
        if r["send"] != "yes":
            continue
        assert r["ua_exact"], r["name"]
        assert r["doc_url"].startswith("https://"), r["name"]
        if r["ua_exact"] != "token only":
            for label in r["ua_exact"].split(" || "):
                assert r["name"].lower() in label.lower(), (r["name"], label)


def test_rules_only_names_say_why():
    for r in rows():
        if r["send"] == "rules-only":
            assert r["reason_not_sent"].strip(), r["name"]


def test_robots_only_tokens_are_never_sent():
    for r in rows():
        if r["name"] in ROBOTS_ONLY_TOKENS:
            assert r["send"] == "rules-only", r["name"]


def test_ip_lists_are_links():
    for r in rows():
        if r["publishes_ips"] == "yes" and r["ip_list_url"]:
            for url in r["ip_list_url"].split(" || "):
                assert re.match(r"https://\S+$", url), (r["name"], url)


def test_proof_quotes_are_short():
    for r in rows():
        if r["proof_quote"]:
            assert len(r["proof_quote"].split()) < 15, r["name"]
