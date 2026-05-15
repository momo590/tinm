# TINM x Continue.dev Integration [BETA-unverified]

> **Status:** Schema unverified — not tested against a live Continue.dev
> installation. Continue.dev is a VS Code / JetBrains extension, not a CLI,
> so this adapter is a one-shot **session importer**, not a real-time hook.

## What it does

Continue.dev does not expose an extension-host hook surface that an external
process can subscribe to. It does, however, write each conversation to a JSON
file on disk:

- macOS / Linux: `~/.continue/sessions/<session_id>.json`
- Windows: `%USERPROFILE%\.continue\sessions\<session_id>.json`

The importer reads one of these files, extracts every `{"role": "user", ...}`
message, and replays it through TINM's normal `user_prompt.sh` pipeline. The
result is that your Continue.dev conversation history shows up in the current
TINM thread's trajectory.

## Install

1. Make sure TINM is installed (you should have `~/.tinm/.venv/bin/python` and
   the skill at `~/.claude/skills/tinm/tinm_continue.py`).

2. Find the Continue.dev session file you want to import:

   ```bash
   ls -lt ~/.continue/sessions/ | head
   ```

3. Run the importer:

   ```bash
   bash ~/.tinm/source/mvp/hooks/continue_import.sh \
       ~/.continue/sessions/<session_id>.json
   ```

   It will print `tinm_continue: imported N user message(s).`

## Recommended workflow

Run the importer at the end of a Continue.dev work session, once your TINM
thread is set (`/tinm load <slug>`), so each Continue.dev prompt lands in the
right thread.

A simple cron / launchd loop is also viable:

```bash
# Every 10 minutes, import the most recent Continue.dev session
*/10 * * * * bash ~/.tinm/source/mvp/hooks/continue_import.sh \
    "$(ls -t ~/.continue/sessions/*.json | head -1)"
```

(Be aware: this will re-import the same messages each time. Future versions of
the importer should de-dupe, but the current MVP does not.)

## Limitations

- **One-shot import**, not real-time. Prompts only land in TINM after you run
  the script.
- **No de-dup yet** — running the importer twice on the same file replays the
  same messages twice. Track imported file mtimes if you cron this.
- **Schema drift risk** — Continue.dev's session JSON shape is not contract-
  ually stable. The importer handles the two shapes we've observed
  (`messages` and `history` arrays, with both string and structured content),
  but a future Continue.dev release could change this.

## Reporting issues

Open an issue with:

- Your Continue.dev version
- A redacted sample of a session file showing the schema you have
- The error you got
