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
orgs = ["example-org"]
exclude_repos = []

[sync]
interval = 60

[display]
seen_delay = 3
```
