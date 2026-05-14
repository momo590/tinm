# Recording the README hero asciinema cast

The README's hero is currently a verbatim text transcript. Once you
record the asciinema cast, drop the GIF into this directory and
uncomment the `<!-- ASCIINEMA HERO -->` block in `README.md`.

## One-shot prerequisites

```bash
# macOS
brew install asciinema agg

# Linux (Debian/Ubuntu)
sudo apt install asciinema
cargo install --git https://github.com/asciinema/agg   # or download a release binary
```

## Seed the demo thread (the scenario the cast reproduces)

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_demo.py --reset
```

This puts `tinm-tour` into the PCP store and sets it as the current
thread, exactly the state the cast assumes.

## Terminal setup

- Size: 80 columns × 24 rows. (Resize before recording — `resize -s 24 80`
  on Linux, or just shrink the window.)
- Font: JetBrains Mono 16pt (any monospace works; avoid nerd-font
  glyphs that `agg` does not render).
- Theme: Solarized Dark or Catppuccin Mocha. Dark background.
- Clear scrollback before starting (`clear && printf '\e[3J'`).

## Record

```bash
asciinema rec docs/assets/replay.cast --idle-time-limit 1.5 \
  --title "TINM — new session surfaces yesterday's biggest effect"
```

The recording session:

1. (Cold terminal, prompt visible.)
2. Type: `claude` ⏎ — Claude Code launches.
3. After the `SessionStart` hook output settles, type the prompt verbatim:
   ```
   What was the biggest absolute effect we measured on the 2WikiMultihopQA pilot?
   ```
4. The `[TINM — Turn 1 | Anchor: ...]` injection should print BEFORE
   Claude's response. The response should cite `+0.114 F1` and `t=4.03`
   from the `wiki2hop-results` artifact, and should explicitly say no
   file was read.
5. Stop recording: Ctrl-D (or close the Claude Code session).

Hard ceiling: 30 seconds. Retake if longer.

## Render to GIF

```bash
agg --font-size 16 --font-family "JetBrains Mono" \
    docs/assets/replay.cast docs/assets/replay.gif
```

Target file size ≤ 1.5 MB. If `agg` outputs larger:

```bash
agg --font-size 16 --speed 1.2 docs/assets/replay.cast docs/assets/replay.gif
```

If still > 2 MB, upload the `.cast` to https://asciinema.org instead
and embed a static thumbnail PNG here:

```bash
# Render a single-frame PNG thumbnail for the mobile fallback
agg --font-size 16 --last-frame docs/assets/replay.cast docs/assets/replay.png
```

## Wire it into the README

Uncomment the `<!-- ASCIINEMA HERO -->` block in `README.md`. If you
shipped the asciinema.org route, replace the `<img src=...>` with the
asciinema embed snippet.

## Commit

```bash
git add docs/assets/replay.cast docs/assets/replay.gif docs/assets/replay.png README.md
git commit -m "docs(readme): add asciinema replay cast + GIF hero"
```

Last commit of the polish series — ship.
