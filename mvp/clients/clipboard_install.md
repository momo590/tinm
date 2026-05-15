# TINM Clipboard Watcher

> Universal fallback adapter — captures AI conversations from any tool (ChatGPT web, Claude.ai web, Perplexity, Notion AI, …) via opt-in clipboard trigger.

## How it works

The clipboard watcher polls your system clipboard. When it sees the trigger phrase `# TINM SAVE` on the first line, it captures everything after that line into your current TINM thread.

**Privacy**: TINM only reads clipboard content that explicitly starts with `# TINM SAVE`. Without the trigger, your clipboard is ignored. The trigger line is stripped before capture (not stored).

## Install

The clipboard watcher is bundled — no extra install needed if you already have TINM.

**Dependencies** (one of):
- macOS: `pbpaste` (built-in)
- Linux X11: `xclip` (`apt install xclip`) or `xsel`
- Linux Wayland: `wl-paste` (part of `wl-clipboard` package)

## Usage

### One-shot mode (manual)

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_clipboard.py --once
```

Checks your clipboard once and exits. Good to wire into other tools or as a manual capture trigger.

### Daemon mode (background polling)

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_clipboard.py --watch &
```

Polls every 3 seconds in the background. Add to your shell startup if you want it always-on.

```bash
# Check daemon status
python ~/.claude/skills/tinm/tinm_clipboard.py --status

# Stop the daemon
python ~/.claude/skills/tinm/tinm_clipboard.py --stop
```

### How to capture a conversation

1. In ChatGPT/Claude.ai/Perplexity/wherever, select the conversation text you want to save.
2. Copy it to clipboard.
3. **Prepend `# TINM SAVE`** as the first line. For example, paste into a scratch text editor first, add the trigger, then re-copy:

```
# TINM SAVE

Q: How do I implement OAuth in Next.js?
A: Use next-auth. Install with `npm i next-auth`, then create...
```

4. The watcher will detect the trigger and capture everything after into your current TINM thread.

## Verification

Make sure TINM has a current thread loaded:

```bash
cat ~/.tinm/current_thread
```

If empty, run `tinm load <thread>` first. Captured content lands in your thread's trajectory and becomes searchable via `artifact_find`.

## Limitations

- Does not work for streaming responses — you can only capture after the AI's response is complete and visible.
- One capture per unique content (dedup by hash). Re-copying the same text won't double-capture.
- Linux Wayland support requires `wl-clipboard`. X11 needs `xclip` or `xsel`.
