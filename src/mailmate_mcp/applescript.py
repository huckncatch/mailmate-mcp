"""AppleScript runner for MailMate automation."""

import subprocess
from typing import Any


def run(script: str) -> str:
    """Run an AppleScript and return stdout, raising on error."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def perform(*selectors_and_args: str) -> str:
    """Call MailMate's perform command with a list of selectors/args."""
    items = ", ".join(f'"{s}"' for s in selectors_and_args)
    return run(f'tell application "MailMate" to perform {{{items}}}')


def open_message(message_url: str) -> None:
    """Open a message:// URL in MailMate."""
    run(f'tell application "MailMate" to open location "{message_url}"')


def fetch_header(message_url: str, header: str) -> str:
    """
    Fetch a header from a message via AppleScript.

    MailMate's 'fetch' command can retrieve any header (or 'body').
    The message is addressed by its position in the app's message list
    after being opened via URL, but here we use a direct by-url approach.
    """
    script = f"""
tell application "MailMate"
    open location "{message_url}"
    delay 0.5
    set msg to message 1
    fetch msg header "{header}"
end tell
"""
    return run(script)
