# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the MCP server (stdio transport, for testing)
uv run mailmate-mcp

# Run the server directly during development
uv run python -m mailmate_mcp.server

# Inspect installed package entry point
uv run which mailmate-mcp
```

There is no test suite yet. Manual testing is done by registering the server in `~/Library/Application Support/Claude/claude_desktop_config.json` and restarting Claude Desktop.

## Architecture

The server has three source files with a strict layering:

**`src/mailmate_mcp/mailstore.py`** — filesystem/parsing layer only. No MCP or AppleScript dependencies. Walks MailMate's on-disk store (`~/Library/Application Support/MailMate/Messages.noindex/IMAP/`), parses `.eml` files using `email.policy.compat32` (tolerates malformed real-world mail), and returns `MessageSummary` dataclasses. Search runs on an in-memory header index, built in a background thread at first use and refreshed with new files on each search. It searches `_by_path`, which has one entry per `.eml` file, because a message can sit in several mailboxes (Gmail Sent Mail plus a label); the Message-ID lookup `_index` keeps only one copy. Search applies the account, mailbox, tag and date filters, sorts newest first, and only then parses bodies from disk when `search_body` is set.

**`src/mailmate_mcp/jmap.py`** — Fastmail JMAP client, network only (stdlib `urllib`). `update_keywords` finds the one message with a given Message-ID (`Email/query` header filter) and patches its keywords (`Email/set`). The token comes from `FASTMAIL_API_TOKEN`.

**`src/mailmate_mcp/server.py`** — MCP boundary. Imports from `mailstore` and `jmap`, defines all tools with `@mcp.tool`. No tool automates MailMate's interface: its AppleScript acts only on what is on screen (`applyTag:` tags the selected message, and MailMate never replies to `open location`), and a run of such calls froze the Mac.

## Key Conventions

**Message identity**: The primary identifier throughout is the `message://` URL, derived from the `Message-ID` header. Format: `message://%3C<url-encoded-id>%3E` where `<`→`%3C`, `>`→`%3E`, `@` is left unencoded. `url_to_message_id` and `message_id_to_url` in `mailstore.py` handle conversion.

**Adding a new tool**: Define it in `server.py` with `@mcp.tool`. The `fastmcp` decorator uses Python type annotations and the docstring to generate the MCP schema — parameter descriptions in `Annotated[type, "description"]` are part of the schema. Add any needed filesystem logic to `mailstore.py` first, then import it. When you add, rename or remove a tool, update the tools list in the vault note `mailmate-expert-instructions` and paste the note into the Claude Desktop "Mailmate Expert" project. `~/config/brain/drift-checks.d/mailmate-expert.sh` reports a mismatch at the next Claude Code session start.

**Tags**: MailMate stores tags as IMAP keywords in its header database (`Database.noindex/Headers/#flags.cache`), not in the `.eml` `Keywords` header. `build_message_tag_index` in `mailstore.py` reads it (about 0.1 s) and keeps only keywords declared in `Tags.plist`. `get_message` and each search read it fresh, because tags change after the index is built. The `.eml` `Keywords` header (`parse_tags`) is used only when the database gives no tags at all. `tag_message` sets keywords on the Fastmail server over JMAP (`jmap.py`), for Fastmail messages only; it refuses other accounts (move the message to Fastmail first). It maps display names to keywords through `tag_keywords()` (`serverKeyword` or `displayName` in `Tags.plist`) and sends them lowercase: Fastmail returns keywords lowercased, and MailMate still matches its mixed-case tags (`toDo`). MailMate shows the change after its next sync.

**move_message**: Renames the `.eml` file on disk. MailMate detects the move and syncs the IMAP MOVE on next access. The `list_mailboxes` `path` field is the `.mailbox` directory (not the `Messages/` subdirectory inside it).

## MCP Registration

Both clients run `~/config/brain/bin/mailmate-mcp.sh`. It reads `FASTMAIL_API_TOKEN` from `~/.config/zsh/mcp-secrets.zsh` (rendered from 1Password) and runs `.venv/bin/mailmate-mcp`. Never put the token in a client config.

### Claude Code (global, all projects)

```bash
claude mcp add --scope user mailmate /Users/soob/config/brain/bin/mailmate-mcp.sh
```

### Claude Desktop

In `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mailmate": {
      "command": "/Users/soob/config/brain/bin/mailmate-mcp.sh",
      "args": []
    }
  }
}
```

The package is an editable install, but each server process keeps the code it loaded at start. `claude mcp remove`/`add` and restarting Claude Desktop do not restart a running Claude Code session's server: that session keeps calling the old code until it reconnects `mailmate` or a new session starts. Check with `ps -axo pid,lstart,command | grep '[m]ailmate-mcp'`. To test the registered path without a client, send MCP JSON-RPC (`initialize`, then `tools/call`) to the launcher over stdio.

## Cross-repo documentation (Obsidian)

This project's note is the Home vault note whose `repo` field is this
repository's path (`repo: ~/Developer/mailmate-mcp`). Find it by searching the vault
for that line (vault location and rules: `~/config/brain/`). Update it when
the tool list, the stack, or project status changes.

Decisions, research, and plans worth cross-project visibility go to the vault
through the `vault` skill (log flow).
