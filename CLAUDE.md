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

**`src/mailmate_mcp/mailstore.py`** — filesystem/parsing layer only. No MCP or AppleScript dependencies. Walks MailMate's on-disk store (`~/Library/Application Support/MailMate/Messages.noindex/IMAP/`), parses `.eml` files using `email.policy.compat32` (tolerates malformed real-world mail), and returns `MessageSummary` dataclasses. All search is a linear scan of `.eml` files.

**`src/mailmate_mcp/server.py`** — MCP boundary. Imports from `mailstore`, defines all tools with `@mcp.tool`, and contains the only AppleScript calls (`osascript`). AppleScript is used for: navigating to messages (`open location`), applying/removing tags (`perform {"applyTag:", tag}`), and activating MailMate.

## Key Conventions

**Message identity**: The primary identifier throughout is the `message://` URL, derived from the `Message-ID` header. Format: `message://%3C<url-encoded-id>%3E` where `<`→`%3C`, `>`→`%3E`, `@` is left unencoded. `url_to_message_id` and `message_id_to_url` in `mailstore.py` handle conversion.

**Adding a new tool**: Define it in `server.py` with `@mcp.tool`. The `fastmcp` decorator uses Python type annotations and the docstring to generate the MCP schema — parameter descriptions in `Annotated[type, "description"]` are part of the schema. Add any needed filesystem logic to `mailstore.py` first, then import it.

**Tags**: Stored as RFC 2822 `Keywords` header in `.eml` files. Reading tags is done in `mailstore.py` (`parse_tags`). Writing/removing tags goes through AppleScript in `server.py` because MailMate needs to handle the IMAP sync.

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
