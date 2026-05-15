# TINM Clipboard Watcher

> Universal capture for AI conversations from any tool (ChatGPT web, Claude.ai, Perplexity, Notion AI, Gemini, …). Opt-in trigger phrase. Auto-starts with Claude Code. Native system notification on capture.

## Quick start (1 command, then forget it)

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_clipboard.py --enable
```

That's it. From now on, every time you open Claude Code, the watcher starts in the background. When you copy something starting with `# TINM SAVE`, you get a desktop notification and the content lands in your current TINM thread.

## How to capture

1. In ChatGPT / Claude.ai / Perplexity / wherever, select the conversation text you want to save.
2. Open a text editor (or just use the URL bar). Type:
   ```
   # TINM SAVE
   <paste your conversation here>
   ```
3. Select all (Cmd+A / Ctrl+A) and copy (Cmd+C / Ctrl+C).
4. **Within ~3 seconds**, a system notification appears: `TINM captured to <thread>`. Done.

## Privacy

- The watcher does **nothing** unless your clipboard's first non-empty line is exactly `# TINM SAVE` (case-insensitive).
- The trigger line is stripped before storage — not stored.
- Clipboard is read, never modified.
- Duplicate captures (re-copying the same content) are silently skipped.

## Commands

```bash
# Status — opt-in state + daemon state
python ~/.claude/skills/tinm/tinm_clipboard.py --status

# Manually trigger one capture check (useful for testing)
python ~/.claude/skills/tinm/tinm_clipboard.py --once

# Stop the daemon (will restart on next Claude Code session if still enabled)
python ~/.claude/skills/tinm/tinm_clipboard.py --stop

# Fully opt-out (stops daemon + prevents auto-start)
python ~/.claude/skills/tinm/tinm_clipboard.py --disable
```

## Notifications

Native to your OS:
- **macOS**: AppleScript `display notification` (appears top-right via Notification Center)
- **Linux**: `notify-send` (requires `libnotify-bin` or equivalent — `apt install libnotify-bin`)

If notifications don't appear, capture still works — only the visual feedback is gone. To verify:

```bash
cat ~/.tinm/pcp/threads/$(cat ~/.tinm/current_thread).json | tail -20
```

The last entry should be your capture.

## Dependencies (clipboard read)

Auto-detected based on platform:
- macOS: `pbpaste` (built-in)
- Linux X11: `xclip` (`apt install xclip`) or `xsel`
- Linux Wayland: `wl-paste` (`apt install wl-clipboard`)

## How the auto-start works

When you run `--enable` once, TINM creates `~/.tinm/clipboard_enabled` (a marker file). The Claude Code `SessionStart` hook checks for this file every time you open Claude Code — if present and no daemon is running, it launches one as a detached background process. Survives Claude Code session exit. Restarts cleanly the next time you open Claude Code.

No `launchd`, no systemd, no per-OS install. Just one flag file and a hook.

## Limitations

- Only captures after the AI's response is fully visible and you copy it. Streaming responses can't be auto-captured.
- One capture per unique content (dedup by hash).
- Linux Wayland needs `wl-clipboard`. X11 needs `xclip` or `xsel`.
