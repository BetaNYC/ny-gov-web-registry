"""crawler_list.py — read data/ai-readability/crawlers.csv, check it, and turn it into the
identities the scanner sends (issue #44; rules in docs/ai-readability/crawlers.md).

Pure: reads one CSV, touches no network. probe_bot_access.py builds its vendor identities from
here, and its pre-run guard (`guard()`) refuses to start when the list fails its own checks.

HOW A ROW BECOMES WHAT WE SEND (Noel's rules: labels are copied word for word, never assembled,
and no version is ever filled in)
  - `send=rules-only`: never sent. The name is read in robots.txt only.
  - `ua_exact = token only` (the company publishes the name but no full label, e.g. Anthropic's
    ClaudeBot): the bare published name is sent exactly as written, `label_kind: token-only`.
  - Otherwise `ua_exact` holds one or more published labels joined by ` || `. ONE label is sent
    per name: the first one, in the company's own order, that
      1. carries no version stand-in (`W.X.Y.Z`): a stand-in is never filled in, and
      2. is not one of Meta's relative-link forms (`(+/documentation/...)`): crawlers.md rule 4
         says Meta's page also publishes the short form on its own, and that is the one to send.
    One label per name, not every variant, because the study asks whether the NAME is admitted;
    every variant would multiply the requests (about 1.5×) to answer a question nobody asked.
    Every candidate label and the reason each one was skipped are kept in the identity record.
  - A row whose every label carries a stand-in (Google-Agent, Amazon's three) is NOT sent, with
    the reason recorded. Its name is still read in robots.txt.
"""

from __future__ import annotations

import csv
import datetime
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CRAWLERS_CSV = ROOT / "data" / "ai-readability" / "crawlers.csv"

HEADER = ["company", "name", "purpose", "ua_exact", "send", "follows_robots_per_vendor",
          "publishes_ips", "ip_list_url", "doc_url", "checked", "proof_quote", "reason_not_sent"]
SEND = {"yes", "rules-only"}
ROBOTS = {"yes", "no", "partly", "not stated"}
IPS = {"yes", "no"}
TOKEN_ONLY = "token only"
NO_PAGE = "no official page"
LABEL_SEP = " || "
MAX_AGE_DAYS = 30

# A version stand-in in a published label (Google, Amazon and Bing print `Chrome/W.X.Y.Z`).
STAND_IN_RX = re.compile(r"W\.X\.Y\.Z")
# Meta's long labels carry a relative link exactly as its page shows it (crawlers.md rule 4).
RELATIVE_LINK = "(+/"

# Names whose company publishes the label as an example that may change (crawlers.md rule 3).
# Sent exactly as published on the check date and marked `example: true`.
EXAMPLE_LABEL_NAMES = {"Claude-Agent", "meta-externalagent", "meta-externalfetcher",
                       "meta-webindexer", "Google-GeminiNotebook"}

# Class per sent name: which W4 aggregate a name counts toward (`ai_*` = AI identities). The CSV
# has no class column, so the class is assigned here from the row's own `purpose`; the guard
# refuses a run when a sent name has no class, so a new row cannot slip in unclassified.
CLASSES = {
    "Googlebot": "search", "bingbot": "search",
    # Applebot is Apple's search crawler; Apple's AI-training control is Applebot-Extended,
    # which is rules-only.
    "Applebot": "search",
    "GPTBot": "ai_training", "ClaudeBot": "ai_training", "CCBot": "ai_training",
    "meta-externalagent": "ai_training", "MistralAI-Training": "ai_training",
    "Amazonbot": "ai_training",
    "OAI-SearchBot": "ai_search", "PerplexityBot": "ai_search", "Claude-SearchBot": "ai_search",
    "meta-webindexer": "ai_search", "MistralAI-Index": "ai_search", "Amzn-SearchBot": "ai_search",
    "DuckAssistBot": "ai_search",
    "ChatGPT-User": "ai_agentic", "Claude-User": "ai_agentic", "Perplexity-User": "ai_agentic",
    "meta-externalfetcher": "ai_agentic", "MistralAI-User": "ai_agentic",
    "Amzn-User": "ai_agentic", "Google-Agent": "ai_agentic", "Claude-Agent": "ai_agentic",
    "Google-GeminiNotebook": "ai_agentic",
}


