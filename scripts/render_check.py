"""render_check.py — catalog test V1: what does a real (headless) browser receive?

WHY THIS EXISTS
  A site can refuse a client because of what it *is* (no JavaScript, a bot) or because of the
  name it *declares*. A headless Chrome sends its own honest name by default ("HeadlessChrome").
  Loading the same page in the same browser, on the same connection, once under that name and
  once as plain "Chrome" separates the two: where only the second gets in, the block is decided
  by the declared name, not by whether the client is a real browser. This script makes that
  measurement repeatable. (Results stay private until the study concludes.)

TWO VARIANTS, ALWAYS BOTH, NEVER MERGED
  headless_default   — the browser's own User-Agent, unmodified (contains "HeadlessChrome").
  headless_as_chrome — the same UA with "HeadlessChrome" replaced by "Chrome". A DISCLOSED control,
                       like probe_bot_access.py's mechanism_probe_chrome: it shows a block is about
                       identity. It is labeled in every row and is never used to collect content
                       for any other purpose.

WHAT IT IS NOT
  Not a crawler (one homepage load per host per variant, nothing followed) and not an evasion tool:
  no stealth plugins, no fingerprint patching, no CAPTCHA solving, no retries past a block.

ACCESS GATE
  Refuses to open a browser without --operator-authorized (configured != authorized), the same
  rule as crawl_about.py. --dry-run prints the plan with no network.

Requires Playwright (`uv run --with playwright python scripts/render_check.py ...`). Uses the
installed Google Chrome (channel "chrome") when present, else Playwright's bundled Chromium.

Run:
  uv run --with playwright python scripts/render_check.py --targets-file pilot.json \
      --run-dir data/ai-readability/runs/<YYYY-MM-DD>-<vantage> --operator-authorized
Output: <run-dir>/v1-headless.jsonl (git-ignored with the rest of runs/).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import sys
import time

CATALOG_VERSION = "0.1"
VARIANTS = ("headless_default", "headless_as_chrome")
PACE_S = 4.0                 # gap between page loads; each host is revisited only once per variant
GOTO_TIMEOUT_MS = 30_000
IDLE_WAIT_MS = 10_000

# Phrases that mark a block or challenge page rather than the site's content. Kept short and
# deliberately conservative; a match is recorded as `challenge: true`, never retried.
CHALLENGE_RE = re.compile(
    r"Access Denied|Just a moment|Attention Required|verify you are human|Checking your browser",
    re.I,
)


def visible_words(text: str | None) -> int:
    return len(re.findall(r"\w+", text or ""))


def is_challenge(title: str | None, text: str | None) -> bool:
    return bool(CHALLENGE_RE.search((title or "") + " " + (text or "")[:2000]))


def as_chrome(ua: str) -> str:
    """The disclosed control UA: the browser's own string with only the Headless marker removed."""
    return ua.replace("HeadlessChrome", "Chrome")


def verdict(rec: dict) -> str:
    if rec.get("error"):
        return "na"
    if rec.get("challenge") or (rec.get("status") or 0) >= 400:
        return "blocked"
    return "pass"


def load_targets(path: str) -> list:
    """Same inputs as probe_bot_access.py --targets-file, including data/ai-readability/websites.json."""
    import probe_bot_access
    return probe_bot_access.load_targets(path)


def run(targets: list, out_path: pathlib.Path) -> int:
    from playwright.sync_api import sync_playwright  # imported here so tests need no Playwright

    n = 0
    with sync_playwright() as p, out_path.open("a") as out:
        try:
            browser = p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=True)
        probe = browser.new_page()
        base_ua = probe.evaluate("navigator.userAgent")
        probe.close()
        uas = {"headless_default": base_ua, "headless_as_chrome": as_chrome(base_ua)}
        for variant in VARIANTS:            # variant-major order spreads the two loads per host apart
            for t in targets:
                ctx = browser.new_context(user_agent=uas[variant])
                page = ctx.new_page()
                rec = {
                    "catalog_version": CATALOG_VERSION, "test": "V1", "variant": variant,
                    "ua": uas[variant], "url": t["url"], "entity_id": t.get("entity_id"),
                    "entity_name": t.get("entity_name"),
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
                try:
                    resp = page.goto(t["url"], wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
                    try:
                        page.wait_for_load_state("networkidle", timeout=IDLE_WAIT_MS)
                    except Exception:
                        pass
                    text = page.inner_text("body") if page.query_selector("body") else ""
                    title = page.title()
                    rec.update(
                        status=resp.status if resp else None, final_url=page.url, title=title,
                        words=visible_words(text), text_head=text[:600],
                        challenge=is_challenge(title, text),
                        sha256=hashlib.sha256(text.encode()).hexdigest(),
                    )
                except Exception as e:  # a failure is a datum; never retried
                    rec["error"] = repr(e)[:300]
                rec["verdict"] = verdict(rec)
                out.write(json.dumps(rec) + "\n")
                out.flush()
                n += 1
                print(variant, t["url"], rec.get("status"), rec.get("words"), rec["verdict"], flush=True)
                ctx.close()
                time.sleep(PACE_S)
        browser.close()
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets-file", required=True, help="JSON list of {entity_id, entity_name, url}")
    ap.add_argument("--run-dir", required=True, help="run folder; writes v1-headless.jsonl there")
    ap.add_argument("--operator-authorized", action="store_true",
                    help="required to open a browser (configured != authorized)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no network")
    a = ap.parse_args(argv)
    targets = load_targets(a.targets_file)
    if a.dry_run or not a.operator_authorized:
        for v in VARIANTS:
            for t in targets:
                print("LOAD", t["url"], "[{}]".format(v))
        print("{} page loads planned; no network used{}".format(
            len(targets) * len(VARIANTS),
            "" if a.dry_run else " (pass --operator-authorized to run)"))
        return 0
    out = pathlib.Path(a.run_dir) / "v1-headless.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    n = run(targets, out)
    print("wrote {} rows to {}".format(n, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
