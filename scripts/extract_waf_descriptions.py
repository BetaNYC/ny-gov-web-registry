#!/usr/bin/env python3
"""Turn browser-pane page captures of WAF-blocked nyc.gov agencies into description records.

Phase 5 of the registry crawled 317 entities with a stdlib crawler; the citywide Akamai WAF
403'd 152 `www.nyc.gov` / `www1.nyc.gov` targets (recorded as `fetch_failed`). A real browser
context passes the WAF, so those pages were re-fetched through the app's Browser pane
(`mcp__Claude_Browser__*`) under explicit operator authorization (Noel, 2026-07-15).

This module is the OFFLINE half of that recovery: it never touches the network. It reads the raw
page text captured by the browser (`data/cache/waf_page_captures.json`), extracts each agency's
own descriptive prose VERBATIM (never summarizes, paraphrases, or generates), and writes
`data/descriptions_recovery.json` in the exact same schema as `data/descriptions.json` so
`merge_descriptions.py` can fold it back in.

Extraction is deliberately conservative — verbatim-or-nothing. It targets the nyc.gov Full Site
Editing "About <Agency>" page shape (a nav column of short links, then the `About <Agency>`
heading, then paragraph prose, then social-media / audio-description / playback boilerplate).
A capture that yields no clean prose block is recorded as `extraction_empty` rather than guessed at.

Statuses produced: ok | no_about_found | fetch_failed | extraction_empty.

Capture schema (input, per id):
  { "source_url": str, "raw_text": str | null, "kind": "about"|"landing", "status_hint": str }
  status_hint one of: found | no_about_found | fetch_failed
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURES_PATH = REPO_ROOT / "data" / "cache" / "waf_page_captures.json"
RECOVERY_PATH = REPO_ROOT / "data" / "descriptions_recovery.json"

MAX_TEXT_CHARS = 5000  # matches crawl_about.py cap
METHOD = "browser-pane"

# A line whose lowercased form starts with any of these ends the prose block (boilerplate tail).
_STOP_PREFIXES = (
    "you can also follow us on social media",
    "audio description:",
    "playback issues",
    "find social channels",
    "stay in touch",
    "connect with us",
    "follow us on",
    "sign up for our newsletter",
    "download the 311 app",
)

# A line whose lowercased, stripped form equals / starts with one of these is dropped inline
# (chrome that can appear amid otherwise-kept content).
_DROP_EXACT = {"share", "(opens in new tab)", "select", "print", "translate", "text size"}

# Signals that the captured page is the nyc.gov "We're Sorry" 404 shell.
_NOT_FOUND_MARKERS = (
    "we're sorry.",
    "you have reached an outdated or non-existing page",
    "that page was not found",  # nyc.gov FSE soft-404 (section title stays, body is a 404)
)


def truncate(text: str, limit: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    """Trim whitespace and cap length. Returns (text, truncated). Mirrors crawl_about.py."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True


def _strip_wrapper(page_text: str) -> str:
    """Drop the get_page_text header (through the first '---' line) and the trailing Tab Context."""
    body = page_text
    # Header: everything up to and including the first line that is exactly '---'.
    lines = body.splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if ln.strip() == "---":
            start = i + 1
            break
    lines = lines[start:]
    # Trailing "Tab Context:" block.
    for i, ln in enumerate(lines):
        if ln.strip().startswith("Tab Context:"):
            lines = lines[:i]
            break
    return "\n".join(lines)


def is_not_found(page_text: str) -> bool:
    """True if the capture is the nyc.gov 404 shell rather than a real page."""
    low = page_text.lower().replace("’", "'")  # normalize curly apostrophe
    return any(m in low for m in _NOT_FOUND_MARKERS)


def _is_prose_start(line: str) -> bool:
    """A line that plausibly begins the descriptive block (a real sentence, not a nav item)."""
    if len(line) >= 80:
        return True
    words = line.split()
    return len(words) >= 12 and line.rstrip().endswith((".", "!", "?"))


