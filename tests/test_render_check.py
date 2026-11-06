"""Offline tests for scripts/render_check.py (no browser, no network)."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import render_check as rc  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "HeadlessChrome/131.0.0.0 Safari/537.36")


def test_as_chrome_only_removes_headless_marker():
    out = rc.as_chrome(UA)
    assert "HeadlessChrome" not in out
    assert out == UA.replace("HeadlessChrome/", "Chrome/")


def test_challenge_detection():
    assert rc.is_challenge("Access Denied", "")
    assert rc.is_challenge("", "Just a moment... checking")
    assert not rc.is_challenge("NYC Council", "Welcome to the council")


def test_verdicts():
    assert rc.verdict({"status": 200, "challenge": False}) == "pass"
    assert rc.verdict({"status": 403, "challenge": True}) == "blocked"
    assert rc.verdict({"status": 200, "challenge": True}) == "blocked"
    assert rc.verdict({"error": "Timeout"}) == "na"


def test_visible_words():
    assert rc.visible_words("one two  three") == 3
    assert rc.visible_words(None) == 0


def test_without_authorization_is_a_dry_run(tmp_path, capsys):
    tf = tmp_path / "t.json"
    tf.write_text(json.dumps([{"entity_id": None, "entity_name": "x", "url": "https://example.test/"}]))
    assert rc.main(["--targets-file", str(tf), "--run-dir", str(tmp_path / "run")]) == 0
    out = capsys.readouterr().out
    assert "2 page loads planned; no network used" in out
    assert not (tmp_path / "run" / "v1-headless.jsonl").exists()
