"""Fastmail JMAP client: change keywords (MailMate tags) on one message.

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


def update_keywords(
    message_id: str, add: list[str], remove: list[str]
) -> tuple[list[str], list[str]]:
    """Add and remove keywords on the one Fastmail message with this Message-ID.

    Keywords are sent in lowercase (JMAP keywords are case-insensitive and
    Fastmail returns them lowercased). Returns (keywords_before, keywords_after).
    Raises JMAPError, and changes nothing, unless exactly one message matches.
    """
    session = _request(SESSION_URL)
    account_id = session["primaryAccounts"]["urn:ietf:params:jmap:mail"]
    api_url = session["apiUrl"]

    bare_id = message_id.strip().strip("<>")
    ids = _call(api_url, account_id, "Email/query", {"filter": {"header": ["Message-ID", bare_id]}})["ids"]
    if len(ids) != 1:
        raise JMAPError(f"Expected one Fastmail message with Message-ID {bare_id}, found {len(ids)}. Nothing was changed.")
    email_id = ids[0]

    def keywords() -> list[str]:
        got = _call(api_url, account_id, "Email/get", {"ids": [email_id], "properties": ["keywords"]})
        return sorted(got["list"][0]["keywords"])

    before = keywords()
    patch: dict[str, bool | None] = {f"keywords/{k.lower()}": True for k in add}
    patch.update({f"keywords/{k.lower()}": None for k in remove})
    result = _call(api_url, account_id, "Email/set", {"update": {email_id: patch}})
    if result.get("notUpdated"):
        raise JMAPError(f"Fastmail did not update the message: {result['notUpdated'][email_id]}")
    return before, keywords()
