# TINM v0.1 — 2-minute setup

On macOS or Linux, in your terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/momo590/tinm/main/mvp/scripts/install.sh | bash
```

The installer will ask one question (telemetry — say `y` for local-only metrics, `n` if you'd rather not, totally fine either way).

That's it. Open Claude Code and run `/tinm init my-project` to create your first thread.

## How it works in 30 seconds

1. **Cross-session** — TINM watches your sessions and stores named artifacts. Next time you open Claude Code on the same machine, the relevant ones come back automatically.
2. **Long sessions** — coming in v0.2 (a digest gets injected when the session crosses ~40k tokens to keep Claude sharp).
3. **Privacy** — everything is local, in `~/.tinm/`. Nothing leaves your machine unless you explicitly opt in to share aggregate telemetry (counts only, never content).

## Try it

After install, in any Claude Code session:

```
/tinm init my-project
```
Creates your first thread. Then keep working as usual.

Anytime you want to mark something for cross-session recall:
```
/tinm save "the architecture decision"
```

Next session, ask Claude anything that touches that decision — TINM will pull the artifact back without you re-pasting anything.

## Check what TINM is doing

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_status.py
```

Shows your current thread, anchor terms, trajectory length.

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_telemetry.py status
```

If you opted in to telemetry: shows event count + the path to the local log.

## Uninstall (fully reversible)

```bash
bash ~/.tinm/source/mvp/scripts/uninstall.sh
```

Removes `~/.tinm/`, `~/.claude/skills/tinm/`, and the TINM-registered hooks from `~/.claude/settings.json` (your other hooks are preserved). A backup of `settings.json` is left at `settings.json.pre-uninstall` in case you want to roll back manually.

Pass `TINM_KEEP_PCP=1` to keep your thread history at `~/tinm-pcp-keep-<timestamp>/` for future reinstall:

```bash
TINM_KEEP_PCP=1 bash ~/.tinm/source/mvp/scripts/uninstall.sh
```

## Feedback

Anything weird? Anything magical? DM me the moment you notice — that's exactly what I'm collecting for beta v0.1.
