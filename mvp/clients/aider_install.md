# TINM x Aider Integration [BETA-unverified]

> **Status:** Wrapper pattern, not tested against a live Aider installation.
> The hook wraps the `aider` binary and tees every line the user types into
> TINM's pipeline. Use `TINM_DEBUG=1` to inspect captured prompts.

## What it does

Aider does not expose a hook API the way Cursor does. Instead, we wrap the
`aider` CLI: when you type a prompt at Aider's `> ` prompt, the wrapper sees
the same stdin line, normalizes it via `tinm_vendor_adapters.normalize_for("aider", ...)`,
and pipes it to `user_prompt.sh` in a background thread. Aider itself receives
the same line unchanged, so your workflow is identical.

## Install

1. Make sure TINM is installed (you should have `~/.tinm/.venv/bin/python` and
   the skill at `~/.claude/skills/tinm/tinm_cli_wrap.py`).

2. Add a shell alias to your `~/.bashrc` or `~/.zshrc`:

   ```bash
   alias aider='bash ~/.tinm/source/mvp/hooks/aider_hook.sh'
   ```

3. Reload your shell: `source ~/.bashrc` (or `~/.zshrc`).

4. Use `aider` exactly as you always have. Every prompt you type at `> ` is
   captured into the current TINM thread.

## Verifying it works

After typing a prompt in Aider:

```bash
tail -n 3 /tmp/tinm_hook.log
```

You should see a recent `hook fired` line. You can also use
`/tinm` (the skill) in Claude Code to confirm the prompt is recorded in
the current thread's trajectory.

## Overrides

- `TINM_AIDER_BIN=/path/to/real/aider` — point at a non-default aider binary
  (useful when aider is shadowed by the alias itself, or for testing).
- `TINM_HOME=...` — override the TINM install root (default `$HOME/.tinm`).

## Limitations

- Only **stdin** is captured. Slash commands typed at Aider's prompt are
  recorded as-is; if Aider expands them into a richer prompt internally,
  TINM sees only the typed form.
- Multi-line input (paste) is captured as multiple separate trajectory turns.
- If the Aider binary is missing, the wrapper falls through to running the
  user's command directly (exit 127 — same as bash).

## Reporting issues

Open an issue with:

- Your Aider version (`aider --version`)
- Output of `tail -n 50 /tmp/tinm_hook.log` (scrub anything sensitive)
- The alias / hook setup you used
