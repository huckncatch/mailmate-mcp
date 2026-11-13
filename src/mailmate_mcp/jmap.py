"""Fastmail JMAP client: change keywords (MailMate tags) on a batch of messages.

Network layer only. No MCP or MailMate dependencies. The token comes from
the FASTMAIL_API_TOKEN environment variable, which the launcher script sets.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

SESSION_URL = "https://api.fastmail.com/jmap/session"
USING = ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"]
TIMEOUT = 20  # seconds
MAX_MESSAGES = 100  # per tag_message call
QUERY_LIMIT = 500  # Email/query results; a Message-ID can match more than one email


class JMAPError(RuntimeError):
    pass


def _request(url: str, body: dict | None = None) -> dict:
    token = os.environ.get("FASTMAIL_API_TOKEN")
    if not token:
        raise JMAPError(
            "FASTMAIL_API_TOKEN is not set. Start the server through "
            "~/config/brain/bin/mailmate-mcp.sh."
        )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise JMAPError(f"Fastmail returned HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise JMAPError(f"Could not reach Fastmail: {e.reason}") from e


def _call(api_url: str, account_id: str, method: str, args: dict) -> dict:
    body = {"using": USING, "methodCalls": [[method, {"accountId": account_id, **args}, "c"]]}
    name, result, _ = _request(api_url, body)["methodResponses"][0]
    if name == "error":
        raise JMAPError(f"{method} failed: {result.get('type')} {result.get('description', '')}".strip())
    return result


def find_emails(
    api_url: str, account_id: str, message_ids: list[str]
) -> dict[str, list[dict]]:
    """Return {Message-ID: [{"id", "keywords"}, ...]} for the Fastmail emails with each Message-ID.

    The JMAP header filter matches substrings, so results are grouped by
    each email's exact messageId. A Message-ID with no email maps to [].
    """
    bare = [m.strip().strip("<>") for m in message_ids]
    conditions = [{"header": ["Message-ID", m]} for m in bare]
    query = _call(api_url, account_id, "Email/query", {
        "filter": {"operator": "OR", "conditions": conditions},
        "calculateTotal": True,
        "limit": QUERY_LIMIT,
    })
    if query.get("total", 0) > len(query["ids"]):
        raise JMAPError(
            f"Fastmail matched {query['total']} emails but returned {len(query['ids'])}. "
            "Tag fewer messages per call. Nothing was changed."
        )
    found: dict[str, list[dict]] = {m: [] for m in bare}
    if not query["ids"]:
        return found
    got = _call(api_url, account_id, "Email/get", {
        "ids": query["ids"], "properties": ["messageId", "keywords"],
    })
    for email in got["list"]:
        for mid in email.get("messageId") or []:
            if mid in found:
                found[mid].append({"id": email["id"], "keywords": sorted(email["keywords"])})
    return found


def update_keywords(
    message_ids: list[str], add: list[str], remove: list[str]
) -> dict[str, dict]:
    """Add and remove keywords on the Fastmail messages with these Message-IDs.

    Keywords are sent in lowercase (JMAP keywords are case-insensitive and
    Fastmail returns them lowercased). Returns {Message-ID: result}, where
    result is {"before": [...], "after": [...]} or {"error": "..."}. A
    message is changed only when exactly one Fastmail email has its Message-ID.
    """
    session = _request(SESSION_URL)
    account_id = session["primaryAccounts"]["urn:ietf:params:jmap:mail"]
    api_url = session["apiUrl"]

    results: dict[str, dict] = {}
    targets: dict[str, dict] = {}  # Message-ID -> the one matching email
    for mid, emails in find_emails(api_url, account_id, message_ids).items():
        if len(emails) == 1:
            targets[mid] = emails[0]
        else:
            results[mid] = {"error": f"Expected one Fastmail message with this Message-ID, found {len(emails)}. Nothing was changed."}
    if not targets:
        return results

    patch: dict[str, bool | None] = {f"keywords/{k.lower()}": True for k in add}
    patch.update({f"keywords/{k.lower()}": None for k in remove})
    update = {email["id"]: patch for email in targets.values()}
    not_updated = _call(api_url, account_id, "Email/set", {"update": update}).get("notUpdated") or {}

    got = _call(api_url, account_id, "Email/get", {"ids": list(update), "properties": ["keywords"]})
    after = {email["id"]: sorted(email["keywords"]) for email in got["list"]}
    for mid, email in targets.items():
        if email["id"] in not_updated:
            results[mid] = {"error": f"Fastmail did not update the message: {not_updated[email['id']]}"}
        else:
            results[mid] = {"before": email["keywords"], "after": after.get(email["id"], [])}
    return results
