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
import email.utils
import plistlib
import re
import struct
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, unquote

MAILMATE_ROOT = (
    Path.home()
    / "Library"
    / "ApplicationSupport"
    / "MailMate"
    / "Messages.noindex"
    / "IMAP"
)

MAILMATE_DB = (
    Path.home()
    / "Library"
    / "ApplicationSupport"
    / "MailMate"
    / "Database.noindex"
    / "Headers"
)

# ---------------------------------------------------------------------------
# MailMate binary database helpers (for IMAP keyword/tag data)
# ---------------------------------------------------------------------------

def _read_mailmate_cache(cache_name: str) -> dict[int, str]:
    """Parse a MailMate Database.noindex/Headers/*.cache + *.offsets pair.

    Each record in the .offsets file is 12 bytes: three little-endian uint32s
    (msg_id, start_offset, end_offset_inclusive) indexing into the .cache file.
    The .cache file is a newline-delimited string store with deduplication —
    many msg_ids may share the same offset range.

    Returns a dict mapping MailMate internal msg_id → string value.
    """
    cache_path = MAILMATE_DB / f"{cache_name}.cache"
    offsets_path = MAILMATE_DB / f"{cache_name}.offsets"
    if not cache_path.exists() or not offsets_path.exists():
        return {}
    try:
        cache_data = cache_path.read_bytes()
        offsets_data = offsets_path.read_bytes()
    except OSError:
        return {}

    result: dict[int, str] = {}
    entry_size = 12  # 3 × uint32_le
    for i in range(len(offsets_data) // entry_size):
        off = i * entry_size
        msg_id, start, end_inc = struct.unpack_from("<III", offsets_data, off)
        value = cache_data[start : end_inc + 1].rstrip(b"\n").decode("utf-8", errors="replace")
        result[msg_id] = value
    return result


def tag_keywords() -> dict[str, str]:
    """Return {displayName: IMAP keyword} for all user-defined MailMate tags.

    Each tag in Tags.plist has a displayName and an optional serverKeyword.
    When serverKeyword is absent, the IMAP keyword equals the displayName.
    """
    tags_plist = Path.home() / "Library" / "ApplicationSupport" / "MailMate" / "Tags.plist"
    if not tags_plist.exists():
        return {}
    try:
        with tags_plist.open("rb") as f:
            data = plistlib.load(f)
        result: dict[str, str] = {}
        for tag in data.get("tags", []):
            name = tag.get("displayName")
            kw = tag.get("serverKeyword") or name
            if name and kw:
                result[name] = kw
        return result
    except Exception:
        return {}


def _load_tag_keywords() -> set[str]:
    """Return the IMAP keyword strings for all user-defined MailMate tags."""
    return set(tag_keywords().values())


def build_message_tag_index() -> dict[str, list[str]]:
    """Read MailMate's binary header database to map Message-ID → user tags.

    MailMate stores IMAP flags (system flags like \\Seen and user keywords
    like 'newsletter') in Database.noindex/Headers/#flags.cache, not in
    the Keywords header of the .eml files. This function decodes that database
    and returns only the keywords that correspond to user-defined tags
    (as declared in Tags.plist).
    """
    valid_keywords = _load_tag_keywords()
    if not valid_keywords:
        return {}

    flags_by_id = _read_mailmate_cache("#flags")
    msgid_by_id = _read_mailmate_cache("message-id")

    result: dict[str, list[str]] = {}
    for msg_id, flags_str in flags_by_id.items():
        message_id = msgid_by_id.get(msg_id)
        if not message_id:
            continue
        tags = [kw for kw in flags_str.split() if kw in valid_keywords]
        if tags:
            result[message_id] = tags
    return result


# ---------------------------------------------------------------------------
# In-memory message-ID → MessageSummary index
# ---------------------------------------------------------------------------

_index: dict[str, "MessageSummary"] = {}   # message_id (with angle brackets) → summary
# Every .eml file, including copies of one message in several mailboxes
# (e.g. Gmail Sent Mail + a label). _index keeps only one copy per Message-ID.
_by_path: dict[Path, "MessageSummary"] = {}
_index_lock = threading.Lock()
_index_started = threading.Event()          # prevents double-launch
_index_ready = threading.Event()            # set when initial scan is complete
_last_scan_time: float = 0.0

_HEADER_NAMES = {b"message-id", b"subject", b"from", b"to", b"cc", b"date", b"keywords"}


def _read_headers_fast(path: Path) -> dict[str, str]:
    """Read only the header block of an .eml, returning a flat dict of header → value.

    Handles RFC 2822 folded headers. Does not read the message body.
    """
    raw: dict[str, str] = {}
    current_key: str = ""
    try:
        with path.open("rb") as f:
            for line in f:
                if line in (b"\r\n", b"\n"):
                    break  # blank line = end of headers
                if line[0:1] in (b" ", b"\t"):
                    # Folded continuation — append to current header
                    if current_key in raw:
                        raw[current_key] += " " + line.strip().decode("utf-8", errors="replace")
                elif b":" in line:
                    colon = line.index(b":")
                    current_key = line[:colon].strip().lower().decode("ascii", errors="replace")
                    raw[current_key] = line[colon + 1:].strip().decode("utf-8", errors="replace")
    except Exception:
        pass
    # Decode RFC 2047 encoded-words (e.g. =?UTF-8?B?...?=) for the fields we care about
    return {
        "message_id": decode_header_value(raw.get("message-id", "")),
        "subject":    decode_header_value(raw.get("subject", "")),
        "from":       decode_header_value(raw.get("from", "")),
        "to":         decode_header_value(raw.get("to", "")),
        "cc":         decode_header_value(raw.get("cc", "")),
        "date":       raw.get("date", ""),
        "keywords":   raw.get("keywords", ""),
    }


def _summary_from_fast_headers(eml_path: Path) -> "MessageSummary | None":
    """Build a MessageSummary from the fast header read. Returns None on failure."""
    try:
        headers = _read_headers_fast(eml_path)
        mid = headers["message_id"].strip()
        if not mid:
            return None
        account, mailbox = _account_mailbox_from_path(eml_path)
        return MessageSummary(
            path=eml_path,
            uid=eml_path.stem,
            account=account,
            mailbox=mailbox,
            message_id=mid,
            subject=headers["subject"],
            from_=headers["from"],
            to=headers["to"],
            date=headers["date"],
            tags=parse_tags(headers["keywords"]),
            timestamp=parse_date(headers["date"]),
        )
    except Exception:
        return None


def _build_index() -> None:
    global _last_scan_time
    new_index: dict[str, MessageSummary] = {}
    new_by_path: dict[Path, MessageSummary] = {}
    scan_start = time.time()
    for _, _, eml_path in iter_messages():
        summary = _summary_from_fast_headers(eml_path)
        if summary:
            new_index[summary.message_id] = summary
            new_by_path[eml_path] = summary

    # Augment with tag data from MailMate's binary header database.
    # Tags are stored as IMAP keywords in Database.noindex/Headers/#flags.cache,
    # not in the Keywords header of .eml files.
    tag_index = build_message_tag_index()
    for summary in new_by_path.values():
        tags = tag_index.get(summary.message_id)
        if tags:
            summary.tags = tags

    with _index_lock:
        _index.update(new_index)
        _by_path.update(new_by_path)
        _last_scan_time = scan_start
    _index_ready.set()


def start_index_build() -> None:
    """Kick off the background index scan (non-blocking). Safe to call multiple times."""
    if not _index_started.is_set():
        _index_started.set()
        threading.Thread(target=_build_index, daemon=True, name="mailmate-index").start()


def _ensure_index() -> None:
    """Start the index build if needed and wait for it to be ready."""
    start_index_build()
    _index_ready.wait()


def _refresh_new_messages() -> None:
    """On a cache miss: scan for .eml files newer than the last scan and add them."""
    global _last_scan_time
    new_scan_time = time.time()
    for _, _, eml_path in iter_messages():
        try:
            if eml_path.stat().st_mtime > _last_scan_time:
                summary = _summary_from_fast_headers(eml_path)
                if summary:
                    with _index_lock:
                        _index[summary.message_id] = summary
                        _by_path[eml_path] = summary
        except Exception:
            continue
    with _index_lock:
        _last_scan_time = new_scan_time


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
    timestamp: float | None = None  # parsed Date header (Unix time); None if unparseable

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


def parse_date(date_header: str | None) -> float | None:
    """Parse an RFC 2822 Date header to Unix time. Returns None if unparseable."""
    if not date_header:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_date_bound(value: str) -> float:
    """Parse an ISO date or datetime (e.g. '2026-10-01') to Unix time.

    A value without a time zone is read as local time.
    """
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(
            f"Invalid date {value!r}. Use ISO format, e.g. '2026-10-01'."
        ) from None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


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


def account_matches(account_filter: str, account_name: str) -> bool:
    """
    Match an account filter against an account directory name.

    MailMate names account directories <login>@<imap-host>, with any "@" in
    the login URL-encoded: "huckncatch%40fastmail.com@imap.fastmail.com",
    "huckncatch@imap.gmail.com". The filter is a case-insensitive substring
    of the raw name, the decoded name, or the email address
    ("huckncatch@fastmail.com", "huckncatch@gmail.com").
    """
    decoded = unquote(account_name)
    login, _, host = decoded.rpartition("@")
    if "@" in login:
        address = login
    else:
        domain = host[len("imap."):] if host.startswith("imap.") else host
        address = f"{login}@{domain}"
    needle = account_filter.lower()
    return any(needle in s.lower() for s in (account_name, decoded, address))


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
        if account and not account_matches(account, acct_name):
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
    """Find the .eml file for a given Message-ID using the in-memory index."""
    target = message_id.strip()
    if not (target.startswith("<") and target.endswith(">")):
        target = f"<{target}>"

    _ensure_index()

    with _index_lock:
        summary = _index.get(target)

    if summary is not None and summary.path.exists():
        return summary.path

    # Cache miss — pick up any mail that arrived since the last scan
    _refresh_new_messages()

    with _index_lock:
        summary = _index.get(target)

    return summary.path if (summary is not None and summary.path.exists()) else None


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


def summary_from_eml(eml_path: Path, headers: dict | None = None) -> MessageSummary:
    """Build a MessageSummary from an .eml file path."""
    if headers is None:
        headers = parse_eml(eml_path)
    account, mailbox = _account_mailbox_from_path(eml_path)
    tag_index = build_message_tag_index()
    if tag_index:
        tags = tag_index.get(headers["message_id"], [])
    else:
        tags = parse_tags(headers["keywords"])
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
        tags=tags,
        timestamp=parse_date(headers["date"]),
    )


