#!/usr/bin/env python3
"""Derive the deterministic about-page candidate list for each WAF-blocked target.

Offline. Reads `data/cache/waf_blocked.json` ([{id, url}, ...]) and emits an ordered list of
about-page candidate URLs per entity, best-first, capped at MAX_CANDIDATES. The browser-pane
recovery loop navigates these in order and stops at the first non-404 page that yields prose.

Derivation (mirrors crawl_about.py's path conventions, adapted for browser recovery):
  * `/site/<slug>/...` targets:
      - if the start path is already a specific about/subpage (not `index.page`), try it first;
      - the nyc.gov FSE about conventions `/site/<slug>/about/about-<slug>.page` then
        `/site/<slug>/about/about.page`;
      - the start URL last (a landing page, for link-scan fallback).
  * `/content/...` and everything else: the start URL only (link-scan fallback in the loop).

All derived about-convention candidates use the canonical `www.nyc.gov` host (the `www1` mirror
frequently 404s); the original start URL is preserved as given.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse

REPO_ROOT = Path(__file__).resolve().parent.parent
BLOCKED_PATH = REPO_ROOT / "data" / "cache" / "waf_blocked.json"
OUT_PATH = REPO_ROOT / "data" / "cache" / "waf_candidates.json"

MAX_CANDIDATES = 4
CANONICAL_HOST = "www.nyc.gov"


def _site_slug(path: str) -> str | None:
    m = re.match(r"^/site/([^/]+)/", path)
    return m.group(1) if m else None


def _canonical(path: str) -> str:
    return urlunparse(("https", CANONICAL_HOST, path, "", "", ""))


def candidate_urls(start_url: str) -> list[str]:
    """Ordered, deduped about-page candidates for `start_url`, best-first."""
    parsed = urlparse(start_url)
    path = parsed.path or "/"
    out: list[str] = []
    slug = _site_slug(path)

    if slug:
        is_landing = path.rstrip("/").endswith(("index.page", f"/site/{slug}"))
        already_about = "about" in path.lower() or not is_landing
        if already_about:
            out.append(start_url)
        out.append(_canonical(f"/site/{slug}/about/about-{slug}.page"))
        out.append(_canonical(f"/site/{slug}/about/about.page"))
        out.append(start_url)  # landing / link-scan fallback
    else:
        out.append(start_url)

    seen: set[str] = set()
    deduped: list[str] = []
    for u in out:
        key = u.rstrip("/")
        if key not in seen:
            seen.add(key)
            deduped.append(u)
    return deduped[:MAX_CANDIDATES]


def build_plan(blocked: list[dict]) -> dict:
    return {b["id"]: candidate_urls(b["url"]) for b in blocked}


def main() -> int:
    blocked = json.loads(BLOCKED_PATH.read_text())
    plan = build_plan(blocked)
    OUT_PATH.write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT_PATH} — {len(plan)} entities, "
          f"{sum(len(v) for v in plan.values())} total candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
