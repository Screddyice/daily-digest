"""Top 5 — NEBOS: the day's most important things to handle.

NEBOS is Team Nebula's central context hub; this section "references" it by
pulling from the two systems behind it that hold the actionable signal:

- Linear — open issues assigned to you (company action items / your to-dos).
- Gmail — external people waiting on a reply (client emails to get back to).

Both are reached through the same Composio REST gateway the meetings section
uses (NEB_COMPOSIO_MCP_API_KEY). The two streams are merged, ranked by a
simple, tunable urgency score, and the top 5 are rendered — nothing else.

Like meetings/bella, every failure path degrades to None so this section can
never sink the digest; the composer drops it when there's nothing to show.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import date

logger = logging.getLogger(__name__)

COMPOSIO_EXECUTE = "https://backend.composio.dev/api/v3/tools/execute/{slug}"
TOP_N = 5
EMAIL_LOOKBACK_DAYS = 21
# The org's own domain — internal mail isn't a "client email to get back to".
COMPANY_DOMAIN = os.environ.get("NEBOS_COMPANY_DOMAIN", "teamnebula.ai")
# Client domains — only mail from these counts as a "client email to reply to".
# Seeded with Team Nebula's known clients (Rivus, Newcalgon, RS21); override with
# NEBOS_CLIENT_DOMAINS as the client list grows (e.g. add Volt Truck's domain).
_DEFAULT_CLIENT_DOMAINS = "rivus.mx,newcalgon.net,rs21.io"


def client_domains() -> set[str]:
    """Allowlist of client email domains, from NEBOS_CLIENT_DOMAINS (comma-sep).
    Empty → fall back to 'any external sender' (COMPANY_DOMAIN excluded)."""
    raw = os.environ.get("NEBOS_CLIENT_DOMAINS", _DEFAULT_CLIENT_DOMAINS)
    return {d.strip().lower() for d in raw.split(",") if d.strip()}
# Senders that are automated, not a person awaiting your reply.
_AUTOMATED = re.compile(
    r"no[-_]?reply|noreply|do[-_]?not[-_]?reply|notifications?@|mailer-daemon"
    r"|gemini-notes@|calendar-notification@|@google\.com|@docs\.google\.com"
    r"|postmaster@|@link\.com|@bounce|@em\.|@mailchimp|@sendgrid",
    re.IGNORECASE)
# Calendar RSVP / invite auto-subjects (EN + ES) — not a reply you owe anyone.
_CALENDAR_NOISE = re.compile(
    r"^(accepted|declined|tentative|invitation|updated invitation|canceled|cancelled"
    r"|aceptado|rechazado|provisional|invitaci[oó]n):",
    re.IGNORECASE)

# Open issues assigned to me, with the fields needed to rank them.
LINEAR_QUERY = (
    "query { viewer { assignedIssues(first: 50, filter: { state: { type: "
    "{ nin: [\"completed\",\"canceled\"] } } }) { nodes { identifier title "
    "priority priorityLabel dueDate url state { name } team { key } } } } }"
)
GMAIL_QUERY = (f"in:inbox is:unread newer_than:{EMAIL_LOOKBACK_DAYS}d "
               "-category:promotions -category:social -category:updates -category:forums")

# Urgency scores — lower sorts first. Tune these to reshuffle what surfaces.
_PRIORITY_RANK = {1: 1, 2: 3, 3: 6, 4: 8}   # Linear: 1=Urgent 2=High 3=Med 4=Low
_NO_PRIORITY = 9                            # 0/unset
_OVERDUE_BOOST = 5                          # subtract when an item is past due
_EMAIL_HOT = 2                              # recent client email awaiting reply
_EMAIL_WARM = 7                             # older client email awaiting reply
_EMAIL_HOT_DAYS = 7


# ----------------------------------------------------------------- transport
def make_call(env: dict, timeout: float = 40.0):
    """`call(slug, arguments)` bound to the Composio account (same gateway as
    the meetings section)."""
    key = env["NEB_COMPOSIO_MCP_API_KEY"]
    uid = env.get("NEB_COMPOSIO_MCP_USER_ID", "user_uwgmr")

    def call(slug: str, arguments: dict) -> dict:
        body = json.dumps({"user_id": uid, "arguments": arguments}).encode()
        req = urllib.request.Request(
            COMPOSIO_EXECUTE.format(slug=slug), data=body, method="POST",
            headers={"x-api-key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    return call


def _walk(obj, key):
    """First value for `key` anywhere in a nested structure (Composio nests
    the real payload at varying depths)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _walk(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk(v, key)
            if found is not None:
                return found
    return None


# ----------------------------------------------------------------- Linear
def parse_issues(resp: dict) -> list[dict]:
    """Composio/GraphQL response -> [{id,title,priority,priority_label,due,team}]."""
    nodes = _walk(resp, "nodes") or []
    out = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("identifier"):
            continue
        out.append({
            "id": n.get("identifier"),
            "title": n.get("title") or "(untitled)",
            "priority": n.get("priority") or 0,
            "priority_label": n.get("priorityLabel") or "No priority",
            "due": n.get("dueDate"),
            "team": (n.get("team") or {}).get("key"),
        })
    return out


def _issue_candidate(issue: dict, today: date) -> dict:
    pr = _PRIORITY_RANK.get(issue["priority"], _NO_PRIORITY)
    overdue = False
    if issue["due"]:
        try:
            overdue = date.fromisoformat(issue["due"]) < today
        except ValueError:
            overdue = False
    score = pr - (_OVERDUE_BOOST if overdue else 0)
    bits = [issue["priority_label"]]
    if issue["due"]:
        bits.append(f"overdue, due {issue['due']}" if overdue else f"due {issue['due']}")
    line = f"• [{issue['id']}] {issue['title']} ({' · '.join(bits)})"
    # tie-break: sooner due date first, then issue id for stability
    return {"score": score, "tiebreak": issue["due"] or "9999-12-31",
            "id2": issue["id"], "line": line}


# ----------------------------------------------------------------- Gmail
def _header(msg: dict, name: str) -> str:
    for h in (msg.get("payload", {}).get("headers") or []):
        if (h.get("name") or "").lower() == name:
            return h.get("value") or ""
    return ""


def _sender_name_org(from_header: str) -> tuple[str, str | None]:
    """'Craig Hardy <chardy@ampere.com>' -> ('Craig Hardy', 'Ampere')."""
    m = re.match(r"\s*(.*?)\s*<([^>]+)>", from_header)
    name = (m.group(1).strip().strip('"') if m else "").strip()
    addr = (m.group(2) if m else from_header).strip()
    domain = addr.split("@")[-1].lower() if "@" in addr else ""
    org = domain.split(".")[0].capitalize() if domain else None
    if not name:
        name = addr.split("@")[0].replace(".", " ").title()
    return name, org


def _domain_of(from_header: str) -> str:
    m = re.search(r"@([\w.-]+)", from_header or "")
    return m.group(1).lower().rstrip(">").strip() if m else ""


def _is_client(domain: str, domains: set[str]) -> bool:
    """domain matches the allowlist exactly or as a subdomain (mail.rivus.mx)."""
    return any(domain == d or domain.endswith("." + d) for d in domains)


def parse_client_emails(resp: dict, today: date, *, domains: set[str] | None = None) -> list[dict]:
    """Threads whose latest message is a client awaiting your reply.

    A message counts only when it's the latest in its thread, inbound (you
    didn't send it), from a non-automated sender, and from a client domain.
    `domains` is the client allowlist; when empty, any external (non-company)
    sender qualifies — drops back to the pre-allowlist behavior.
    """
    domains = client_domains() if domains is None else domains
    threads = _walk(resp, "threads") or []
    out = []
    for t in threads:
        msgs = [m for m in (t.get("messages") or [])
                if "DRAFT" not in (m.get("labelIds") or [])]
        if not msgs:
            continue
        latest = max(msgs, key=lambda m: int(m.get("internalDate") or 0))
        labels = latest.get("labelIds") or []
        if "SENT" in labels:            # you spoke last — nothing owed
            continue
        frm = _header(latest, "from")
        if not frm or _AUTOMATED.search(frm):
            continue
        domain = _domain_of(frm)
        if domains:
            if not _is_client(domain, domains):     # not a client — skip
                continue
        elif COMPANY_DOMAIN.lower() in frm.lower():  # no allowlist: drop internal
            continue
        subject = (_header(latest, "subject") or "(no subject)").strip()
        if _CALENDAR_NOISE.match(subject):   # calendar RSVP/invite, not a reply owed
            continue
        name, org = _sender_name_org(frm)
        subject = re.sub(r"^(?:(?:re|fw|fwd)\s*:\s*)+", "", subject, flags=re.IGNORECASE)
        age_days = (today - date.fromtimestamp(int(latest["internalDate"]) / 1000)).days \
            if latest.get("internalDate") else 99
        out.append({"name": name, "org": org, "subject": subject,
                    "age_days": age_days, "important": "IMPORTANT" in labels})
    return out


def _email_candidate(email: dict) -> dict:
    score = _EMAIL_HOT if email["age_days"] <= _EMAIL_HOT_DAYS else _EMAIL_WARM
    who = f"{email['name']} ({email['org']})" if email["org"] else email["name"]
    line = f"• Reply to {who} — {email['subject']}"
    return {"score": score, "tiebreak": f"{email['age_days']:03d}",
            "id2": email["name"], "line": line}


# ---------------------------------------------------------------- composition
def render_section(lines: list[str]) -> str:
    L = ["🎯 Top 5 — NEBOS", ""]
    L += lines
    return "\n".join(L)


def _meeting_candidate(item: dict) -> dict | None:
    """Turn an explicitly Shawn-owned meeting action into a ranked candidate."""
    if not item.get("mine") or not str(item.get("text") or "").strip():
        return None
    age = max(0, int(item.get("age_days") or 0))
    text = str(item["text"]).strip()
    text = re.sub(
        r"^shawn(?:\s+reddy)?\s+(?:to|will|should|needs\s+to|must)\s+",
        "", text, flags=re.IGNORECASE)
    if text:
        text = text[0].upper() + text[1:]
    title = str(item.get("title") or "recent meeting").strip()
    day = str(item.get("day") or "recently").strip()
    return {
        "score": min(age, 3),
        "tiebreak": f"{age:03d}",
        "id2": f"meeting:{title}:{text}",
        "dedupe": re.sub(r"\W+", " ", text.lower()).strip(),
        "line": f"• {text} — meeting note: {title}, {day}",
    }


def build_shawn_action_section(today: date | None = None, *, env: dict | None = None,
                               call=None, meeting_items: list[dict] | None = None) -> str | None:
    """Up to five concrete actions for Shawn from meetings and Linear only.

    Linear's ``viewer.assignedIssues`` provides the tenant/user boundary. Meeting
    actions qualify only when the note explicitly names Shawn as the owner.
    Gmail, general team work, and ownerless meeting notes are intentionally out.
    """
    today = today or date.today()
    env = os.environ if env is None else env
    candidates = [c for item in (meeting_items or []) if (c := _meeting_candidate(item))]

    if call is None and env.get("NEB_COMPOSIO_MCP_API_KEY"):
        call = make_call(env)
    if call is not None:
        try:
            issues = parse_issues(call("LINEAR_RUN_QUERY_OR_MUTATION",
                                       {"query_or_mutation": LINEAR_QUERY}))
            for issue in issues:
                c = _issue_candidate(issue, today)
                c["dedupe"] = re.sub(r"\W+", " ", issue["title"].lower()).strip()
                details = [issue["priority_label"]]
                if issue["due"]:
                    try:
                        overdue = date.fromisoformat(issue["due"]) < today
                    except ValueError:
                        overdue = False
                    details.append(
                        f"overdue; due {issue['due']}" if overdue else f"due {issue['due']}")
                c["line"] = (
                    f"• {issue['title']} — Linear {issue['id']} · "
                    f"{' · '.join(details)}")
                candidates.append(c)
        except Exception as exc:
            logger.warning("nebos: linear fetch failed: %s", exc)

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c["score"], c["tiebreak"], c["id2"]))
    seen: set[str] = set()
    lines = []
    for candidate in candidates:
        key = candidate["dedupe"]
        if not key or key in seen:
            continue
        seen.add(key)
        lines.append(candidate["line"])
        if len(lines) == TOP_N:
            break
    if not lines:
        return None
    return "\n".join(["🎯 Shawn — Top 5 actions", "", *lines])


