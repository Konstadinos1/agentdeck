# agentdeck

Terminal control center for local AI coding agents on Linux / WSL2.

`agentdeck` gives you one dashboard for all your local agents (**Claude Code**, **Codex**, **Hermes**, **OpenClaw**, **OpenCode**, **Antigravity**, **Gemini CLI**, **Z Code**, **Paperclip**, **Ollama**):

- **Processes (`1`)**: Live CPU% and RSS memory aggregated across each agent's entire process tree (including child MCP servers, shells, and language servers), plus process mode (`tty` vs `daemon`), uptime, and command line.
- **Sessions (`2`)**: Recent conversations across Claude Code, Codex, Hermes, and OpenClaw with model, input/output/cache token counts, cost, and last activity.
- **Usage (`3`)**: Today vs. lookback-window token and cost totals per agent.
- **One-key actions**: Graceful kill (`k`), force kill (`K`), and safe daemon restart (`r`) with confirmation modals.

## Quickstart

```bash
cd ~/agentdeck
uv sync
uv run agentdeck
```

Or install globally as a CLI tool:

```bash
uv tool install --editable ~/agentdeck
agentdeck
```

## Keybindings

| Key | Action |
| --- | --- |
| `1` / `2` / `3` | Switch to **Processes**, **Sessions**, or **Usage** tab |
| `f` | Cycle agent filter (`all` → active agents) |
| `s` | Cycle process sort order (`ram` → `cpu` → `agent`) |
| `k` / `K` | Graceful stop (`SIGTERM` tree) / Force kill (`SIGKILL` tree) |
| `r` | Restart selected daemon agent (detaches and logs to `~/.local/state/agentdeck/restarts/`) |
| `u` | Reload session logs immediately |
| `q` | Quit |

## Pricing Configuration

Hermes and OpenClaw record session costs directly in their own logs/databases, and `agentdeck` displays those reported figures automatically.

For **Claude Code** and **Codex**, copy `pricing.example.toml` to `~/.config/agentdeck/pricing.toml` and fill in your current per-million-token rates:

```bash
mkdir -p ~/.config/agentdeck
cp pricing.example.toml ~/.config/agentdeck/pricing.toml
```

## Development

```bash
uv run pytest --cov=agentdeck
```