def _body_matches(eml_path: Path, query_lower: str) -> bool:
    """Return True if the Cc header or a text/plain part contains query_lower."""
    with eml_path.open("rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.compat32)
    if query_lower in decode_header_value(msg.get("Cc", "")).lower():
        return True
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload and query_lower in payload.decode(
                part.get_content_charset() or "utf-8", errors="replace"
            ).lower():
                return True
    return False


def search_messages(
    query: str = "",
    search_body: bool = False,
    account: str | None = None,
    mailbox: str | None = None,
    tag: str | None = None,
    since: str | None = None,
    before: str | None = None,
    max_results: int = 50,
) -> list[MessageSummary]:
    """
    Search messages by query string, tag, and/or date range, newest first.

    All filters run on the in-memory index. With search_body, only the
    messages that pass the other filters and miss on subject/from/to are
    parsed from disk, newest first, until max_results matches are found.
    since is inclusive and before is exclusive (ISO dates, local time).
    Messages with an unparseable Date header are excluded by a date filter
    and sorted last otherwise.
    """
    query_lower = query.lower()
    tag_lower = tag.lower() if tag else None
    since_ts = parse_date_bound(since) if since else None
    before_ts = parse_date_bound(before) if before else None

    _ensure_index()
    _refresh_new_messages()
    # Tags change after the index is built, so read them fresh each search.
    tag_index = build_message_tag_index()
    with _index_lock:
        candidates = list(_by_path.values())
    if tag_index:
        for summary in candidates:
            summary.tags = tag_index.get(summary.message_id, [])

    filtered = []
    for summary in candidates:
        if account and not account_matches(account, summary.account):
            continue
        if mailbox and mailbox.lower() not in summary.mailbox.lower():
            continue
        if tag_lower and not any(tag_lower == t.lower() for t in summary.tags):
            continue
        if since_ts is not None or before_ts is not None:
            ts = summary.timestamp
            if ts is None:
                continue
            if since_ts is not None and ts < since_ts:
                continue
            if before_ts is not None and ts >= before_ts:
                continue
        filtered.append(summary)

    filtered.sort(
        key=lambda s: s.timestamp if s.timestamp is not None else float("-inf"),
        reverse=True,
    )

    results = []
    for summary in filtered:
        if len(results) >= max_results:
            break
        if not summary.path.exists():
            continue  # moved or deleted since it was indexed
        if query_lower:
            haystack = (
                summary.subject + " " + summary.from_ + " " + summary.to
            ).lower()
            if query_lower not in haystack:
                if not search_body:
                    continue
                try:
                    if not _body_matches(summary.path, query_lower):
                        continue
                except Exception:
                    continue
        results.append(summary)
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
