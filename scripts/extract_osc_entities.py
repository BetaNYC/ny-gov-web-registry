"""extract_osc_entities.py — pull the four general-purpose government sheets out of OSC's
entity-tables.xls into a plain JSON file the localities sync can read without an Excel library.

Source: Office of the State Comptroller, Local Government Entities, "entity-tables.xls"
        https://web.osc.state.ny.us/localgov/web-entity-map/excel/entity-tables.xls
        (linked from https://web.osc.state.ny.us/localgov/web-entity-map/)

Sheets and headers read (verified on the 2026-11-13 download, title row "Counts as of 2026"):
    Counties   row 2: Municipal Code | County Name | Economic Region
    Cities     row 2: Municipal Code | City Name | County | Economic Region
    Towns      row 2: Municipal Code | Town Name | County | Economic Region
    Villages   row 2: Municipal Code | Village Name | County | Economic Region
    Summary Table row 3: Government Type | Specific Government Type | Count
Header cells carry trailing spaces in the file ("County "); they are compared stripped. A sheet
whose headers differ raises: a changed layout is an error, not something to guess around.

Why a JSON extract: the workbook also carries a very hidden Excel 4.0 macro sheet ("Macro1",
"Auto_Open"). xlrd reads cell values only and never runs anything, but the raw workbook is kept
out of git (data/cache/ only) and only this extract is committed as the dated snapshot.

INPUT:  data/cache/osc_entity-tables.xls (the operator's download; this script never fetches)
OUTPUT: data/cache/osc_entity_tables.json (read by sync_nys_localities.py); pass --out to write
        the dated snapshot under data/ai-readability/sources/<date>/ as well.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
XLS = CACHE / "osc_entity-tables.xls"
OUT = CACHE / "osc_entity_tables.json"
URL = "https://web.osc.state.ny.us/localgov/web-entity-map/excel/entity-tables.xls"

# sheet -> (output key, expected header row (stripped), header row index)
SHEETS = {
    "Counties": ("counties", ["Municipal Code", "County Name", "Economic Region"]),
    "Cities": ("cities", ["Municipal Code", "City Name", "County", "Economic Region"]),
    "Towns": ("towns", ["Municipal Code", "Town Name", "County", "Economic Region"]),
    "Villages": ("villages", ["Municipal Code", "Village Name", "County", "Economic Region"]),
}
HEADER_ROW = 1  # 0-based: row 0 is the sheet title, row 1 the headers
SUMMARY_SHEET = "Summary Table"
SUMMARY_HEADER = ["Government Type", "Specific Government Type", "Count"]


def _cell(v) -> str:
    """Cell value as a stripped string. The municipal codes are text cells in this file; a float
    would mean Excel converted one, which would drop leading zeros, so it raises."""
    if isinstance(v, float):
        raise TypeError(f"numeric cell {v!r} where text was expected (a code lost its leading zeros?)")
    return str(v).strip()


def extract(book) -> dict:
    """Pure over an opened xlrd Book: the four sheets as row dicts, plus the summary counts."""
    out: dict = {}
    for sheet_name, (key, header) in SHEETS.items():
        sh = book.sheet_by_name(sheet_name)
        got = [str(c).strip() for c in sh.row_values(HEADER_ROW)][:len(header)]
        if got != header:
            raise ValueError(f"sheet {sheet_name!r}: headers {got} differ from {header}")
        rows = []
        for i in range(HEADER_ROW + 1, sh.nrows):
            vals = sh.row_values(i)[:len(header)]
            if not any(str(v).strip() for v in vals):
                continue
            rec = dict(zip(header, (_cell(v) for v in vals), strict=True))
            row = {"municipal_code": rec["Municipal Code"], "name": rec[header[1]],
                   "economic_region": rec["Economic Region"]}
            row["county"] = rec.get("County")  # None on the Counties sheet (no county column)
            rows.append(row)
        out[key] = rows
    sh = book.sheet_by_name(SUMMARY_SHEET)
    title = [str(c).strip() for c in sh.row_values(1)]
    hdr = [str(c).strip() for c in sh.row_values(2)]
    if hdr != SUMMARY_HEADER:
        raise ValueError(f"summary headers {hdr} differ from {SUMMARY_HEADER}")
    out["summary"] = {"title": title[0],
                      "general_purpose": [{"type": str(r[1]).strip(), "count": int(r[2])}
                                          for r in (sh.row_values(i) for i in range(3, sh.nrows))
                                          if str(r[0]).startswith("General Purpose")]}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--xls", type=pathlib.Path, default=XLS)
    ap.add_argument("--out", type=pathlib.Path, action="append",
                    help="output path (repeatable); default data/cache/osc_entity_tables.json")
    ap.add_argument("--fetched", required=True, help="UTC time of the download, ISO 8601")
    ap.add_argument("--last-modified", required=True, help="the download's HTTP Last-Modified header")
    args = ap.parse_args(argv)
    if not args.xls.exists():
        print(f"[gated] no OSC workbook at {args.xls}; download {URL} there first.", file=sys.stderr)
        return 2
    try:
        import xlrd  # declared in requirements.txt; only this script needs it
    except ModuleNotFoundError:
        print("error: xlrd is not installed (pip install -r requirements.txt)", file=sys.stderr)
        return 2
    data = extract(xlrd.open_workbook(str(args.xls)))
    doc = {"_source": {"publisher": "Office of the State Comptroller", "url": URL,
                       "fetched": args.fetched, "http_last_modified": args.last_modified,
                       "sha256_of_xls": hashlib.sha256(args.xls.read_bytes()).hexdigest(),
                       "extracted_by": "scripts/extract_osc_entities.py",
                       "sheets": list(SHEETS)}, **data}
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    for path in args.out or [OUT]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    print("rows: " + ", ".join(f"{k} {len(data[k])}" for k, _ in SHEETS.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
