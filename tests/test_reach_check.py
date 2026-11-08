"""Offline tests for scripts/reach_check.py (no network)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import reach_check as rc  # noqa: E402


def test_classify_each_layer():
    c = rc.classify
    assert c({"dns_resolves": False}) == "dns_missing"
    assert c({"dns_resolves": False, "alt_host": "example.gov"}) == "dns_alt_host_only"
    assert c({"dns_resolves": True, "tcp443": False, "tcp80": False}) == "no_server"
    assert c({"dns_resolves": True, "tcp443": False, "tcp80": True, "http_status": 200}) == "http_only"
    assert c({"dns_resolves": True, "tcp443": False, "tcp80": True, "http_error": "x"}) == "unclear"
    assert c({"dns_resolves": True, "tcp443": True, "tcp80": True, "tls": "bad_cert",
              "http_status": 301}) == "bad_certificate_http_ok"
    assert c({"dns_resolves": True, "tcp443": True, "tcp80": False, "tls": "bad_cert"}) == "bad_certificate"
    assert c({"dns_resolves": True, "tcp443": True, "tcp80": True, "tls": "error",
              "http_status": 200}) == "tls_fails_http_ok"
    ok = {"dns_resolves": True, "tcp443": True, "tcp80": True, "tls": "ok"}
    assert c(dict(ok, https_status=403)) == "refuses"
    assert c(dict(ok, https_status=301)) == "answers_now"
    assert c(dict(ok, https_status=404)) == "error_status"
    assert c(dict(ok, https_error="TimeoutError")) == "https_error"


def test_every_diagnosis_is_documented():
    seen = {rc.classify(r) for r in (
        {"dns_resolves": False}, {"dns_resolves": False, "alt_host": "a"},
        {"dns_resolves": True, "tcp443": False, "tcp80": False})}
    assert seen <= set(rc.DIAGNOSES)


def test_dead_name_records_alternate_host(monkeypatch):
    monkeypatch.setattr(rc, "_resolves", lambda h: h == "example.gov")
    rec = rc.check("https://www.example.gov/", "ua")
    assert rec["dns_resolves"] is False and rec["alt_host"] == "example.gov"
    assert rec["diagnosis"] == "dns_alt_host_only" and "tcp443" not in rec


def test_uses_the_research_identity():
    assert rc.research_ua().startswith("BetaNYC-agentic-access-study/")
