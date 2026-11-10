"""network_record.py — which network a run went out on (issue #44).

Every run.json carries a `network` record: the place (office | home | vpn, from --vantage), the
internet provider, its network number (ASN), the public address, and the time, read at the start
of the run and again at the end. A block can be about our network, so the network is part of
the measurement.

ONE DOCUMENTED SERVICE: the RIPEstat Data API (RIPE NCC), no token. Fields used, each as its
docs define it (read 2026-11-10):
  - whats-my-ip   https://stat.ripe.net/docs/02.data-api/whats-my-ip.html
                  `ip`: "contains the requester's IP address"
  - network-info  https://stat.ripe.net/docs/02.data-api/network-info.html
                  `asns`: "ASNs the prefix is announced from"; `prefix`
  - as-overview   https://stat.ripe.net/docs/02.data-api/as-overview.html
                  `holder`: "Descriptive name of the ASN holder"
  Every call's fields sit under `data`, with `status` "ok" | "error" | "maintenance"
  (https://stat.ripe.net/docs/02.data-api/). The `sourceapp` parameter identifies us, as the
  docs ask of regular users.

ipinfo.io was the first choice and was not used: its docs (ipinfo.io/developers, core-api,
lite-api, read 2026-11-10) do not document `https://ipinfo.io/json` without a token and show no
`org` field; the documented endpoints need a token.

FAILURE IS RECORDED, NEVER GUESSED. A lookup that fails leaves its fields null and says why in
`error`. Nothing here knows what any network "should" be: the office and home values in the
tests (Charter/Spectrum AS12271, Verizon Business AS701) are fixtures only.

Three small requests per reading, to stat.ripe.net only (never a government site), twice a run.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SERVICE = "RIPEstat Data API"
BASE = "https://stat.ripe.net/data/{call}/data.json"
DOCS = {
    "whats-my-ip": "https://stat.ripe.net/docs/02.data-api/whats-my-ip.html",
    "network-info": "https://stat.ripe.net/docs/02.data-api/network-info.html",
    "as-overview": "https://stat.ripe.net/docs/02.data-api/as-overview.html",
}
SOURCEAPP = "betanyc-ny-gov-web-registry"
PLACES = ("office", "home", "vpn")
TIMEOUT_S = 15
USER_AGENT = ("BetaNYC-agentic-access-study/1.0 "
              "(+https://github.com/BetaNYC/ny-gov-web-registry; noel@beta.nyc)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def url_for(call: str, **params) -> str:
    params["sourceapp"] = SOURCEAPP
    return BASE.format(call=call) + "?" + urllib.parse.urlencode(params)


def http_json(url: str):
    """GET a RIPEstat URL; returns the parsed JSON. Raises on any failure (caller records it)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read(200_000).decode("utf-8"))


def _data(doc: dict, call: str) -> dict:
    if not isinstance(doc, dict):
        raise ValueError("{}: response is not a JSON object".format(call))
    if doc.get("status") != "ok":
        raise ValueError("{}: status {!r}{}".format(
            call, doc.get("status"), ": " + str(doc["message"])[:200] if doc.get("message") else ""))
    data = doc.get("data")
    if not isinstance(data, dict):
        raise ValueError("{}: no `data` object".format(call))
    return data


def lookup(get=None, now=_now) -> dict:
    """One reading. `get(url) -> parsed JSON` is injectable so tests use a fake. Fields that
    could not be read stay None; `error` says what failed."""
    get = get or http_json
    rec = {"time": now(), "service": SERVICE, "docs": DOCS, "public_ip": None, "asn": None,
           "provider": None, "prefix": None, "error": None}
    try:
        ip = _data(get(url_for("whats-my-ip")), "whats-my-ip").get("ip")
        if not ip:
            raise ValueError("whats-my-ip: no `ip` in data")
        rec["public_ip"] = ip
        info = _data(get(url_for("network-info", resource=ip)), "network-info")
        asns = info.get("asns") or []
        rec["prefix"] = info.get("prefix")
        if not asns:
            raise ValueError("network-info: no `asns` for {}".format(ip))
        if len(asns) > 1:
            rec["asns_all"] = ["AS{}".format(a) for a in asns]
        rec["asn"] = "AS{}".format(str(asns[0]).upper().lstrip("AS"))
        over = _data(get(url_for("as-overview", resource=rec["asn"])), "as-overview")
        rec["provider"] = over.get("holder")
        if not rec["provider"]:
            raise ValueError("as-overview: no `holder` for {}".format(rec["asn"]))
    except Exception as e:  # recorded, never guessed
        rec["error"] = "{}: {}".format(type(e).__name__, str(e)[:300])
    return rec


def place_of(vantage: str):
    v = (vantage or "").strip().lower()
    return v if v in PLACES else None


def start_record(vantage: str, reading: dict) -> dict:
    rec = {"place": place_of(vantage), "vantage": vantage, "start": reading, "end": None}
    if rec["place"] is None:
        rec["place_note"] = "vantage {!r} is not one of {}".format(vantage, ", ".join(PLACES))
    return rec


def finish_record(rec: dict, reading: dict) -> dict:
    """Adds the end reading and whether the network changed during the run. `changed` is None
    when either reading failed: an unreadable network is not evidence that it stayed put."""
    rec["end"] = reading
    s, e = rec.get("start") or {}, reading or {}
    if s.get("error") or e.get("error") or not s.get("asn") or not e.get("asn"):
        rec["changed"] = None
    else:
        rec["changed"] = {"asn": s["asn"] != e["asn"], "public_ip": s["public_ip"] != e["public_ip"]}
    return rec