def build_section(today: date | None = None, *, env: dict | None = None,
                  call=None, domains: set[str] | None = None) -> str | None:
    """The Top-5 section, or None when there's nothing to show — Composio not
    configured, both sources unreachable, or no open items / client emails."""
    today = today or date.today()
    env = os.environ if env is None else env
    if call is None:
        if not env.get("NEB_COMPOSIO_MCP_API_KEY"):
            return None  # gateway not configured — section dropped
        call = make_call(env)

    candidates: list[dict] = []
    try:
        issues = parse_issues(call("LINEAR_RUN_QUERY_OR_MUTATION",
                                   {"query_or_mutation": LINEAR_QUERY}))
        candidates += [_issue_candidate(i, today) for i in issues]
    except Exception as exc:  # one source down shouldn't sink the other
        logger.warning("nebos: linear fetch failed: %s", exc)
    try:
        emails = parse_client_emails(call("GMAIL_LIST_THREADS",
                                          {"query": GMAIL_QUERY, "max_results": 25,
                                           "verbose": True}), today, domains=domains)
        candidates += [_email_candidate(e) for e in emails]
    except Exception as exc:
        logger.warning("nebos: gmail fetch failed: %s", exc)

    if not candidates:
        return None  # nothing actionable — drop the section
    candidates.sort(key=lambda c: (c["score"], c["tiebreak"], c["id2"]))
    return render_section([c["line"] for c in candidates[:TOP_N]])
