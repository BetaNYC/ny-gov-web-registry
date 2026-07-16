#!/usr/bin/env python3
"""Assemble data/cache/waf_page_captures.json from the raw browser captures + a meta manifest.

Offline. The browser-pane recovery loop saves each fetched page's raw text to
`data/cache/waf_captures_raw/<id>.txt` and records, in `data/cache/waf_capture_meta.json`, the
winning source URL and a status hint per entity:

  { "<id>": {"source_url": str, "status_hint": "found"|"no_about_found"|"fetch_failed"} }

This script joins the two into the captures map that `extract_waf_descriptions.py` consumes.
Append-safe across batches: entities absent from the meta manifest are simply not emitted, so the
recovery can be built incrementally as batches complete.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
META_PATH = REPO_ROOT / "data" / "cache" / "waf_capture_meta.json"
RAW_DIR = REPO_ROOT / "data" / "cache" / "waf_captures_raw"
OUT_PATH = REPO_ROOT / "data" / "cache" / "waf_page_captures.json"
FETCHED_AT = "2026-07-16"


def assemble(meta: dict, raw_dir: Path, fetched_at: str = FETCHED_AT) -> dict:
    captures = {}
    for entity_id, m in meta.items():
        hint = m.get("status_hint", "found")
        cap = {
            "source_url": m.get("source_url"),
            "fetched_at": fetched_at,
            "status_hint": hint,
            "raw_text": None,
        }
        if hint == "found":
            raw_file = raw_dir / f"{entity_id}.txt"
            if not raw_file.exists():
                raise FileNotFoundError(f"meta says '{entity_id}' found but {raw_file} is missing")
            cap["raw_text"] = raw_file.read_text(encoding="utf-8")
        captures[entity_id] = cap
    return captures


def main() -> int:
    meta = json.loads(META_PATH.read_text())
    captures = assemble(meta, RAW_DIR)
    OUT_PATH.write_text(json.dumps(captures, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT_PATH} — {len(captures)} captures assembled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