def key_for(name: str) -> str:
    """Identity key from the published name: `OAI-SearchBot` -> `oai_searchbot` (the keys the
    2026-11-06 run already used)."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def is_pseudo_name(name: str) -> bool:
    """The `(none: Copilot-specific name)` row records an absence; it is not a robots token."""
    return name.startswith("(")


def load(path=None) -> list:
    path = pathlib.Path(path) if path else CRAWLERS_CSV
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        if reader.fieldnames != HEADER:
            raise ValueError("{}: header is {}, expected {}".format(path, reader.fieldnames, HEADER))
    return rows


def labels(row: dict) -> list:
    v = row.get("ua_exact") or ""
    if v in ("", TOKEN_ONLY, NO_PAGE):
        return []
    return v.split(LABEL_SEP)


def check(rows: list) -> list:
    """The list's own checks (the same rules tests/test_crawlers_list.py holds the file to).
    Returns problems; empty means clean."""
    problems = []
    seen = set()
    for r in rows:
        n = r.get("name") or "?"
        if r.get("send") not in SEND:
            problems.append("{}: send={!r} is not yes or rules-only".format(n, r.get("send")))
        if r.get("follows_robots_per_vendor") not in ROBOTS:
            problems.append("{}: follows_robots_per_vendor={!r}".format(n, r.get("follows_robots_per_vendor")))
        if r.get("publishes_ips") not in IPS:
            problems.append("{}: publishes_ips={!r}".format(n, r.get("publishes_ips")))
        try:
            datetime.date.fromisoformat(r.get("checked") or "")
        except ValueError:
            problems.append("{}: checked={!r} is not a date".format(n, r.get("checked")))
        if not (r.get("company") and r.get("name") and r.get("purpose")):
            problems.append("{}: company, name and purpose are required".format(n))
        if n.lower() in seen:
            problems.append("{}: name listed twice".format(n))
        seen.add(n.lower())
        for v in r.values():
            if v and ("NEEDS BROWSER" in v or "TODO" in v):
                problems.append("{}: unfinished row".format(n))
                break
        if r.get("send") == "yes":
            if not r.get("ua_exact") or r["ua_exact"] == NO_PAGE:
                problems.append("{}: sent but has no label".format(n))
            if not (r.get("doc_url") or "").startswith("https://"):
                problems.append("{}: sent but has no https company page".format(n))
            for label in labels(r):
                if n.lower() not in label.lower():
                    problems.append("{}: label does not contain the name: {}".format(n, label))
        if r.get("send") == "rules-only" and not (r.get("reason_not_sent") or "").strip():
            problems.append("{}: rules-only without reason_not_sent".format(n))
    return problems


def choose_label(row: dict) -> dict:
    """What to send for one `send=yes` row. Returns {label, label_kind, candidates, reason}; label
    is None (with a reason) when no published label may be sent."""
    name = row["name"]
    if row.get("ua_exact") == TOKEN_ONLY:
        return {"label": name, "label_kind": "token-only", "reason": None,
                "candidates": [{"label": name, "used": True,
                                "note": "company publishes the name only; sent bare, never assembled"}]}
    cands, chosen = [], None
    for lab in labels(row):
        skip = None
        if STAND_IN_RX.search(lab):
            skip = "carries a version stand-in (W.X.Y.Z); never filled in"
        elif RELATIVE_LINK in lab:
            skip = "Meta's relative-link form; its page also publishes the short form (rule 4)"
        elif chosen is not None:
            skip = "a published label earlier in the row is sent (one label per name)"
        if skip is None:
            chosen = lab
        cands.append({"label": lab, "used": skip is None, "note": skip})
    if chosen is None:
        return {"label": None, "label_kind": None, "candidates": cands,
                "reason": "every published label carries a version stand-in (W.X.Y.Z); a "
                          "stand-in is never filled in, so this name is read in robots.txt only"}
    return {"label": chosen, "label_kind": "published", "reason": None, "candidates": cands}


def build_identities(rows: list) -> "tuple[list, list]":
    """(sent identities, not-sent records) from the rows. Identity dicts match the scanner's
    shape: key, label, ua, klass, string_verified, doc, plus name, label_kind, example, checked,
    candidates and source."""
    sent, not_sent = [], []
    for r in rows:
        name = r["name"]
        if is_pseudo_name(name):
            not_sent.append({"name": name, "company": r["company"], "send": r["send"],
                             "reason": r.get("reason_not_sent") or "not a crawler name"})
            continue
        if r["send"] != "yes":
            not_sent.append({"name": name, "company": r["company"], "send": r["send"],
                             "reason": r.get("reason_not_sent")})
            continue
        pick = choose_label(r)
        if pick["label"] is None:
            not_sent.append({"name": name, "company": r["company"], "send": r["send"],
                             "reason": pick["reason"], "candidates": pick["candidates"]})
            continue
        sent.append({
            "key": key_for(name), "name": name, "label": "{} ({})".format(name, r["purpose"]),
            "ua": pick["label"], "klass": CLASSES.get(name), "string_verified": True,
            "label_kind": pick["label_kind"], "example": name in EXAMPLE_LABEL_NAMES,
            "doc": r["doc_url"], "checked": r["checked"], "candidates": pick["candidates"],
            "source": "data/ai-readability/crawlers.csv"})
    return sent, not_sent


def robots_tokens(rows: list) -> list:
    """Every name in the list (sent and rules-only), in file order: the robots.txt tokens."""
    return [r["name"] for r in rows if not is_pseudo_name(r["name"])]


def published_labels(rows: list) -> set:
    """Every string the list allows on the wire: each published label, and the bare name of a
    token-only row."""
    out = set()
    for r in rows:
        if r.get("send") != "yes":
            continue
        if r.get("ua_exact") == TOKEN_ONLY:
            out.add(r["name"])
        out.update(labels(r))
    return out


def guard(rows: list, identities: list, today: datetime.date, controls=(),
          max_age_days: int = MAX_AGE_DAYS) -> list:
    """The pre-run guard. Reasons to refuse the run; empty means go. Refuses when the list fails
    its own checks, when a sent label is not in the list (code-defined controls excepted), when a
    sent label still carries a stand-in, when a sent name has no class, or when any row's
    `checked` is more than `max_age_days` old."""
    reasons = ["crawlers.csv: " + p for p in check(rows)]
    allowed = published_labels(rows)
    for ident in identities:
        if ident["key"] in controls:
            continue
        ua = ident.get("ua")
        if not ua or ua not in allowed:
            reasons.append("identity {} sends a label that is not in crawlers.csv: {!r}".format(
                ident["key"], ua))
        elif STAND_IN_RX.search(ua):
            reasons.append("identity {} sends a version stand-in: {!r}".format(ident["key"], ua))
        if not ident.get("klass"):
            reasons.append("identity {} ({}) has no class in crawler_list.CLASSES".format(
                ident["key"], ident.get("name")))
    for r in rows:
        try:
            age = (today - datetime.date.fromisoformat(r.get("checked") or "")).days
        except ValueError:
            continue                      # already reported by check()
        if age > max_age_days:
            reasons.append("{}: checked {} is {} days old (limit {}); re-check the row against "
                           "its company page".format(r["name"], r["checked"], age, max_age_days))
    return reasons