def extract_description(page_text: str) -> str:
    """Extract the agency's own about-page prose, verbatim (whitespace normalized).

    Returns '' when no clean prose block is found. Never paraphrases or summarizes.
    """
    if is_not_found(page_text):
        return ""
    body = _strip_wrapper(page_text)
    raw_lines = [ln.strip() for ln in body.splitlines()]

    # Find the first prose line; everything before it is the nav column / heading chrome.
    start_idx = None
    for i, ln in enumerate(raw_lines):
        if not ln:
            continue
        if _is_prose_start(ln):
            start_idx = i
            break
    if start_idx is None:
        return ""

    kept: list[str] = []
    for ln in raw_lines[start_idx:]:
        low = ln.lower()
        if any(low.startswith(p) for p in _STOP_PREFIXES):
            break
        if not ln:
            kept.append("")  # preserve paragraph boundary
            continue
        if low in _DROP_EXACT or low.startswith("(opens in new tab)"):
            continue
        kept.append(ln)

    # Collapse runs of blank lines into single paragraph breaks; join.
    paragraphs: list[str] = []
    buf: list[str] = []
    for ln in kept:
        if ln:
            buf.append(ln)
        elif buf:
            paragraphs.append(" ".join(buf))
            buf = []
    if buf:
        paragraphs.append(" ".join(buf))
    return "\n\n".join(p for p in paragraphs if p.strip())


def record_for(capture: dict) -> dict:
    """Build one descriptions.json-shaped record from a capture dict."""
    source_url = capture.get("source_url")
    fetched_at = capture.get("fetched_at", "2026-07-16")
    hint = capture.get("status_hint", "found")

    base = {
        "text": None,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "method": None,
        "status": None,
        "truncated": False,
    }
    if hint == "fetch_failed":
        base["status"] = "fetch_failed"
        return base
    if hint == "no_about_found":
        base["status"] = "no_about_found"
        return base

    raw = capture.get("raw_text") or ""
    prose = extract_description(raw)
    if not prose:
        base["status"] = "extraction_empty"
        return base
    text, was_trunc = truncate(prose)
    base.update(text=text, method=METHOD, status="ok", truncated=was_trunc)
    return base


def build_recovery(captures: dict, fetched_at: str = "2026-07-16") -> dict:
    """Produce the descriptions_recovery.json structure from the captures map."""
    descriptions = {}
    legacy_entities = []
    for entity_id, cap in captures.items():
        cap = dict(cap)
        cap.setdefault("fetched_at", fetched_at)
        if cap.get("path_family") == "legacy":
            legacy_entities.append(entity_id)
        descriptions[entity_id] = record_for(cap)
    return {
        "_generated_from": "extract_waf_descriptions.py",
        "_note": (
            "Browser-pane recovery of WAF-blocked www.nyc.gov / www1.nyc.gov agencies "
            "(operator-authorized, Noel 2026-07-15). Verbatim agency prose; never generated."
        ),
        "method": METHOD,
        # Web-history data: entities whose About page is still served from the pre-CMS /html/
        # generation. Carried here so reconciliation / the Wayback harvester can inherit it.
        "_legacy_path_entities": sorted(legacy_entities),
        "descriptions": descriptions,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract WAF-recovery descriptions from browser captures.")
    ap.add_argument("--captures", type=Path, default=CAPTURES_PATH)
    ap.add_argument("--out", type=Path, default=RECOVERY_PATH)
    ap.add_argument("--fetched-at", default="2026-07-16")
    args = ap.parse_args()

    captures = json.loads(args.captures.read_text())
    recovery = build_recovery(captures, fetched_at=args.fetched_at)
    args.out.write_text(json.dumps(recovery, indent=1, ensure_ascii=False) + "\n")

    from collections import Counter
    counts = Counter(v["status"] for v in recovery["descriptions"].values())
    print(f"wrote {args.out} — {len(recovery['descriptions'])} records: {dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
