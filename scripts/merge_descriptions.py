#!/usr/bin/env python3
"""Merge browser-pane recovery records into data/descriptions.json.

Offline, deterministic, idempotent. Folds `data/descriptions_recovery.json` (produced by
`extract_waf_descriptions.py`) into `data/descriptions.json`, preserving the wrapper metadata
of the base file.

This pass re-examined exactly the phase-5 `fetch_failed` WAF bucket with a real browser, so its
findings are newer, better evidence for those entities specifically.

Merge rule (per entity id present in the recovery file):
  * a recovery record REPLACES a base record whose status is `fetch_failed` (the WAF bucket this
    pass targets) — whether the recovery is `ok` (verbatim text recovered) or `no_about_found` /
    `extraction_empty` (browser reached the site but found no clean about prose). The latter is
    strictly more honest than leaving `fetch_failed`, which implies the WAF is still blocking;
  * a recovery record also fills an id ABSENT from the base;
  * every other existing base status is left untouched — an `ok`, `no_url`, `robots_disallowed`,
    or `no_about_found` base record is NEVER overwritten (phase-5 results outside the WAF bucket win).

Re-running on an already-merged file is a no-op. The base wrapper (`_generated_from`,
`crawl_started_at`, `user_agent`, `crawl_finished_at`) is kept; a `_recovery_merged_at` note is
added/updated so the provenance of the browser-pane pass is visible in the file itself.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESCRIPTIONS_PATH = REPO_ROOT / "data" / "descriptions.json"
RECOVERY_PATH = REPO_ROOT / "data" / "descriptions_recovery.json"


def merge(base: dict, recovery: dict, merged_at: str | None = None) -> tuple[dict, dict]:
    """Return (merged_base, stats). Pure; does not mutate inputs."""
    out = json.loads(json.dumps(base))  # deep copy
    base_desc = out.setdefault("descriptions", {})
    rec_desc = recovery.get("descriptions", {})

    stats = {"recovered_ok": 0, "reclassified": 0, "filled_new": 0, "skipped_protected": 0}

    for entity_id, rec in rec_desc.items():
        existing = base_desc.get(entity_id)
        rec_ok = rec.get("status") == "ok"

        if existing is None:
            base_desc[entity_id] = rec
            stats["recovered_ok" if rec_ok else "filled_new"] += 1
        elif existing.get("status") == "fetch_failed":
            base_desc[entity_id] = rec
            stats["recovered_ok" if rec_ok else "reclassified"] += 1
        else:
            # ok / no_url / robots_disallowed / already-recovered — never overwritten.
            stats["skipped_protected"] += 1

    if merged_at is not None:
        out["_recovery_merged_at"] = merged_at
        out["_recovery_source"] = "extract_waf_descriptions.py (browser-pane, operator-authorized)"
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge WAF-recovery descriptions into descriptions.json.")
    ap.add_argument("--descriptions", type=Path, default=DESCRIPTIONS_PATH)
    ap.add_argument("--recovery", type=Path, default=RECOVERY_PATH)
    ap.add_argument("--out", type=Path, default=None, help="default: overwrite --descriptions")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base = json.loads(args.descriptions.read_text())
    recovery = json.loads(args.recovery.read_text())
    merged_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    merged, stats = merge(base, recovery, merged_at=merged_at)

    from collections import Counter
    counts = Counter(v.get("status") for v in merged["descriptions"].values())
    print(f"merge: {stats}")
    print(f"post-merge status counts: {dict(counts)}")

    if args.dry_run:
        print("dry-run: no file written")
        return 0
    out_path = args.out or args.descriptions
    out_path.write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
