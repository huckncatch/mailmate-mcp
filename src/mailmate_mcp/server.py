"""MailMate MCP server — search, read, move, tag, and link emails."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP

from .mailstore import (
    MAILMATE_ROOT,
    find_eml_by_message_id,
    get_message_body,
    list_mailboxes_all,
    message_id_to_url,
    parse_eml,
    search_messages,
    summary_from_eml,
    url_to_message_id,
)

mcp = FastMCP(
    "mailmate",
    instructions=(
        "Access and manage email in the local MailMate installation. "
        "Messages are identified by message:// URLs (e.g. "
        "message://%3CfTJUwhWlRX2F6Fi4B07W0g@geopod-ismtpd-52%3E). "
        "Use search_messages to find emails, get_message to read them, "
        "move_message and tag_message to organize, and get_message_link "
        "to obtain shareable message:// URLs."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_applescript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _resolve_message(message_url: str) -> tuple[Path, dict]:
    """Find the .eml file for a message:// URL and return (path, headers)."""
    message_id = url_to_message_id(message_url)
    eml_path = find_eml_by_message_id(message_id)
    if eml_path is None:
        raise FileNotFoundError(
            f"No message found for Message-ID {message_id!r}. "
            "Make sure MailMate has downloaded the message."
        )
    headers = parse_eml(eml_path)
    return eml_path, headers


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool
def search_messages_tool(
    query: Annotated[str, "Search query (matched against subject, from, to); leave empty to match all"] = "",
    tag: Annotated[str | None, "Filter to messages with this tag (exact match, case-insensitive)"] = None,
    account: Annotated[
        str | None,
        "Optional account filter (substring match on email address)",
    ] = None,
    mailbox: Annotated[
        str | None,
        "Optional mailbox filter (substring match, e.g. 'INBOX', 'Archive')",
    ] = None,
    include_body: Annotated[
        bool,
        "Also search inside message body text (slower)",
    ] = False,
    max_results: Annotated[int, "Maximum number of results to return"] = 20,
) -> list[dict]:
    """
    Search MailMate messages by query string and/or tag.

    query and tag are both optional — omit query to list all messages with a given tag,
    or omit tag to search by text only. Use list_tags to see available tags.

    Returns a list of matching messages with subject, from, to, date,
    mailbox, tags, and message_url for each.
    """
    results = search_messages(
        query=query,
        search_body=include_body,
        account=account,
        mailbox=mailbox,
        tag=tag,
        max_results=max_results,
    )
    return [
        {
            "message_url": m.message_url,
            "subject": m.subject,
            "from": m.from_,
            "to": m.to,
            "date": m.date,
            "account": m.account,
            "mailbox": m.mailbox,
            "tags": m.tags,
            "message_id": m.message_id,
        }
        for m in results
    ]


@mcp.tool
def get_message(
    message_url: Annotated[
        str,
        "The message:// URL of the message to retrieve",
    ],
    include_body: Annotated[bool, "Include the message body text"] = True,
    prefer_html: Annotated[bool, "Prefer HTML body over plain text"] = False,
) -> dict:
    """
    Get full details of a specific email by its message:// URL.

    Returns headers, metadata, tags, and optionally the body.
    """
    eml_path, headers = _resolve_message(message_url)
    summary = summary_from_eml(eml_path, headers)

    result = {
        "message_url": summary.message_url,
        "message_id": summary.message_id,
        "subject": summary.subject,
        "from": summary.from_,
        "to": summary.to,
        "date": summary.date,
        "account": summary.account,
        "mailbox": summary.mailbox,
        "tags": summary.tags,
    }

    if include_body:
        result["body"] = get_message_body(eml_path, prefer_html=prefer_html)

    return result


@mcp.tool
def list_mailboxes() -> list[dict]:
    """
    List all mailboxes across all configured MailMate accounts.

    Returns account name, mailbox path, and message count for each.
    """
    return list_mailboxes_all()


@mcp.tool
def get_message_link(
    message_url: Annotated[
        str,
        "The message:// URL to open in MailMate",
    ],
) -> dict:
    """
    Get the message:// link for a message and open it in MailMate.

    The returned URL can be pasted into any app to jump directly
    to the message in MailMate (same as Edit > Copy as Link).
    """
    # Validate the message exists
    eml_path, headers = _resolve_message(message_url)
    mid = headers["message_id"]
    url = message_id_to_url(mid)

    # Open MailMate at this message
    _run_applescript(
        f'tell application "MailMate" to open location "{url}"'
    )
    _run_applescript('tell application "MailMate" to activate')

    return {
        "message_url": url,
        "message_id": mid,
        "subject": headers["subject"],
    }


