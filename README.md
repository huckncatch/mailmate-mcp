# mailmate-mcp

An [MCP](https://modelcontextprotocol.io/) server that gives Claude (and other MCP clients) direct access to [MailMate](https://freron.com/), the macOS email client. Search, read, move, tag, and link emails without leaving your conversation.

## Requirements

- macOS (MailMate is Mac-only)
- [MailMate](https://freron.com/) installed and configured with at least one IMAP account
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
git clone https://github.com/huckncatch/mailmate-mcp
cd mailmate-mcp
uv sync
```

## MCP Registration

### Claude Code (global, all projects)

```bash
claude mcp add --scope user mailmate /path/to/mailmate-mcp/.venv/bin/mailmate-mcp
```

Or add manually to `~/.claude.json` under `"mcpServers"`:

```json
"mailmate": {
  "type": "stdio",
  "command": "/path/to/mailmate-mcp/.venv/bin/mailmate-mcp",
  "args": [],
  "env": {}
}
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mailmate": {
      "command": "/path/to/mailmate-mcp/.venv/bin/mailmate-mcp"
    }
  }
}
```

Restart Claude Desktop after any change to server code or config.

## Tools

| Tool | Description |
|------|-------------|
| `search_messages` | Search by query string, tag, account, or mailbox. Supports body search. |
| `get_message` | Fetch full details and body for a message by its `message://` URL. |
| `list_mailboxes` | List all mailboxes across all configured accounts with message counts. |
| `list_tags` | List all tags defined in MailMate Preferences → Tags. |
| `get_message_link` | Get the `message://` link for a message and open it in MailMate. |
| `open_message_in_mailmate` | Navigate MailMate to a specific message. |
| `move_message` | Move a message to a different mailbox (syncs via IMAP). |
| `tag_message` | Add or remove tags on a message (syncs as IMAP keywords). |

Messages are identified by `message://` URLs derived from the RFC 2822 `Message-ID` header, e.g. `message://%3Cabc123@example.com%3E`. Use `search_messages` to find them.

## How It Works

The server reads MailMate's on-disk message store at:

```
~/Library/Application Support/MailMate/Messages.noindex/IMAP/
  <account>/
    <mailbox>.mailbox/
      Messages/
        <uid>.eml
```

**`mailstore.py`** — filesystem and parsing layer. Walks the store, parses `.eml` files, and maintains an in-memory index for fast search. No MCP or AppleScript dependencies.

**`server.py`** — MCP boundary. Defines all tools via `fastmcp` and issues AppleScript calls to MailMate for actions that require IMAP sync (open, tag, activate).

## Development

```bash
# Run the server directly (stdio transport)
uv run mailmate-mcp

# Or via the module
uv run python -m mailmate_mcp.server
```

There is no automated test suite. Manual testing is done by registering the server with Claude Desktop or Claude Code and exercising the tools in a conversation.
