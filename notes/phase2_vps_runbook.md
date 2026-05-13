# Phase 2 — TINM cross-machine setup (Mac ↔ VPS Hostinger)

Step-by-step runbook for the Phase 2 (ii) deployment locked on
2026-05-13: shared TINM memory between the Mac and the Hostinger VPS,
both running Claude Code, via **Syncthing-over-Tailscale**. No sync
code inside TINM — only filesystem sharing as recommended by
`mvp/pcp_v0_spec.md` §5.

**Architecture in one diagram:**

```
  Mac ──────── Tailscale mesh (WireGuard, 100.x.y.z) ────────── VPS
    │                                                            │
    └── Syncthing daemon ◄═══ shares "tinm-pcp" ══════► Syncthing daemon
            │                                                    │
            ▼                                                    ▼
  $TINM_PCP_DIR (synced)                              $TINM_PCP_DIR (synced)
  $TINM_HOME/.venv (local)                            $TINM_HOME/.venv (local)
  $TINM_HOME/current_thread (local)                   $TINM_HOME/current_thread (local)
```

The Mac and VPS each run their own Claude Code, their own venv, their
own session marker. They share only the PCP store (threads + artifacts
JSON) through a Syncthing folder reachable over Tailscale.

---

## 0. Pre-flight checklist

- [ ] Mac is on a recent macOS with Homebrew installed.
- [ ] You can SSH into the Hostinger VPS as a sudo-capable user.
- [ ] The VPS runs a Debian/Ubuntu base (Hostinger hPanel default;
  adjust `apt` commands if you picked a different distro).
- [ ] You have a Tailscale account (free Personal tier is enough for
  two devices) — sign up at https://login.tailscale.com.
- [ ] You have an Anthropic API key set on the VPS (`ANTHROPIC_API_KEY`
  in `~/.bashrc` — never paste it in chat).
- [ ] The TINM repo is on a remote you can `git clone` from on the VPS
  (push from Mac to GitHub/GitLab first if it is local-only).

---

## 1. Install Tailscale on both hosts

**Mac** (one of the two, pick the easier):

```bash
brew install --cask tailscale     # menu-bar app, auth via browser
# OR the headless CLI:
brew install tailscale
sudo tailscale up
```

**VPS** (Debian/Ubuntu):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

The `tailscale up` step prints a login URL; open it once per host to
join the same tailnet. Verify both hosts appear in
`tailscale status` on either side.

**Test connectivity** (replace 100.x.y.z with the VPS's Tailscale IP
from `tailscale status`):

```bash
tailscale ping 100.x.y.z          # from Mac
ssh user@100.x.y.z                # SSH over the tailnet, no public IP needed
```

You can keep using Hostinger's public IP for SSH if you prefer — the
tailnet is for Syncthing pairing, not a requirement for shell access.

---

## 2. Install TINM on the VPS

The VPS will run Claude Code with the same MCP server + hooks as the
Mac. From the VPS shell:

```bash
# (a) System deps
sudo apt update
sudo apt install -y python3.12 python3.12-venv git

# (b) Clone the TINM repo. Replace <REMOTE> with your fork/remote URL.
git clone <REMOTE> ~/TNIM
cd ~/TNIM

# (c) Build the venv
mkdir -p ~/.tinm
python3.12 -m venv ~/.tinm/.venv
~/.tinm/.venv/bin/pip install --no-cache-dir -r mvp/requirements.txt

# (d) Symlinks for hooks + slash command (mirror the Mac install)
mkdir -p ~/.claude/skills ~/.claude/commands
ln -s ~/TNIM/mvp/skill        ~/.claude/skills/tinm
ln -s ~/TNIM/mvp/commands/tinm.md  ~/.claude/commands/tinm.md
```

Sanity-check the install before wiring Claude Code:

```bash
~/.tinm/.venv/bin/python -c "import mcp, sentence_transformers; print('OK')"
~/.tinm/.venv/bin/python ~/TNIM/mvp/skill/tinm_paths.py 2>&1 || true   # imports cleanly
```

Don't `tinm init` yet — we want the VPS to pull the existing
`tinm-paper-polish` thread from the Mac via Syncthing in step 4.

---

## 3. Install and pair Syncthing

**Mac**:

```bash
brew install syncthing
brew services start syncthing
open http://127.0.0.1:8384       # GUI to add the share
```

**VPS**:

```bash
sudo apt install -y syncthing
systemctl --user enable --now syncthing
# Tunnel the GUI to your laptop for the one-time setup:
ssh -L 8385:127.0.0.1:8384 user@<vps-tailscale-ip>
# Then open http://127.0.0.1:8385 in your browser.
```

**Pair the two devices** in the Syncthing GUI:

1. Mac GUI → Actions → Show ID → copy.
2. VPS GUI → Add Remote Device → paste the Mac ID. Under
   *Addresses*, force the Tailscale IP (e.g. `tcp://100.x.y.z:22000`)
   instead of the default `dynamic`. This keeps the pairing on the
   private tailnet, not the public internet.
3. Mac GUI → accept the incoming device request from the VPS.

**Create the shared folder** on both sides:

- Folder ID: `tinm-pcp` (must match exactly).
- Folder path Mac: `~/Syncthing/tinm-pcp` (or anywhere — pick once).
- Folder path VPS: `~/Syncthing/tinm-pcp`.
- Share with the paired device.

After ~30s the folders should report "Up to Date" on both sides.

---

## 4. Migrate the Mac's existing PCP store into the synced folder

The Mac currently holds `~/.tinm/threads/` and `~/.tinm/artifacts/`
from Phase 1. Move them into the Syncthing folder, point TINM at the
new location, and let Syncthing propagate to the VPS.

