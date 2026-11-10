"""cert_log_names.py — find a government's other addresses in public certificate logs (issue #41).

WHY THIS EXISTS
  A body's website list names its main host. Its other hosts (a permit system, a data site, a
  meeting portal) are often listed nowhere, but every public HTTPS certificate is logged.
  crt.sh searches those logs. One query per registered domain returns every name a certificate
  was issued for under it. Nothing is guessed: we never try `data.`, `gis.` or `api.` on a domain.

WHAT IT DOES
  1. query   One request per registered domain to https://crt.sh/?q=%25.<domain>&output=json,
             at least PACE_S seconds apart, backing off on 429, 5xx and timeouts. The raw answer
             is cached outside git (CACHE/raw/<domain>.json), so a rerun never asks again.
             A domain that keeps failing is written to CACHE/failed.jsonl and skipped.
  2. filter  names_from_crtsh() keeps a name only when it is under the queried domain, is not a
             wildcard, and its first label is not infrastructure (INFRA_LABELS: mail, autodiscover,
             vpn, cpanel and the like; see docs/ai-readability/addresses.md).
  3. answer  Each new name (not already in websites.json) gets ONE GET to https://<name>/ with
             the research_betanyc user agent, redirects not followed. A name is kept when
             accept() says so (an HTML or API answer). One request per name, so no host is asked
             twice; the pool is small and every name is a different host.
             Results go to CACHE/answers.jsonl (outside git; never published).
  4. export  `--export PATH` writes the kept names, and nothing else about them, as
             address,registered_domain,found (the CSV ai_readability_websites.py reads).

WHICH DOMAINS
  registered_domains(): the base domain of every websites.json row without a host issue, when
  the body owns it: it is a `.gov` or `.ny.us` domain, or the listed host IS the domain (or
  `www.` + the domain). Shared hosting (wordpress.com, a vendor's platform) is never queried, and
  VENDOR_DOMAINS names the ones that would otherwise slip through. Non-government portals
  (`ny_government` false) are never queried.

Run (resumable; safe to stop and start):
  nohup python3 scripts/cert_log_names.py > ~/tmp/g1-crtsh/run.log 2>&1 &
  python3 scripts/cert_log_names.py --export data/ai-readability/cert-log-addresses.csv
"""

from __future__ import annotations

import argparse
import csv
import http.client
import json
import os
import pathlib
import queue
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEBSITES = ROOT / "data" / "ai-readability" / "websites.json"
CACHE = pathlib.Path(os.environ.get("G1_CRTSH_CACHE", pathlib.Path.home() / "tmp" / "g1-crtsh"))
CRTSH = "https://crt.sh/?q=%25.{}&output=json"
PACE_S = 6.0                  # at least 5 s between two crt.sh queries (Noel, issue #41)
BACKOFF_S = (30, 90, 240, 600)
CRTSH_TIMEOUT = 240
ANSWER_TIMEOUT = 15
WORKERS = 6
# The honest research identity, word for word from probe_bot_access.py IDENTITIES.
UA = ("BetaNYC-agentic-access-study/1.0 "
      "(+https://github.com/BetaNYC/ny-gov-web-registry; noel@beta.nyc)")

# Labels that name mail, sign-in, remote access, hosting control panels, DNS, or a
# non-public copy of a site. A name whose first label is one of these, optionally followed by
# digits or by `-anything` (mail2, vpn-east), is dropped before it is ever asked. The test is made
# on EVERY label below the registered domain (click.email.x.org is an email-tracking name), and a
# label containing `staging` (visitstaging) is dropped too.
INFRA_LABELS = (
    "mail", "email", "webmail", "smtp", "imap", "pop", "pop3", "mx", "owa", "exchange",
    "autodiscover", "autoconfig", "lyncdiscover", "lync", "sip", "msoid",
    "enterpriseenrollment", "enterpriseregistration",
    "vpn", "sslvpn", "remote", "rdweb", "rdgateway", "rds", "citrix", "gateway", "fw",
    "firewall", "anyconnect", "globalprotect",
    "cpanel", "whm", "webdisk", "cpcalendars", "cpcontacts", "webmin", "plesk",
    "ftp", "sftp", "ns", "dns", "adfs", "sts", "mdm", "localhost", "login", "signin", "sso",
    "dev", "test", "testing", "stage", "staging", "uat", "qa", "sandbox", "demo",
    "click", "track", "trk", "links", "mta-sts", "_dmarc", "_domainkey",
)
_INFRA = re.compile(r"^(?:{})(?:\d+|-.*)?$".format("|".join(map(re.escape, INFRA_LABELS))))