@mcp.tool
def open_message_in_mailmate(
    message_url: Annotated[str, "The message:// URL to open"],
) -> dict:
    """
    Open a specific message in MailMate by its message:// URL.

    Activates MailMate and navigates to the message.
    """
    _run_applescript(
        f'tell application "MailMate" to open location "{message_url}"'
    )
    _run_applescript('tell application "MailMate" to activate')
    return {"opened": message_url}


@mcp.tool
def move_message(
    message_url: Annotated[str, "The message:// URL of the message to move"],
    target_mailbox_path: Annotated[
        str,
        "Filesystem path of the target .mailbox directory "
        "(e.g. from list_mailboxes 'path' field), "
        "or the mailbox name like 'Archive' or 'INBOX'.",
    ],
) -> dict:
    """
    Move a message to a different mailbox.

    MailMate performs the actual IMAP MOVE, so the message will be
    moved on the server as well.  Provide the 'path' from list_mailboxes
    or just a mailbox name — if a name is given, the first matching
    mailbox across all accounts is used.
    """
    eml_path, headers = _resolve_message(message_url)
    summary = summary_from_eml(eml_path, headers)

    # Resolve target mailbox directory
    target = Path(target_mailbox_path)
    if not target.exists():
        # Try to find by name
        all_mbs = list_mailboxes_all()
        matches = [
            m
            for m in all_mbs
            if target_mailbox_path.lower() in m["mailbox"].lower()
            or target_mailbox_path.lower() in m["path"].lower()
        ]
        if not matches:
            raise ValueError(
                f"No mailbox found matching {target_mailbox_path!r}. "
                "Use list_mailboxes to see available mailboxes."
            )
        target = Path(matches[0]["path"])

    target_messages = target / "Messages"
    if not target_messages.exists():
        raise ValueError(f"Target mailbox has no Messages directory: {target}")

    # Move the .eml file
    dest = target_messages / eml_path.name
    eml_path.rename(dest)

    return {
        "moved": True,
        "message_url": summary.message_url,
        "subject": summary.subject,
        "from_mailbox": summary.mailbox,
        "to_mailbox": str(target),
        "note": (
            "The file has been moved on disk. "
            "MailMate will sync the IMAP MOVE on next sync."
        ),
    }


@mcp.tool
def tag_message(
    message_url: Annotated[str, "The message:// URL of the message to tag"],
    add_tags: Annotated[
        list[str],
        "Tags to add (MailMate will sync as IMAP keywords)",
    ] = [],
    remove_tags: Annotated[list[str], "Tags to remove"] = [],
) -> dict:
    """
    Add or remove tags on a message.

    Tags are stored as IMAP Keywords headers and synced to the server.
    MailMate will pick up the change on next access.

    Use list_tags to see what tags are defined in MailMate.
    """
    if not add_tags and not remove_tags:
        raise ValueError("Provide at least one tag to add or remove.")

    eml_path, headers = _resolve_message(message_url)

    # Use AppleScript perform to apply tags — this is the safe way
    # that lets MailMate handle the IMAP sync properly.
    message_id = headers["message_id"]
    url = message_id_to_url(message_id)

    # Open the message first to make it current
    _run_applescript(
        f'tell application "MailMate" to open location "{url}"'
    )

    errors = []
    applied_add = []
    applied_remove = []

    for tag in add_tags:
        try:
            _run_applescript(
                f'tell application "MailMate" to perform {{"applyTag:", "{tag}"}}'
            )
            applied_add.append(tag)
        except RuntimeError as e:
            errors.append(f"add {tag!r}: {e}")

    for tag in remove_tags:
        try:
            _run_applescript(
                f'tell application "MailMate" to perform {{"removeTag:", "{tag}"}}'
            )
            applied_remove.append(tag)
        except RuntimeError as e:
            errors.append(f"remove {tag!r}: {e}")

    return {
        "message_url": url,
        "subject": headers["subject"],
        "tags_added": applied_add,
        "tags_removed": applied_remove,
        "errors": errors,
    }


@mcp.tool
def list_tags() -> list[str]:
    """
    List all tags defined in MailMate.

    Tags correspond to IMAP keywords and are configured in
    MailMate > Preferences > Tags.
    """
    import plistlib

    tags_plist = Path.home() / "Library" / "Application Support" / "MailMate" / "Tags.plist"
    if not tags_plist.exists():
        return []

    with tags_plist.open("rb") as f:
        data = plistlib.load(f)

    tags = data.get("tags", [])
    return [t.get("displayName", "") for t in tags if t.get("displayName")]


def main() -> None:
    """Entry point for the MCP server (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
