"""Direct access to MailMate's on-disk message store.

MailMate stores messages as standard RFC 2822 .eml files under:
  ~/Library/Application Support/MailMate/Messages.noindex/IMAP/
    <account-dir>/
      <mailbox>.mailbox/
        Messages/
          <uid>.eml
"""

import email
import email.header
import email.policy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, unquote

MAILMATE_ROOT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "MailMate"
    / "Messages.noindex"
    / "IMAP"
)


@dataclass
class MessageSummary:
    path: Path
    uid: str           # numeric UID filename stem (e.g. "354792")
    account: str       # e.g. "huckncatch@imap.gmail.com"
    mailbox: str       # e.g. "INBOX"
    message_id: str    # raw Message-ID value, with angle brackets
    subject: str
    from_: str
    to: str
    date: str
    tags: list[str] = field(default_factory=list)

    @property
    def message_url(self) -> str:
        """Return a message:// URL for this message."""
        return message_id_to_url(self.message_id)


def decode_header_value(value: str | None) -> str:
    """Decode a potentially RFC 2047-encoded header value."""
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def parse_tags(keywords_header: str | None) -> list[str]:
    """Parse the RFC 2822 Keywords header into a list of tags."""
    if not keywords_header:
        return []
    return [t.strip() for t in keywords_header.split(",") if t.strip()]


