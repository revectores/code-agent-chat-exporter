#!/usr/bin/env python3
"""Export Claude Code, Codex CLI, and GitHub Copilot Chat history to Markdown."""

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_DIR = Path("./exported_chats")


# ── helpers ──────────────────────────────────────────────────────────────────

def ts_to_dt(ts) -> datetime:
    """Convert epoch-ms or ISO string to datetime."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def safe_filename(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip()
    s = re.sub(r"[\s]+", "_", s)
    return s[:max_len] or "untitled"


def write_md(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  wrote {path}")


# ── Claude Code ───────────────────────────────────────────────────────────────

def extract_claude_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    name = block.get("name", "tool")
                    inp = block.get("input", {})
                    parts.append(f"*[Tool: {name}]*\n```json\n{json.dumps(inp, indent=2, ensure_ascii=False)}\n```")
                elif block.get("type") == "tool_result":
                    c = block.get("content", "")
                    if isinstance(c, list):
                        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                    parts.append(f"*[Tool result]*\n```\n{c}\n```")
        return "\n".join(parts)
    return ""


def export_claude(out_dir: Path):
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        print("Claude Code projects directory not found, skipping.")
        return

    dest = out_dir / "claude"
    total = 0

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        project_name = project_dir.name.lstrip("-").replace("-", "/")

        for jsonl_path in sorted(project_dir.glob("*.jsonl")):
            session_id = jsonl_path.stem
            messages = []

            with open(jsonl_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") not in ("user", "assistant"):
                        continue
                    role = obj["type"]
                    msg = obj.get("message", {})
                    text = extract_claude_text(msg.get("content", ""))
                    if not text.strip():
                        continue
                    ts = obj.get("timestamp", "")
                    dt = ts_to_dt(ts) if ts else None
                    messages.append((role, text.strip(), dt))

            if not messages:
                continue

            first_dt = next((m[2] for m in messages if m[2]), None)
            first_user = next((m[1] for m in messages if m[0] == "user"), session_id)
            title = safe_filename(first_user[:50])
            date_str = first_dt.strftime("%Y-%m-%d") if first_dt else "unknown"

            lines = [
                f"# {first_user[:100]}",
                "",
                f"**Source:** Claude Code  ",
                f"**Project:** `{project_name}`  ",
                f"**Session:** `{session_id}`  ",
                f"**Date:** {date_str}",
                "",
                "---",
                "",
            ]
            for role, text, dt in messages:
                label = "**User**" if role == "user" else "**Claude**"
                ts_str = f" *({dt.strftime('%H:%M:%S UTC')})*" if dt else ""
                lines.append(f"### {label}{ts_str}")
                lines.append("")
                lines.append(text)
                lines.append("")

            filename = dest / date_str / f"{title}__{session_id[:8]}.md"
            write_md(filename, "\n".join(lines))
            total += 1

    print(f"Claude Code: exported {total} session(s).")


# ── Codex CLI ─────────────────────────────────────────────────────────────────

_CODEX_SKIP_PREFIXES = (
    "<permissions",
    "# AGENTS.md",
    "# Context from",
    "<turn_aborted>",
    "<collaboration_mode>",
    "<local-command-caveat>",
)


def extract_codex_user_request(text: str) -> str:
    """Strip IDE context wrapper from Codex user messages."""
    marker = "## My request for Codex:\n"
    idx = text.find(marker)
    if idx != -1:
        return text[idx + len(marker):].strip()
    if any(text.startswith(p) for p in _CODEX_SKIP_PREFIXES):
        return ""
    return text.strip()


def export_codex(out_dir: Path):
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.exists():
        print("Codex sessions directory not found, skipping.")
        return

    dest = out_dir / "codex"
    total = 0

    for jsonl_path in sorted(sessions_dir.rglob("rollout-*.jsonl")):
        session_meta = {}
        messages = []

        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if obj.get("type") == "session_meta":
                    session_meta = obj.get("payload", {})
                    continue

                if obj.get("type") != "response_item":
                    continue

                payload = obj.get("payload", {})
                if payload.get("type") != "message":
                    continue

                role = payload.get("role", "")
                if role not in ("user", "assistant"):
                    continue

                content = payload.get("content", [])
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        t = block.get("text") or block.get("output_text") or block.get("input_text", "")
                        if t:
                            text_parts.append(t)
                raw_text = "\n".join(text_parts).strip()

                if role == "user":
                    text = extract_codex_user_request(raw_text)
                else:
                    # Skip empty / phase=commentary very short messages
                    text = raw_text
                    if payload.get("phase") == "commentary" and len(text) < 20:
                        continue

                if not text:
                    continue

                ts_str = obj.get("timestamp", "")
                dt = ts_to_dt(ts_str) if ts_str else None
                messages.append((role, text, dt))

        if not messages:
            continue

        # Deduplicate consecutive identical user messages (Codex re-sends context on retry)
        deduped = [messages[0]]
        for msg in messages[1:]:
            if msg[0] == "user" and deduped[-1][0] == "user" and msg[1] == deduped[-1][1]:
                continue
            deduped.append(msg)
        messages = deduped

        session_id = session_meta.get("id", jsonl_path.stem)
        cwd = session_meta.get("cwd", "")
        first_dt = next((m[2] for m in messages if m[2]), None)
        first_user = next((m[1] for m in messages if m[0] == "user"), session_id)
        title = safe_filename(first_user[:50])
        date_str = first_dt.strftime("%Y-%m-%d") if first_dt else "unknown"

        lines = [
            f"# {first_user[:100]}",
            "",
            f"**Source:** Codex CLI  ",
            f"**Workspace:** `{cwd}`  ",
            f"**Session:** `{session_id}`  ",
            f"**Date:** {date_str}",
            "",
            "---",
            "",
        ]
        for role, text, dt in messages:
            label = "**User**" if role == "user" else "**Codex**"
            ts_label = f" *({dt.strftime('%H:%M:%S UTC')})*" if dt else ""
            lines.append(f"### {label}{ts_label}")
            lines.append("")
            lines.append(text)
            lines.append("")

        filename = dest / date_str / f"{title}__{session_id[:8]}.md"
        write_md(filename, "\n".join(lines))
        total += 1

    print(f"Codex CLI: exported {total} session(s).")


# ── GitHub Copilot Chat (VS Code) ─────────────────────────────────────────────

def assemble_copilot_response(response_items: list) -> str:
    """Join streamed markdown chunks and summarise inline edits."""
    text_parts = []
    edit_files = []

    for item in response_items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind is None and "value" in item:
            text_parts.append(item["value"])
        elif kind == "textEditGroup":
            uri = item.get("uri", {})
            fpath = uri.get("fsPath") or uri.get("path", "")
            if fpath:
                edit_files.append(fpath)

    result = "".join(text_parts).strip()
    if edit_files:
        edits_md = "\n".join(f"- `{p}`" for p in edit_files)
        result = (result + "\n\n*Inline edits applied to:*\n" + edits_md).strip()
    return result


def get_workspace_folder(db_path: Path) -> str:
    """Read workspace.json next to state.vscdb to get the folder name."""
    ws_json = db_path.parent / "workspace.json"
    if ws_json.exists():
        try:
            data = json.loads(ws_json.read_text())
            folder = data.get("folder", "")
            if folder:
                return Path(folder.replace("file://", "")).name
        except Exception:
            pass
    return db_path.parent.name[:16]


def export_copilot(out_dir: Path):
    ws_storage = Path.home() / "Library" / "Application Support" / "Code" / "User" / "workspaceStorage"
    if not ws_storage.exists():
        print("VS Code workspaceStorage not found, skipping.")
        return

    dest = out_dir / "copilot"
    total = 0

    for chat_sessions_dir in sorted(ws_storage.glob("*/chatSessions")):
        if not chat_sessions_dir.is_dir():
            continue
        db_path = chat_sessions_dir.parent / "state.vscdb"
        workspace_name = get_workspace_folder(db_path)

        for session_json in sorted(chat_sessions_dir.glob("*.json")):
            try:
                obj = json.load(open(session_json, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            requests = obj.get("requests", [])
            if not requests:
                continue

            messages = []
            for req in requests:
                user_text = req.get("message", {}).get("text", "").strip()
                if not user_text:
                    continue
                ts_ms = req.get("timestamp")
                dt = ts_to_dt(ts_ms) if ts_ms else None
                messages.append(("user", user_text, dt))

                response_text = assemble_copilot_response(req.get("response", []))
                if response_text:
                    messages.append(("assistant", response_text, dt))

            if not messages:
                continue

            session_id = session_json.stem
            first_dt = next((m[2] for m in messages if m[2]), None)
            first_user = next((m[1] for m in messages if m[0] == "user"), session_id)
            title = safe_filename(first_user[:50])
            date_str = first_dt.strftime("%Y-%m-%d") if first_dt else "unknown"

            lines = [
                f"# {first_user[:100]}",
                "",
                f"**Source:** GitHub Copilot Chat  ",
                f"**Workspace:** `{workspace_name}`  ",
                f"**Session:** `{session_id}`  ",
                f"**Date:** {date_str}",
                "",
                "---",
                "",
            ]
            for role, text, dt in messages:
                label = "**User**" if role == "user" else "**Copilot**"
                ts_label = f" *({dt.strftime('%H:%M:%S UTC')})*" if dt else ""
                lines.append(f"### {label}{ts_label}")
                lines.append("")
                lines.append(text)
                lines.append("")

            filename = dest / date_str / f"{title}__{session_id[:8]}.md"
            write_md(filename, "\n".join(lines))
            total += 1

    print(f"GitHub Copilot Chat: exported {total} session(s).")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Export Claude Code / Codex CLI / Copilot Chat history to Markdown"
    )
    parser.add_argument(
        "--source",
        choices=["claude", "codex", "copilot", "all"],
        default="all",
        help="Which source(s) to export (default: all)",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_DIR),
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    args = parser.parse_args()

    out = Path(args.output)
    print(f"Exporting to: {out.resolve()}\n")

    src = args.source
    if src in ("claude", "all"):
        export_claude(out)
    if src in ("codex", "all"):
        export_codex(out)
    if src in ("copilot", "all"):
        export_copilot(out)

    print("\nDone.")


if __name__ == "__main__":
    main()
