"""headed_check.py — would a person get in? A real, visible Chrome visits sites that refused the probe.

WHY THIS EXISTS
  probe_bot_access.py and render_check.py ask as declared bots or as headless Chrome. A site that
  refuses all of them may still serve a person. This script opens the installed Google Chrome as a
  normal, visible window (not headless) with its own User-Agent, unchanged: no name is borrowed or
  edited, so the client is exactly what it says it is. If Chrome gets the page, the same core-tier
  tests the probe runs (W1-W6, M1-M4, R1, R3, I1-I5, D1, F1, F2, F4, P1-P4) are scored on what
  Chrome received, so "blocks bots" can be told apart from "blocks everyone".

HOW
  It drives probe_bot_access.host_plan, the probe's own per-host request plan, so the requests
  (robots.txt, llms.txt, sitemap, homepage, a 404 probe, http://) and the scoring are identical.
  Only the client changes. Every row is labeled identity "headed_chrome" with the browser's UA,
  never the research identity. One browser context per host keeps cookies within that host, as a
  person's browser would. The homepage body scored is the last document Chrome was served, so a
  challenge page that the browser clears on its own counts as cleared.

WHAT IT IS NOT
  Not an evasion tool. It uses no stealth plugins and no fingerprint patching. It never solves a
  CAPTCHA, and it never retries past a block. It makes one pass, paced, and follows nothing
  beyond the probe's plan.

ACCESS GATE
  It refuses to run without --operator-authorized. It needs a logged-in desktop session, because
  the window must open.

Run (results stay private: write into a git-ignored run folder):
  uv run --with playwright python scripts/headed_check.py --targets-file <targets.json> \
      --run-dir data/ai-readability/runs/<YYYY-MM-DD>-<vantage>-headed --vantage home-hearth \
      --operator-authorized
Output: <run-dir>/results.jsonl (same row shape as the probe), headed.jsonl (one row per host:
  status, title, words, challenge, verdict), and summary counts on stdout.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time
import types
import urllib.parse

IDENTITY = "headed_chrome"
PACE_S = 3.0
GOTO_TIMEOUT_MS = 30_000
IDLE_WAIT_MS = 8_000
MAX_BYTES = 120_000


def _modules():
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in sys.path:  # `python -I` leaves the script's own folder off sys.path
        sys.path.insert(0, here)
    import ai_readability as ar
    import probe_bot_access as pba
    import render_check as rc
    return ar, pba, rc


def to_resp(pba, url: str, status, headers: dict, raw: bytes, final_url: str, redirects: list,
            error: str = None) -> dict:
    """Shape a browser response like probe_bot_access.http_request's dict."""
    if error:
        return {"url": url, "final_url": final_url or url, "status": None, "headers": {}, "body": "",
                "raw": b"", "bytes": 0, "content_type": "", "is_block_page": None, "body_class": None,
                "waf": [], "redirects": redirects, "peercert": None, "truncated": False,
                "transport_error": error[:240], "transport_kind": "browser"}
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    raw = (raw or b"")[:MAX_BYTES]
    body = raw.decode("utf-8", "replace")
    cls, is_block = pba.classify_body(body)
    start = urllib.parse.urlparse(url).netloc.lower()
    offhost = urllib.parse.urlparse(final_url or url).netloc.lower() != start
    return {"url": url, "final_url": final_url, "status": status, "headers": hdrs, "body": body,
            "raw": raw, "bytes": len(raw), "content_type": hdrs.get("content-type", ""),
            "is_block_page": is_block, "body_class": cls,
            "waf": pba.fingerprint_waf(hdrs, hdrs.get("set-cookie", "")), "redirects": redirects,
            "peercert": None, "truncated": len(raw) >= MAX_BYTES, "offhost_redirect": offhost,
            "transport_error": None, "transport_kind": None}


def browser_fetch(page, pba, spec: dict) -> dict:
    """One navigation. Returns the last main-frame document Chrome was served (a self-clearing
    challenge leaves the real page as the last document)."""
    url = spec["url"]
    docs = []

    def on_response(r):
        try:
            if r.request.is_navigation_request() and r.frame == page.main_frame:
                docs.append(r)
        except Exception:
            pass

    page.on("response", on_response)
    try:
        first = page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
        if spec.get("kind") == "home":
            try:
                page.wait_for_load_state("networkidle", timeout=IDLE_WAIT_MS)
            except Exception:
                pass
        last = docs[-1] if docs else first
        if last is None:
            return to_resp(pba, url, None, {}, b"", url, [], error="no response")
        redirects, req = [], last.request.redirected_from
        while req is not None:
            rr = req.response()
            redirects.insert(0, {"status": rr.status if rr else None, "location": req.url})
            req = req.redirected_from
        if spec.get("follow") == "none" and first is not None and first.request.redirected_from:
            # the http:// check reads the FIRST hop only, as the probe does (follow="none")
            hop = first.request.redirected_from
            while hop.redirected_from is not None:
                hop = hop.redirected_from
            rr = hop.response()
            return to_resp(pba, url, rr.status if rr else None, rr.headers if rr else {}, b"",
                           url, [])
        try:
            raw = last.body()
        except Exception:
            raw = b""
        return to_resp(pba, url, last.status, last.headers, raw, last.url, redirects)
    except Exception as e:  # a failure is a datum; never retried
        return to_resp(pba, url, None, {}, b"", url, [], error="{}: {}".format(type(e).__name__, e))
    finally:
        page.remove_listener("response", on_response)


