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

The server has two source files with a strict layering:

**`src/mailmate_mcp/mailstore.py`** — filesystem/parsing layer only. No MCP or AppleScript dependencies. Walks MailMate's on-disk store (`~/Library/Application Support/MailMate/Messages.noindex/IMAP/`), parses `.eml` files using `email.policy.compat32` (tolerates malformed real-world mail), and returns `MessageSummary` dataclasses. Search runs on an in-memory header index, built in a background thread at first use and refreshed with new files on each search. It searches `_by_path`, which has one entry per `.eml` file, because a message can sit in several mailboxes (Gmail Sent Mail plus a label); the Message-ID lookup `_index` keeps only one copy. Search applies the account, mailbox, tag and date filters, sorts newest first, and only then parses bodies from disk when `search_body` is set.

**`src/mailmate_mcp/server.py`** — MCP boundary. Imports from `mailstore`, defines all tools with `@mcp.tool`, and contains the only AppleScript calls (`osascript`). AppleScript is used for: navigating to messages (`open location`), applying/removing tags (`perform {"applyTag:", tag}`), and activating MailMate. Each call times out after `APPLESCRIPT_TIMEOUT` seconds and raises an error, so a stuck MailMate does not hang the tool. A command that timed out can still run later.

## Key Conventions

**Message identity**: The primary identifier throughout is the `message://` URL, derived from the `Message-ID` header. Format: `message://%3C<url-encoded-id>%3E` where `<`→`%3C`, `>`→`%3E`, `@` is left unencoded. `url_to_message_id` and `message_id_to_url` in `mailstore.py` handle conversion.

**Adding a new tool**: Define it in `server.py` with `@mcp.tool`. The `fastmcp` decorator uses Python type annotations and the docstring to generate the MCP schema — parameter descriptions in `Annotated[type, "description"]` are part of the schema. Add any needed filesystem logic to `mailstore.py` first, then import it. When you add, rename or remove a tool, update the tools list in the vault note `mailmate-expert-instructions` and paste the note into the Claude Desktop "Mailmate Expert" project. `~/config/brain/drift-checks.d/mailmate-expert.sh` reports a mismatch at the next Claude Code session start.

**Tags**: MailMate stores tags as IMAP keywords in its header database (`Database.noindex/Headers/#flags.cache`), not in the `.eml` `Keywords` header. `build_message_tag_index` in `mailstore.py` reads it (about 0.1 s) and keeps only keywords declared in `Tags.plist`. `get_message` and each search read it fresh, because tags change after the index is built. The `.eml` `Keywords` header (`parse_tags`) is used only when the database gives no tags at all. Writing/removing tags goes through AppleScript in `server.py` because MailMate needs to handle the IMAP sync.

**move_message**: Renames the `.eml` file on disk. MailMate detects the move and syncs the IMAP MOVE on next access. The `list_mailboxes` `path` field is the `.mailbox` directory (not the `Messages/` subdirectory inside it).

## MCP Registration

### Claude Code (global, all projects)

Add to `~/.claude.json` under `"mcpServers"`:

```json
"mailmate": {
  "type": "stdio",
  "command": "/Users/soob/Developer/mailmate-mcp/.venv/bin/mailmate-mcp",
  "args": [],
  "env": {}
}
```

Or via CLI:

```bash
claude mcp add --scope user mailmate /Users/soob/Developer/mailmate-mcp/.venv/bin/mailmate-mcp
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mailmate": {
      "command": "/Users/soob/Developer/mailmate-mcp/.venv/bin/mailmate-mcp"
    }
  }
}
```

After changing server code, restart Claude Desktop to reload.

## Cross-repo documentation (Obsidian)

This project's note is the Home vault note whose `repo` field is this
repository's path (`repo: ~/Developer/mailmate-mcp`). Find it by searching the vault
for that line (vault location and rules: `~/config/brain/`). Update it when
the tool list, the stack, or project status changes.

Decisions, research, and plans worth cross-project visibility go to the vault
through the `vault` skill (log flow).