**Mac**:

```bash
# Tell TINM about the new layout. Add to ~/.zshrc so it persists:
echo 'export TINM_PCP_DIR="$HOME/Syncthing/tinm-pcp"' >> ~/.zshrc
source ~/.zshrc

# Move the data using the migration script (idempotent, refuses overwrite):
~/TNIM/mvp/scripts/migrate_to_pcp_subdir.sh
```

Expected output:

```
TINM_HOME    = /Users/user/.tinm
TINM_PCP_DIR = /Users/user/Syncthing/tinm-pcp

  threads:   moved /Users/user/.tinm/threads   -> /Users/user/Syncthing/tinm-pcp/threads
  artifacts: moved /Users/user/.tinm/artifacts -> /Users/user/Syncthing/tinm-pcp/artifacts

OK: current thread 'tinm-paper-polish' resolves at .../threads/tinm-paper-polish.json
```

Watch the Syncthing GUI on the VPS — within ~30s,
`~/Syncthing/tinm-pcp/threads/tinm-paper-polish.json` should appear.

**VPS**: tell its TINM about the same layout:

```bash
echo 'export TINM_PCP_DIR="$HOME/Syncthing/tinm-pcp"' >> ~/.bashrc
source ~/.bashrc
```

The VPS does **not** run the migration script — there is no legacy
data there. It just reads what Syncthing delivered.

---

## 5. Wire Claude Code on the VPS

Same pattern as `mvp/README.md` step 4, with the env vars propagated to
the MCP server entry. Edit `~/.claude.json` on the VPS:

```json
{
  "mcpServers": {
    "tinm": {
      "command": "/home/<user>/.tinm/.venv/bin/python",
      "args": ["/home/<user>/TNIM/mvp/mcp_server/tinm_server.py"],
      "env": {
        "TINM_PCP_DIR": "/home/<user>/Syncthing/tinm-pcp"
      }
    }
  },
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command",
                    "command": "/home/<user>/TNIM/mvp/hooks/session_start.sh" }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command",
                    "command": "/home/<user>/TNIM/mvp/hooks/user_prompt.sh" }] }
    ]
  }
}
```

The `env` field is what makes the env var visible to the MCP child
process — Claude Code does not by default propagate the user shell
environment to MCP servers. Hooks inherit the shell, so the `~/.bashrc`
export from step 4 is enough for them. Restart Claude Code after the
edit.

---

## 6. Verify the end-to-end loop

This is the cross-machine equivalent of the Phase 1 dogfood test from
the previous session.

1. **On the Mac**, in a Claude Code session, type:
   ```
   /tinm load tinm-paper-polish
   ```
   Confirm the trajectory and the `phase2-vps-plan` artifact appear.

2. **On the VPS**, open a fresh Claude Code session (no prior thread
   loaded). The `SessionStart` hook should auto-inject the same
   trajectory + artifacts. The `current_thread` marker is per-host, so
   the first time on the VPS you may need:
   ```
   /tinm load tinm-paper-polish
   ```
   once. After that, Syncthing keeps the PCP store fresh.

3. **From the VPS session**, ask Claude to register a new artifact via
   `tinm.artifact_add`. Then on the Mac, run `/tinm load
   tinm-paper-polish` and confirm the artifact is visible.

4. **Race-free single-writer assumption**: do not run TINM-writing
   actions on both machines literally simultaneously. Single-user
   serial access is the v0.1 contract (`pcp_v0_spec.md` §5). If a
   conflict file appears
   (`*.sync-conflict-YYYYMMDD-HHMMSS-XXXXX.json`), pick a winner by
   hand and delete the loser — TINM does not auto-merge.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| SessionStart hook emits nothing on the VPS | `TINM_PCP_DIR` not exported in the shell that runs Claude Code | Confirm `env \| grep TINM` in a fresh terminal; re-source `~/.bashrc` or set it in `~/.profile`. |
| MCP `current_thread` returns `"none"` on the VPS but the file is present in `$TINM_PCP_DIR/threads/` | `current_thread` is per-host and was never written on the VPS — by design | Run `/tinm load <slug>` once on the VPS. |
| `*.sync-conflict-*.json` files appear in `threads/` | Both hosts wrote the same thread within Syncthing's sync window | Stop one host, manually choose the winner, delete the loser. Consider working on one host at a time. |
| Embedding model mismatch error on `tinm_update.py` | The synced thread file expects `all-MiniLM-L6-v2` but a host has a different model | Confirm `~/.tinm/.venv` on the VPS includes `sentence-transformers` and is using the same default model. The `EXPECTED_MODEL` check is intentional — do not bypass. |
| Tailscale ping drops after VPS reboot | `tailscaled` not enabled to start at boot | `sudo systemctl enable --now tailscaled`. |
| Syncthing stuck "out of sync" | Symlinks inside the shared folder (none expected, but check) | Add a `.stignore` excluding any symlink path. The PCP store is plain JSON only — no symlinks should be there. |

---

## 8. What this runbook does NOT cover

- **Cross-vendor sync** (Claude.ai, ChatGPT) — that is the original
  Phase 2 of the charter, deferred until cross-machine same-vendor
  proves out.
- **Multi-user shared threads** — v0.1 trusts a single user.
- **Encryption at rest on the VPS** — Syncthing-over-Tailscale gives
  encryption-in-transit; the JSON files on disk are not encrypted.
  If the VPS is shared or audited, treat the threads as confidential
  user data and apply disk-level protection separately.
- **Automated failover** if the VPS is unreachable — the Mac keeps
  working from its local copy of `$TINM_PCP_DIR`; the VPS catches up
  whenever Tailscale + Syncthing reconnect.
