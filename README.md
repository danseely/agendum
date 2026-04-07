# agendum

A terminal dashboard for tracking your GitHub PRs, issues, and tasks. Runs persistently in an iTerm2 pane, syncs with GitHub every 60 seconds, and rings the bell when something needs your attention.

## Install

```bash
pip install -e .
```

Requires:
- Python 3.11+
- [gh CLI](https://cli.github.com/) installed and authenticated (`gh auth login`)

## Usage

```bash
agendum
```

On first run, you'll be prompted to configure your GitHub org. Config lives at `~/.agendum/config.toml`.

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
orgs = ["your-org"]
exclude_repos = []

[sync]
interval = 60

[display]
seen_delay = 3
```