# Shared platforms whose registered domain is not a government's own.
VENDOR_DOMAINS = frozenset({
    "arcgis.com", "legistar.com", "granicus.com", "wordpress.com", "homestead.com", "webs.com",
    "yourtasc.com", "wixsite.com", "squarespace.com", "civicplus.com", "revize.com",
    "ecode360.com", "municode.com", "google.com", "blogspot.com", "weebly.com",
})

ACCEPT_TYPES = ("text/html", "application/xhtml+xml", "application/json", "application/xml",
                "text/xml", "application/geo+json", "application/vnd.api+json")


def _key(host: str) -> str:
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


# ------------------------------------------------------------------------------ pure parts
def _on_vendor(domain: str) -> bool:
    return any(domain == v or domain.endswith("." + v) for v in VENDOR_DOMAINS)


def registered_domains(rows: list) -> list:
    """Registered domains to query, from websites.json rows (see module doc)."""
    out = set()
    for r in rows:
        if r.get("ny_host_issue") or r.get("ny_government") is False:
            continue
        bd = (r.get("base_domain") or "").lower()
        host = (r.get("ny_initial_domain") or "").lower()
        if not bd or "." not in bd or "," in bd or _on_vendor(bd):
            continue
        if bd.endswith(".gov") or bd.endswith(".ny.us") or _key(host) == bd:
            out.add(bd)
    return sorted(out)


def names_from_crtsh(entries: list, domain: str) -> list:
    """Every name in a crt.sh answer that is under `domain`, not a wildcard and not
    infrastructure. Lowercased, sorted, unique."""
    domain = domain.lower()
    out = set()
    for e in entries:
        for n in (e.get("name_value") or "").split("\n") + [e.get("common_name") or ""]:
            n = n.strip().lower().rstrip(".")
            if not n or "*" in n or " " in n or "@" in n:
                continue
            if n != domain and not n.endswith("." + domain):
                continue
            if is_infra(n, domain):
                continue
            out.add(n)
    return sorted(out)


def is_infra(name: str, domain: str) -> bool:
    """True when any label of `name` below `domain` is infrastructure (see INFRA_LABELS)."""
    if name == domain:
        return False
    labels = name[: -len(domain) - 1].split(".")
    return any(_INFRA.match(l) or "staging" in l for l in labels)


