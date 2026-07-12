"""sync_nygov.py — STUB. NYS executive agencies from the ny.gov agency directory.

Source: https://www.ny.gov/agencies  (HTML directory, paginated, NO bulk export, no license)

This covers state EXECUTIVE agencies that are not "public authorities" and so are
absent from the ABO Directory (sync_abo.py). There is no API and no downloadable file,
so populating this requires an HTML scrape + hand-curation.

⚠️ NOT IMPLEMENTED — and intentionally so for now:
  1. Access gate: live fetching/scraping is gated pending explicit operator authorization.
  2. Scraping ny.gov reliably needs its own care (pagination, rate limits, brittle markup).

When authorized, implement: fetch pages -> parse (agency name, agency URL) -> emit records
with government_level="nys", provenance.sources=["nygov"], into data/cache/records_nygov.json.
Until then, NYS executive agencies enter the registry via hand-authored records
(provenance.sources=["manual"]) in data/registry.seed.json or curated additions.
"""
from __future__ import annotations

import sys


def main() -> int:
    print("sync_nygov.py is a stub — ny.gov agency scraping is not implemented "
          "(gated + needs care). Curate NYS executive agencies manually for now. "
          "See this file's docstring.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
