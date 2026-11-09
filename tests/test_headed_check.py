"""Offline tests for scripts/headed_check.py (no browser, no network)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import headed_check  # noqa: E402

ar, pba, rc = headed_check._modules()


def test_refuses_without_operator_authorization(capsys):
    assert headed_check.main(["--targets-file", "x.json", "--run-dir", "/tmp/x",
                              "--vantage", "home-hearth"]) == 2
    assert "operator-authorized" in capsys.readouterr().err


def test_to_resp_shapes_a_page_like_the_probe():
    body = ("<html><head><title>Town</title></head><body>" + "word " * 200 + "</body></html>").encode()
    r = headed_check.to_resp(pba, "https://example.ny.gov/", 200, {"Content-Type": "text/html"},
                             body, "https://example.ny.gov/", [])
    assert r["status"] == 200 and r["headers"]["content-type"] == "text/html"
    assert r["body_class"] == "content" and r["is_block_page"] is False
    assert r["transport_error"] is None and r["offhost_redirect"] is False
    assert ar.w4_identity_verdict(r)[0] == "pass"
    assert ar.test_r1(r)[0] == "pass"


def test_to_resp_records_a_browser_failure_as_unreachable():
    r = headed_check.to_resp(pba, "https://gone.ny.gov/", None, {}, b"", None, [],
                             error="Error: net::ERR_NAME_NOT_RESOLVED")
    assert ar.unreachable(r)
    assert ar.w4_identity_verdict(r)[0] == "na"


def test_offhost_final_url_is_flagged():
    r = headed_check.to_resp(pba, "https://a.ny.gov/robots.txt", 200, {}, b"User-agent: *\n",
                             "https://b.example.com/robots.txt", [])
    assert r["offhost_redirect"] is True


def test_identity_is_never_the_research_identity():
    assert headed_check.IDENTITY != pba.RESEARCH_KEY
