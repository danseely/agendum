# agendum

A terminal dashboard for tracking your GitHub PRs, issues, and tasks. Runs persistently in an iTerm2 pane, syncs with GitHub every 60 seconds, and rings the bell when something needs your attention.

## Install

```bash
uv tool install --editable /path/to/agendum
```

Requires:
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [gh CLI](https://cli.github.com/) installed and authenticated (`gh auth login`)

If `agendum` is not found after installing, add uv's tool directory to your shell:

```bash
uv tool update-shell
```

If you previously installed agendum with pip, remove that install first:

```bash
python -m pip uninstall agendum
```

## Development Hooks

Install the local git hooks after syncing dev dependencies:

```bash
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
```

Commits and PR titles are expected to follow Conventional Commits, and releases follow SemVer.

## Development

```bash
uv sync --dev
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
```

This repo uses Conventional Commits and SemVer. PR titles should also follow Conventional Commits so squash merges remain release-friendly.

Releases are created manually from the GitHub Actions release workflow on `main`. For the first tagged release, provide an explicit version input because there is no previous release tag yet.

## Usage

```bash
agendum
```

For a non-interactive install check:

```bash
agendum self-check
```

On first run, you'll be prompted to configure your GitHub org. Config lives at `~/.agendum/config.toml`.

## MCP Server

For local development, register the MCP server from this checkout. This avoids a separate tool install and keeps the MCP server using the code in your working tree.

Codex CLI:

```bash
codex mcp add agendum -- "$(which uv)" run --directory /path/to/agendum agendum-mcp
```

Claude Code:

```bash
claude mcp add agendum -- "$(which uv)" run --directory /path/to/agendum agendum-mcp
```

If your MCP client does not have an add command, configure the stdio server manually:

```json
{
  "mcpServers": {
    "agendum": {
      "command": "/path/to/uv",
      "args": ["run", "--directory", "/path/to/agendum", "agendum-mcp"]
    }
  }
}
```

Use the absolute path from `which uv` for `command`, and the absolute path to this checkout for `--directory`.

For a global command install instead:

```bash
uv tool install --reinstall --editable /path/to/agendum
```

Then register the installed executable:

```bash
codex mcp add agendum -- agendum-mcp
claude mcp add agendum -- agendum-mcp
```

Or configure it manually:

```json
{
  "mcpServers": {
    "agendum": {
      "command": "agendum-mcp"
    }
  }
}
```

If Claude can't find `agendum-mcp`, use the absolute path from `which agendum-mcp`.

Examples:
- "Create a new agendum task called follow up on telemetry PR"
- "Are there any open PRs waiting on my review?"
- "Did Alex review my API PR yet?"

## Keybindings

| Key | Action |
|-----|--------|
| `j` / `↓` | Move down |
| `k` / `↑` | Move up |
| `Enter` | Open action menu for selected task |
| `r` | Force sync now |
| `q` | Quit |

Type in the bottom input row to create a manual task.

## Config

`~/.agendum/config.toml`:

```toml
[github]
orgs = ["example-org"]
exclude_repos = []

[sync]
interval = 60

[display]
seen_delay = 3
```
