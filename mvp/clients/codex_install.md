# TINM x Codex CLI Integration [BETA-unverified]

> **Status:** Wrapper pattern, not tested against a live OpenAI Codex CLI
> installation. The hook wraps the `codex` binary and tees every line the
> user types into TINM's pipeline.

## What it does

The OpenAI Codex CLI does not expose a hook API. Instead, we wrap the `codex`
binary: stdin lines you type are mirrored to both the real codex CLI and to
TINM's `user_prompt.sh` (in a background thread). Codex receives the same
input unchanged, so behavior is identical.

## Install

1. Make sure TINM is installed (you should have `~/.tinm/.venv/bin/python` and
   the skill at `~/.claude/skills/tinm/tinm_cli_wrap.py`).

2. Add a shell alias to your `~/.bashrc` or `~/.zshrc`:

   ```bash
   alias codex='bash ~/.tinm/source/mvp/hooks/codex_hook.sh'
   ```

3. Reload your shell: `source ~/.bashrc` (or `~/.zshrc`).

4. Use `codex` exactly as you always have. Every prompt you type is captured
   into the current TINM thread.

## Verifying it works

After typing a prompt in Codex:

```bash
tail -n 3 /tmp/tinm_hook.log
```

You should see a recent `hook fired` line.

## Overrides

- `TINM_CODEX_BIN=/path/to/real/codex` — point at a non-default codex binary
  (useful when codex is shadowed by the alias itself, or for testing).
- `TINM_HOME=...` — override the TINM install root (default `$HOME/.tinm`).

## Limitations

- Only **stdin** is captured. If Codex reads prompts from any source other
  than stdin (e.g., a TUI key handler), those prompts are invisible to TINM.
- Multi-line input (paste) is captured as multiple separate trajectory turns.
- If the Codex binary is missing, the wrapper falls through to running the
  user's command directly.

## Reporting issues

Open an issue with:

- Your Codex CLI version
- Output of `tail -n 50 /tmp/tinm_hook.log` (scrub anything sensitive)
- The alias / hook setup you used
