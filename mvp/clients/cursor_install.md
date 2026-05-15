# TINM x Cursor Integration [BETA]

> **Status:** Schema unverified — not tested against a live Cursor installation.  
> Run `cursor_hook.sh --debug` to verify the JSON received from Cursor.

## What it does

Maps Cursor's `beforeSubmitPrompt` hook to TINM's memory pipeline.  
Every prompt in Cursor gets captured and stored in your PCP thread, exactly like Claude Code.

## Install

1. In your project, create `.cursor/hooks/beforeSubmitPrompt.sh`:

```bash
#!/bin/bash
exec bash ~/.tinm/source/mvp/hooks/cursor_hook.sh "$@"
```

2. Make it executable: `chmod +x .cursor/hooks/beforeSubmitPrompt.sh`

3. Verify the schema (first run):
```bash
echo '{"prompt": "test"}' | bash ~/.tinm/source/mvp/hooks/cursor_hook.sh --debug
cat /tmp/tinm_cursor_debug.log
```

4. If the schema doesn't match, look at the logged JSON and update the field
   mapping in `tinm_cursor.py::_PROMPT_FIELDS` and `_SESSION_FIELDS`.

## Debug mode

Run with `--debug` to log the full JSON received from Cursor to `/tmp/tinm_cursor_debug.log`.
This lets you verify the actual field names Cursor sends.

## Reporting schema issues

If the adapter doesn't work, open an issue with:
- Your Cursor version
- The contents of `/tmp/tinm_cursor_debug.log` (no sensitive data)