def run(targets: list, run_dir: pathlib.Path, vantage: str) -> dict:
    from playwright.sync_api import sync_playwright  # imported here so tests need no Playwright

    ar, pba, rc = _modules()
    run_id = run_dir.name
    portals_path = pba.ROOT / "data" / "ai-readability" / "portals.json"
    ctx = types.SimpleNamespace(
        git_sha=pba.git_sha(), run_id=run_id, vantage=vantage, tier="core",
        identities=[{"key": pba.RESEARCH_KEY, "klass": "baseline"}],
        portals=ar.load_portals(portals_path) if portals_path.exists() else
        {"hosts": set(), "host_suffixes": (), "url_prefixes": ()},
        dotgov=None, today=datetime.date.today())
    counts = {}
    with sync_playwright() as p, (run_dir / "results.jsonl").open("a") as res, \
            (run_dir / "headed.jsonl").open("a") as hd:
        browser = p.chromium.launch(headless=False, channel="chrome")
        probe = browser.new_page()
        ua = probe.evaluate("navigator.userAgent")
        probe.close()
        if "Headless" in ua:
            raise SystemExit("refusing: the browser reports a headless User-Agent: " + ua)
        for t in targets:
            host = urllib.parse.urlparse(t["url"]).hostname
            job = {"host": host, "url": t["url"], "entity_id": t.get("entity_id"),
                   "entity_name": t.get("entity_name"), "entity_ids": t.get("entity_ids")}
            bctx = browser.new_context()   # no user_agent override: Chrome's own name
            page = bctx.new_page()
            home = None
            gen = pba.host_plan(job, ctx)
            try:
                spec = next(gen)
                while True:
                    r = browser_fetch(page, pba, spec)
                    if spec["kind"] == "home":
                        home = (r, page.title() if r.get("status") else "",
                                page.inner_text("body") if page.query_selector("body") else "")
                    time.sleep(PACE_S)
                    spec = gen.send(r)
            except StopIteration as stop:
                rows = stop.value or []
            for row in rows:
                if row["identity"] == pba.RESEARCH_KEY:
                    row["identity"] = IDENTITY
                row.update(client=IDENTITY, ua=ua)
                res.write(json.dumps(row, default=str) + "\n")
            r, title, text = home or ({}, "", "")
            rec = {"test": "headed", "identity": IDENTITY, "ua": ua, "vantage": vantage,
                   "url": t["url"], "host": host, "entity_id": t.get("entity_id"),
                   "entity_name": t.get("entity_name"),
                   "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "status": r.get("status"), "final_url": r.get("final_url"), "title": title,
                   "words": rc.visible_words(text), "text_head": (text or "")[:600],
                   "challenge": rc.is_challenge(title, text), "waf": r.get("waf"),
                   "error": r.get("transport_error")}
            rec["verdict"] = rc.verdict(rec)
            hd.write(json.dumps(rec) + "\n")
            res.flush()
            hd.flush()
            counts[rec["verdict"]] = counts.get(rec["verdict"], 0) + 1
            print(host, rec["status"], rec["words"], rec["verdict"], flush=True)
            bctx.close()
        browser.close()
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets-file", required=True, help="same inputs as probe_bot_access.py")
    ap.add_argument("--run-dir", required=True, help="git-ignored run folder")
    ap.add_argument("--vantage", required=True, help="machine short name, e.g. home-hearth")
    ap.add_argument("--operator-authorized", action="store_true",
                    help="required: the operator authorized this run of a visible browser")
    a = ap.parse_args(argv)
    if not a.operator_authorized:
        print("refusing: pass --operator-authorized (configured is not authorized)", file=sys.stderr)
        return 2
    _, pba, _ = _modules()
    targets = pba.load_targets(a.targets_file)
    run_dir = pathlib.Path(a.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    counts = run(targets, run_dir, a.vantage)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
