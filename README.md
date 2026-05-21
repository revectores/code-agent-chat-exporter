# code-agent-chat-exporter

Export chat history from Claude Code, Codex CLI, and GitHub Copilot Chat to Markdown files.

## Supported sources

| Source | Local data location |
|--------|-------------------|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex CLI | `~/.codex/sessions/**/rollout-*.jsonl` |
| GitHub Copilot Chat (VS Code) | `~/Library/Application Support/Code/User/workspaceStorage/*/chatSessions/*.json` |

## Usage

```bash
python export_chats.py [--source {claude,codex,copilot,all}] [--output DIR]
```

**Options**

- `--source` — which source(s) to export (default: `all`)
- `--output` — destination directory (default: configured `OUTPUT_DIR` at top of script)

**Examples**

```bash
# Export everything
python export_chats.py

# Export only Claude Code sessions
python export_chats.py --source claude

# Export to a custom directory
python export_chats.py --output ~/Desktop/chats
```

## Output structure

Each session is written as a single Markdown file:

```
<output_dir>/
└── <project-folder-name>/
    └── <YYYYMMDD_HHMMSS>__<title>__<session-id>.md
```

The filename prefix is the UTC timestamp of the first message, making sessions sort chronologically by default.

Each file includes a metadata header (source, project path, session ID, date) followed by the full conversation with per-message timestamps.