def accept(status: int, content_type: str | None, location: str | None, name: str):
    """(kept, why). Kept: an HTML or API answer (2xx, or 401/403, which still serve a page), or a
    redirect that stays on the same host. Dropped: no answer, an error status, a redirect to
    another host (an alias of an address already listed elsewhere), or a non-page content type."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if 300 <= status < 400:
        tgt = urllib.parse.urlparse(urllib.parse.urljoin("https://{}/".format(name), location or ""))
        if (tgt.hostname or "").lower().rstrip(".") == name:
            return True, "redirects within its host"
        return False, "redirects to another host"
    if not (200 <= status < 300 or status in (401, 403)):
        return False, "status {}".format(status)
    if ctype in ACCEPT_TYPES or ctype.endswith("+json") or ctype.endswith("+xml"):
        return True, "{} {}".format(status, ctype)
    return False, "content type {!r}".format(ctype or None)


# ------------------------------------------------------------------------------ network
def _log(*a):
    print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), *a, flush=True)


def query_crtsh(domain: str):
    """Raw crt.sh entries for one domain, from cache or from one request (with backoff)."""
    raw = CACHE / "raw" / (domain + ".json")
    if raw.exists():
        return json.loads(raw.read_text(encoding="utf-8")), False
    url = CRTSH.format(urllib.parse.quote(domain))
    last = None
    for i, wait in enumerate((0,) + BACKOFF_S):
        if wait:
            _log("crt.sh backoff", domain, "{}s after".format(wait), last)
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=CRTSH_TIMEOUT) as resp:
                body = resp.read()
            data = json.loads(body or b"[]")
            raw.write_bytes(body or b"[]")
            return data, True
        except urllib.error.HTTPError as e:
            last = "HTTP {}".format(e.code)
            if e.code != 429 and e.code < 500:
                break
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                http.client.HTTPException, json.JSONDecodeError, ssl.SSLError) as e:
            last = "{}: {}".format(type(e).__name__, e)
    with open(CACHE / "failed.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"domain": domain, "error": last, "ts": time.time()}) + "\n")
    return None, True


def ask(name: str) -> dict:
    """One GET to https://<name>/, redirects not followed, body not read beyond 1 KB."""
    rec = {"name": name, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ctx = ssl.create_default_context()
    try:
        conn = http.client.HTTPSConnection(name, timeout=ANSWER_TIMEOUT, context=ctx)
        conn.request("GET", "/", headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5"})
        resp = conn.getresponse()
        resp.read(1024)
        rec.update(status=resp.status, content_type=resp.getheader("Content-Type"),
                   location=resp.getheader("Location"))
        conn.close()
        rec["kept"], rec["why"] = accept(resp.status, rec["content_type"], rec["location"], name)
    except Exception as e:  # noqa: BLE001  any failure to answer means "not kept"
        rec.update(kept=False, why="no answer: {}".format(type(e).__name__))
    return rec


# ------------------------------------------------------------------------------ run
def known_hosts() -> set:
    rows = json.loads(WEBSITES.read_text(encoding="utf-8"))
    return {_key(r.get("ny_initial_domain") or "") for r in rows}


def run(limit=None) -> int:
    (CACHE / "raw").mkdir(parents=True, exist_ok=True)
    rows = json.loads(WEBSITES.read_text(encoding="utf-8"))
    domains = registered_domains(rows)[:limit]
    failed = set()
    if (CACHE / "failed.jsonl").exists():
        failed = {json.loads(l)["domain"] for l in open(CACHE / "failed.jsonl", encoding="utf-8")}
    answers = CACHE / "answers.jsonl"
    asked = set()
    if answers.exists():
        asked = {json.loads(l)["name"] for l in open(answers, encoding="utf-8")}
    known = known_hosts()
    lock = threading.Lock()
    work = queue.Queue()
    queued = set()

    def worker():
        while True:
            item = work.get()
            if item is None:
                return
            rec = ask(item[0])
            rec["domain"] = item[1]
            with lock:
                with open(answers, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
            work.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    _log("start: {} domains, {} already asked, {} failed before".format(len(domains), len(asked), len(failed)))
    last_q = 0.0
    for i, d in enumerate(domains, 1):
        if d in failed:
            continue
        cached = (CACHE / "raw" / (d + ".json")).exists()
        if not cached:
            gap = PACE_S - (time.monotonic() - last_q)
            if gap > 0:
                time.sleep(gap)
        data, fetched = query_crtsh(d)
        if fetched:
            last_q = time.monotonic()
        if data is None:
            _log("FAILED", d)
            continue
        names = [n for n in names_from_crtsh(data, d)
                 if _key(n) not in known and n not in asked and n not in queued]
        for n in names:
            queued.add(n)
            work.put((n, d))
        if i % 25 == 0 or names:
            _log("{}/{} {} entries={} new_names={} queue={}".format(
                i, len(domains), d, len(data), len(names), work.qsize()))
    work.join()
    for _ in threads:
        work.put(None)
    _log("done: domains={} failed={}".format(
        len(domains), sum(1 for _ in open(CACHE / "failed.jsonl")) if (CACHE / "failed.jsonl").exists() else 0))
    (CACHE / "DONE").write_text(datetime.now(timezone.utc).isoformat() + "\n")
    return 0


def export(path) -> int:
    """Write the kept names: address,registered_domain,found. Nothing else leaves the cache."""
    best = {}
    for l in open(CACHE / "answers.jsonl", encoding="utf-8"):
        r = json.loads(l)
        if r.get("kept") and not is_infra(r["name"], r["domain"]):
            best.setdefault(r["name"], (r["domain"], r["ts"][:10]))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["address", "registered_domain", "found"])
        for n in sorted(best):
            w.writerow(["https://{}/".format(n), best[n][0], best[n][1]])
    print("wrote {} names to {}".format(len(best), path))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", metavar="PATH")
    ap.add_argument("--list-domains", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    if a.list_domains:
        for d in registered_domains(json.loads(WEBSITES.read_text(encoding="utf-8"))):
            print(d)
        return 0
    if a.export:
        return export(a.export)
    return run(a.limit)


if __name__ == "__main__":
    sys.exit(main())