def parse_eml(path: Path) -> dict[str, str]:
    """Parse an .eml file, returning a dict of key headers."""
    with path.open("rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.compat32)

    return {
        "message_id": decode_header_value(msg.get("Message-ID", "")),
        "subject": decode_header_value(msg.get("Subject", "")),
        "from": decode_header_value(msg.get("From", "")),
        "to": decode_header_value(msg.get("To", "")),
        "date": decode_header_value(msg.get("Date", "")),
        "keywords": decode_header_value(msg.get("Keywords", "")),
        "cc": decode_header_value(msg.get("Cc", "")),
    }


def iter_accounts() -> Iterator[Path]:
    """Yield account directories."""
    if not MAILMATE_ROOT.exists():
        return
    for p in MAILMATE_ROOT.iterdir():
        if p.is_dir():
            yield p


def _mailbox_name(mailbox_dir: Path) -> str:
    """Convert a .mailbox path to a human-readable mailbox name."""
    # Strip account prefix and .mailbox suffixes, join with /
    parts = []
    for part in mailbox_dir.parts:
        if part.endswith(".mailbox"):
            parts.append(part[: -len(".mailbox")])
    return "/".join(parts) if parts else mailbox_dir.name


def iter_mailboxes(account_dir: Path) -> Iterator[tuple[str, Path]]:
    """Yield (mailbox_name, messages_dir) for an account."""
    for mailbox_dir in account_dir.rglob("*.mailbox"):
        messages_dir = mailbox_dir / "Messages"
        if messages_dir.is_dir():
            name = _mailbox_name(mailbox_dir)
            yield name, messages_dir


def iter_messages(
    account: str | None = None,
    mailbox: str | None = None,
) -> Iterator[tuple[str, str, Path]]:
    """
    Yield (account_name, mailbox_name, eml_path) tuples.

    Optionally filter by account or mailbox substring.
    """
    for account_dir in iter_accounts():
        acct_name = account_dir.name
        if account and account.lower() not in acct_name.lower():
            continue
        for mb_name, messages_dir in iter_mailboxes(account_dir):
            if mailbox and mailbox.lower() not in mb_name.lower():
                continue
            for eml_path in messages_dir.glob("*.eml"):
                yield acct_name, mb_name, eml_path


def message_id_to_url(message_id: str) -> str:
    """
    Convert a Message-ID header value to a message:// URL.

    MailMate's format: message://%3C<url-encoded-id-without-angles>%3E
    """
    mid = message_id.strip()
    # Strip angle brackets if present
    if mid.startswith("<") and mid.endswith(">"):
        mid = mid[1:-1]
    # MailMate keeps @ unencoded in its URLs (matches "Copy as Link" output)
    encoded = quote(mid, safe="@")
    return f"message://%3C{encoded}%3E"


def url_to_message_id(url: str) -> str:
    """Convert a message:// URL back to a Message-ID with angle brackets."""
    # Remove the message:// prefix and decode
    stripped = url.removeprefix("message://")
    decoded = unquote(stripped)
    # decoded looks like <message-id>
    if not decoded.startswith("<"):
        decoded = f"<{decoded}>"
    return decoded


def find_eml_by_message_id(message_id: str) -> Path | None:
    """
    Find the .eml file for a given Message-ID by scanning the store.

    This is a linear scan; fast enough for personal mailboxes.
    """
    target = message_id.strip()
    if not (target.startswith("<") and target.endswith(">")):
        target = f"<{target}>"

    for _, _, eml_path in iter_messages():
        try:
            headers = parse_eml(eml_path)
            if headers["message_id"].strip() == target:
                return eml_path
        except Exception:
            continue
    return None


def _account_mailbox_from_path(eml_path: Path) -> tuple[str, str]:
    """Extract (account, mailbox) from an eml file's path."""
    # Path: .../IMAP/<account>/<some>/<mailbox>.mailbox/Messages/<uid>.eml
    try:
        rel = eml_path.relative_to(MAILMATE_ROOT)
        parts = rel.parts
        account = parts[0]
        # Everything between account and Messages/<uid>.eml
        mailbox_parts = []
        for part in parts[1:]:
            if part == "Messages":
                break
            if part.endswith(".mailbox"):
                mailbox_parts.append(part[: -len(".mailbox")])
        return account, "/".join(mailbox_parts)
    except Exception:
        return "", ""


def summary_from_eml(eml_path: Path) -> MessageSummary:
    """Build a MessageSummary from an .eml file path."""
    headers = parse_eml(eml_path)
    account, mailbox = _account_mailbox_from_path(eml_path)
    return MessageSummary(
        path=eml_path,
        uid=eml_path.stem,
        account=account,
        mailbox=mailbox,
        message_id=headers["message_id"],
        subject=headers["subject"],
        from_=headers["from"],
        to=headers["to"],
        date=headers["date"],
        tags=parse_tags(headers["keywords"]),
    )


def search_messages(
    query: str,
    search_body: bool = False,
    account: str | None = None,
    mailbox: str | None = None,
    max_results: int = 50,
) -> list[MessageSummary]:
    """
    Search messages by query string.

    Searches subject, from, to headers (and optionally body).
    Returns up to max_results matches.
    """
    query_lower = query.lower()
    results = []

    for acct_name, mb_name, eml_path in iter_messages(account, mailbox):
        if len(results) >= max_results:
            break
        try:
            headers = parse_eml(eml_path)
            haystack = (
                headers["subject"]
                + " "
                + headers["from"]
                + " "
                + headers["to"]
                + " "
                + headers["cc"]
            ).lower()

            if search_body:
                # Read body text — only plain text parts
                with eml_path.open("rb") as f:
                    msg = email.message_from_binary_file(
                        f, policy=email.policy.compat32
                    )
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            haystack += " " + payload.decode(
                                part.get_content_charset() or "utf-8",
                                errors="replace",
                            ).lower()

            if query_lower in haystack:
                results.append(
                    MessageSummary(
                        path=eml_path,
                        uid=eml_path.stem,
                        account=acct_name,
                        mailbox=mb_name,
                        message_id=headers["message_id"],
                        subject=headers["subject"],
                        from_=headers["from"],
                        to=headers["to"],
                        date=headers["date"],
                        tags=parse_tags(headers["keywords"]),
                    )
                )
        except Exception:
            continue

    return results


def get_message_body(eml_path: Path, prefer_html: bool = False) -> str:
    """Extract the plain text or HTML body from an .eml file."""
    with eml_path.open("rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.compat32)

    preferred = "text/html" if prefer_html else "text/plain"
    fallback = "text/plain" if prefer_html else "text/html"

    for content_type in (preferred, fallback):
        for part in msg.walk():
            if part.get_content_type() == content_type:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")

    return ""


def list_mailboxes_all() -> list[dict[str, str]]:
    """Return a list of all mailboxes across all accounts."""
    mailboxes = []
    for account_dir in iter_accounts():
        acct_name = account_dir.name
        for mb_name, messages_dir in iter_mailboxes(account_dir):
            count = sum(1 for _ in messages_dir.glob("*.eml"))
            mailboxes.append(
                {
                    "account": acct_name,
                    "mailbox": mb_name,
                    "message_count": str(count),
                    "path": str(messages_dir.parent),
                }
            )
    return mailboxes
