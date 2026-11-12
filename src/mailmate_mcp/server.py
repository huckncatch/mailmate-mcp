"""MailMate MCP server — search, read, move, tag, and link emails."""

from __future__ import annotations

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
    tag_keywords,
    url_to_message_id,
)
from .jmap import update_keywords

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
        "Optional account filter (substring match on email address, e.g. 'name@fastmail.com', or on the account name from list_mailboxes)",
    ] = None,
    mailbox: Annotated[
        str | None,
        "Optional mailbox filter (substring match, e.g. 'INBOX', 'Archive')",
    ] = None,
    include_body: Annotated[
        bool,
        "Also search inside message body text (slower; narrow with since/before, mailbox, or account)",
    ] = False,
    since: Annotated[
        str | None,
        "Only messages dated on or after this ISO date, e.g. '2026-10-01' (local time)",
    ] = None,
    before: Annotated[
        str | None,
        "Only messages dated before this ISO date, e.g. '2026-11-01' (local time)",
    ] = None,
    max_results: Annotated[int, "Maximum number of results to return"] = 20,
) -> list[dict]:
    """
    Search MailMate messages by query string, tag, and/or date range.

    All filters are optional — omit query to list all messages with a given tag
    or in a date range. Use list_tags to see available tags.

    Results are sorted newest first. Returns a list of matching messages with
    subject, from, to, date, mailbox, tags, and message_url for each.
    """
    results = search_messages(
        query=query,
        search_body=include_body,
        account=account,
        mailbox=mailbox,
        tag=tag,
        since=since,
        before=before,
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
        "The message:// URL of the message",
    ],
) -> dict:
    """
    Get the message:// link for a message. Does not open MailMate.

    The returned URL can be pasted into any app to jump directly
    to the message in MailMate (same as Edit > Copy as Link).
    """
    # Validate the message exists
    eml_path, headers = _resolve_message(message_url)
    mid = headers["message_id"]
    url = message_id_to_url(mid)

    return {
        "message_url": url,
        "message_id": mid,
        "subject": headers["subject"],
    }


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
        "Tags to add (display names from list_tags)",
    ] = [],
    remove_tags: Annotated[list[str], "Tags to remove (display names from list_tags)"] = [],
) -> dict:
    """
    Add or remove tags on one Fastmail message.

    Sets the tags' IMAP keywords on the Fastmail server over JMAP. MailMate
    shows the change after its next sync. Does not open MailMate. Messages
    in other accounts cannot be tagged; move them to Fastmail first.
    """
    if not add_tags and not remove_tags:
        raise ValueError("Provide at least one tag to add or remove.")

    eml_path, headers = _resolve_message(message_url)
    summary = summary_from_eml(eml_path, headers)
    if "imap.fastmail.com" not in summary.account:
        raise ValueError(
            f"Only Fastmail messages can be tagged; this one is in "
            f"{summary.account}. Move it to Fastmail first."
        )

    # Display name (any case) -> (display name, IMAP keyword)
    defined = {name.lower(): (name, kw) for name, kw in tag_keywords().items()}
    unknown = [t for t in add_tags + remove_tags if t.lower() not in defined]
    if unknown:
        raise ValueError(f"Unknown tags {unknown}. Use list_tags to see defined tags.")
    overlap = {t.lower() for t in add_tags} & {t.lower() for t in remove_tags}
    if overlap:
        raise ValueError(f"Tags in both add_tags and remove_tags: {sorted(overlap)}")

    before, after = update_keywords(
        headers["message_id"],
        add=[defined[t.lower()][1] for t in add_tags],
        remove=[defined[t.lower()][1] for t in remove_tags],
    )

    # Keyword (lowercase) -> display name; Fastmail-only keywords are dropped
    by_keyword = {kw.lower(): name for name, kw in defined.values()}

    def as_tags(keywords: list[str]) -> list[str]:
        return [by_keyword[k] for k in keywords if k in by_keyword]

    return {
        "message_url": summary.message_url,
        "subject": summary.subject,
        "tags_before": as_tags(before),
        "tags_after": as_tags(after),
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
