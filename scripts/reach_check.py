"""reach_check.py — why could a site not be read? DNS, TCP, TLS and HTTP, one layer at a time.

WHY THIS EXISTS
  probe_bot_access.py records a host it cannot reach as `na` and quarantines it, which is right for
  the run but says nothing about *why*. A dead domain, an expired certificate, a site that only
  speaks plain http, and a server that refuses us are different findings. This script takes the
  hosts a run could not read and checks each layer in order, so every failure gets a cause.

WHAT IT DOES, PER HOST (at most 2 HTTP requests)
  1. DNS: does the name resolve? If not, does the www / bare variant resolve?
  2. TCP: does anything answer on 443 and on 80?
  3. TLS: does the certificate verify? (If not, the reason: expired, wrong name, self-signed.)
  4. HTTPS GET / (only if TLS verified) and HTTP GET / — status, Location, Server.
  Then `classify()` turns the record into one `diagnosis` (see DIAGNOSES).

  It sends the study's attributable research User-Agent (the `research_betanyc` identity in
  probe_bot_access.py), follows no redirects, and never retries.

Run (results stay private: write into a git-ignored run folder):
  python3 scripts/reach_check.py --targets-file data/ai-readability/runs/<run>/targets.json \
      --out data/ai-readability/runs/<run>/reach.jsonl
  The targets file takes the same inputs as probe_bot_access.py --targets-file.
  ai_readability_export.py reads reach.jsonl from the run folder into the ai_reach_* columns.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime
import http.client
import json
import pathlib
import socket
import ssl
import sys
import urllib.parse

TIMEOUT = 12

# diagnosis -> plain meaning (also the data dictionary's allowed values)
DIAGNOSES = {
    "dns_missing": "The name does not resolve; neither does the www or bare variant.",
    "dns_alt_host_only": "The name does not resolve, but the www or bare variant does.",
    "no_server": "The name resolves, but nothing answers on port 443 or 80.",
    "http_only": "Nothing answers on 443; plain http on 80 answers.",
    "bad_certificate_http_ok": "The certificate fails to verify; plain http answers.",
    "bad_certificate": "The certificate fails to verify; plain http does not answer.",
    "tls_fails_http_ok": "The TLS handshake fails; plain http answers.",
    "tls_fails": "The TLS handshake fails; plain http does not answer.",
    "refuses": "HTTPS answers with 401, 403, 429 or 503.",
    "answers_now": "HTTPS answers with a 2xx or 3xx status now.",
    "error_status": "HTTPS answers with another 4xx or 5xx status.",
    "https_error": "TLS verified, but the HTTPS request itself failed.",
    "unclear": "None of the above.",
}
REFUSAL_STATUSES = {401, 403, 429, 503}


def research_ua() -> str:
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in sys.path:  # `python -I` leaves the script's own folder off sys.path
        sys.path.insert(0, here)
    import probe_bot_access
    return next(i["ua"] for i in probe_bot_access.IDENTITIES if i["key"] == "research_betanyc")


def classify(rec: dict) -> str:
    """One diagnosis from a check record. Pure, so it is tested offline."""
    if not rec.get("dns_resolves"):
        return "dns_alt_host_only" if rec.get("alt_host") else "dns_missing"
    tcp443, tcp80 = rec.get("tcp443"), rec.get("tcp80")
    http_ok = rec.get("http_status") is not None
    tls = rec.get("tls")
    if not tcp443 and not tcp80:
        return "no_server"
    if not tcp443:
        return "http_only" if http_ok else "unclear"
    if tls == "bad_cert":
        return "bad_certificate_http_ok" if http_ok else "bad_certificate"
    if tls == "error":
        return "tls_fails_http_ok" if http_ok else "tls_fails"
    status = rec.get("https_status")
    if status in REFUSAL_STATUSES:
        return "refuses"
    if status is not None and 200 <= status < 400:
        return "answers_now"
    if status is not None:
        return "error_status"
    if rec.get("https_error"):
        return "https_error"
    return "unclear"


def _get(scheme: str, host: str, ua: str) -> dict:
    try:
        if scheme == "https":
            conn = http.client.HTTPSConnection(host, timeout=TIMEOUT, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(host, timeout=TIMEOUT)
        conn.request("GET", "/", headers={"User-Agent": ua, "Accept": "text/html"})
        r = conn.getresponse()
        out = {"status": r.status, "location": r.getheader("Location"), "server": r.getheader("Server")}
        r.read(2048)
        conn.close()
        return out
    except Exception as e:  # a failure is a datum; never retried
        return {"error": "{}: {}".format(type(e).__name__, str(e)[:160])}


def _tls(host: str) -> tuple:
    try:
        with socket.create_connection((host, 443), timeout=TIMEOUT) as s:
            with ssl.create_default_context().wrap_socket(s, server_hostname=host):
                return "ok", None
    except ssl.SSLCertVerificationError as e:
        return "bad_cert", (e.verify_message or str(e))[:160]
    except Exception as e:
        return "error", "{}: {}".format(type(e).__name__, str(e)[:160])


def _tcp(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=TIMEOUT).close()
        return True
    except Exception:
        return False


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except Exception:
        return False


def check(url: str, ua: str) -> dict:
    host = urllib.parse.urlparse(url).hostname
    rec = {"test": "reach", "url": url, "host": host,
           "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "dns_resolves": _resolves(host)}
    if not rec["dns_resolves"]:
        alt = host[4:] if host.startswith("www.") else "www." + host
        rec["alt_host"] = alt if _resolves(alt) else None
    else:
        rec["tcp443"], rec["tcp80"] = _tcp(host, 443), _tcp(host, 80)
        if rec["tcp443"]:
            rec["tls"], rec["tls_detail"] = _tls(host)
            if rec["tls"] == "ok":
                h = _get("https", host, ua)
                rec["https_status"], rec["https_location"] = h.get("status"), h.get("location")
                rec["https_server"], rec["https_error"] = h.get("server"), h.get("error")
        if rec["tcp80"]:
            p = _get("http", host, ua)
            rec["http_status"], rec["http_location"], rec["http_error"] = (
                p.get("status"), p.get("location"), p.get("error"))
    rec["diagnosis"] = classify(rec)
    return rec


def load_urls(path: str) -> list:
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import probe_bot_access
    return [t["url"] for t in probe_bot_access.load_targets(path)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets-file", required=True)
    ap.add_argument("--out", required=True, help="reach.jsonl in a git-ignored run folder")
    ap.add_argument("--max-workers", type=int, default=4)
    a = ap.parse_args(argv)
    urls, ua = load_urls(a.targets_file), research_ua()
    with open(a.out, "w", encoding="utf-8") as out, cf.ThreadPoolExecutor(a.max_workers) as ex:
        counts = collections.Counter()
        for rec in ex.map(lambda u: check(u, ua), urls):
            out.write(json.dumps(rec) + "\n")
            out.flush()
            counts[rec["diagnosis"]] += 1
    for k, n in counts.most_common():
        print("{:>5}  {}".format(n, k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
